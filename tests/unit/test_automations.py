from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from lora.automations import (
    AUTOMATION_MESSAGE_KIND,
    AutomationScheduler,
    AutomationStore,
    automation_trigger_message,
)
from lora.automations.models import next_occurrence
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora_api.app import create_app


def _future(minutes: int = 10) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def test_store_creates_one_time_task_and_claims_manual_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutomationStore(tmp_path / "automations.sqlite3")
    task = store.create(
        name="daily check",
        prompt="Inspect the project",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )

    requested = store.request_run(task.automation_id)
    claimed = store.claim_ready_runs(owner="test")

    assert [item.run_id for item in claimed] == [requested.run_id]
    assert claimed[0].status == "running"
    completed = store.finish_run(requested.run_id, status="passed", final_answer="ok")
    assert completed.final_answer == "ok"


def test_claim_skips_second_manual_run_for_same_active_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutomationStore(tmp_path / "automations.sqlite3")
    task = store.create(
        name="check",
        prompt="Inspect",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )
    first = store.request_run(task.automation_id)
    second = store.request_run(task.automation_id)

    claimed = store.claim_ready_runs(owner="test", limit=4)

    assert [item.run_id for item in claimed] == [first.run_id]
    assert store.get_run(second.run_id).status == "skipped"


def test_rrule_uses_named_timezone_across_dst() -> None:
    result = next_occurrence(
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        timezone_name="America/New_York",
        dtstart="2026-03-01T14:00:00+00:00",
        after="2026-03-08T13:30:00+00:00",
    )

    assert result == "2026-03-09T13:00:00+00:00"


def test_periodic_task_catches_up_only_once_after_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutomationStore(tmp_path / "automations.sqlite3")
    task = store.create(
        name="hourly",
        prompt="Inspect",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        rrule="FREQ=HOURLY",
    )
    missed = (datetime.now(UTC) - timedelta(hours=12)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE automations SET next_run_at=? WHERE automation_id=?",
            (missed, task.automation_id),
        )
        connection.commit()

    [run] = store.claim_ready_runs(owner="first-start")
    assert run.scheduled_for == missed
    assert store.get(task.automation_id).next_run_at > datetime.now(UTC).isoformat()
    assert store.claim_ready_runs(owner="duplicate-wakeup") == []


def test_due_one_time_task_becomes_completed_when_claimed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutomationStore(tmp_path / "automations.sqlite3")
    task = store.create(
        name="once",
        prompt="Inspect",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )

    assert len(store.claim_ready_runs(owner="test")) == 1
    assert store.get(task.automation_id).status == "completed"
    assert store.get(task.automation_id).next_run_at is None


def test_automation_message_is_user_role_with_application_origin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutomationStore(tmp_path / "automations.sqlite3")
    task = store.create(
        name="检查 <main>",
        prompt="Inspect <unsafe>",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )
    run = store.request_run(task.automation_id)

    message = automation_trigger_message(task, run)

    assert message.role == "user"
    assert message.kind == AUTOMATION_MESSAGE_KIND
    assert message.data["origin"] == "automation"
    assert "<automation-trigger" in message.content
    assert "Inspect &lt;unsafe&gt;" in message.content


def test_heartbeat_requires_existing_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from lora.automations.service import AutomationService

    service = AutomationService(AutomationStore(tmp_path / "automations.sqlite3"))
    with pytest.raises(FileNotFoundError):
        service.create(
            name="follow up",
            prompt="Continue",
            workspace_root=str(workspace),
            destination="heartbeat",
            target_session_id="missing",
            timezone_name="UTC",
            at_time=_future(),
        )


@pytest.mark.asyncio
async def test_scheduler_submits_unattended_standalone_trigger(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = RunConfig(
        workspace_root=str(workspace), lora_root=str(workspace / ".lora")
    )
    store = AutomationStore(tmp_path / "automations.sqlite3")
    automation = store.create(
        name="nightly",
        prompt="Inspect the build",
        workspace_root=str(workspace),
        destination="standalone",
        timezone_name="UTC",
        at_time=_future(),
    )
    requested = store.request_run(automation.automation_id)
    [run] = store.claim_ready_runs(owner="test")
    commands = []

    class Lease:
        runtime = SimpleNamespace(
            reminders=SimpleNamespace(prewarm_session=lambda _session_id: None)
        )

        async def release(self) -> None:
            return None

    class Turn:
        run_ref = SimpleNamespace(case_run_id="case-run-1")
        execution_handle = SimpleNamespace(execution_id="execution-1")

        async def wait_ready(self) -> None:
            return None

        async def result(self):
            return (
                SimpleNamespace(
                    data={"result": {"status": "passed", "final_answer": "done"}}
                ),
                SimpleNamespace(),
            )

    class Coordinator:
        async def submit_turn(self, *, lease, command):
            commands.append(command)
            return Turn()

    async def acquire_runtime(**_kwargs):
        return Lease()

    scheduler = AutomationScheduler(
        store=store,
        coordinator=cast(Any, Coordinator()),
        acquire_runtime=cast(Any, acquire_runtime),
        config_factory=lambda _workspace: config,
    )
    await scheduler._execute(run)

    command = commands[0]
    assert command.submission_id == requested.run_id
    assert command.message_kind == AUTOMATION_MESSAGE_KIND
    assert command.message_data["origin"] == "automation"
    assert command.interactive_approvals is False
    assert command.session_title == "定时任务：nightly"
    assert store.get_run(run.run_id).status == "passed"
    assert SessionManager(config).show(command.session_id)["metadata"]["title"] == (
        "定时任务：nightly"
    )


@pytest.mark.asyncio
async def test_automation_api_crud_and_manual_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(workspace_root=str(workspace))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/automations",
            json={
                "name": "daily",
                "prompt": "Inspect",
                "workspace_root": str(workspace),
                "destination": "standalone",
                "timezone": "UTC",
                "rrule": "FREQ=DAILY;BYHOUR=9",
            },
        )
        assert response.status_code == 201
        automation_id = response.json()["automation_id"]
        assert (await client.post(f"/automations/{automation_id}/pause")).json()[
            "status"
        ] == "paused"
        assert (await client.post(f"/automations/{automation_id}/resume")).json()[
            "status"
        ] == "active"
        run = (await client.post(f"/automations/{automation_id}/run")).json()
        assert run["status"] == "queued"
        runs = (await client.get(f"/automations/{automation_id}/runs")).json()
        assert runs["runs"][0]["run_id"] == run["run_id"]
