from __future__ import annotations

import asyncio
import subprocess
import threading

import pytest

from lora.runtime.reminders import BootstrapStatus, ReminderSection, ReminderService
from lora.runtime.reminders.git_context import adaptive_check_interval
from lora.runtime.reminders.rendering import render_context_body
from lora.schema import RunConfig
from lora.sessions import SessionManager


def _create(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    config = RunConfig(workspace_root=tmp_path, lora_root=tmp_path / ".lora")
    manager = SessionManager(config)
    session = manager.create("chat", mode="chat")
    return config, session


def test_renderer_orders_sources_in_one_envelope() -> None:
    reminder = render_context_body(
        [
            ReminderSection("git.context", 30, ("<git-context />",)),
            ReminderSection("cli.context", 10, ("<cli-context />",), True),
            ReminderSection("skills.context", 20, ("<skills-context />",)),
        ]
    )
    assert reminder is not None
    assert "<system-reminder>" not in reminder
    assert (
        reminder.index("<cli-context")
        < reminder.index("<skills-context")
        < reminder.index("<git-context")
    )


@pytest.mark.asyncio
async def test_first_turn_waits_for_complete_snapshot_and_consumes_once(
    tmp_path, monkeypatch
) -> None:
    # Exercise delivery, not the production best-effort Git timeout.
    monkeypatch.setattr("lora.runtime.reminders.git_context.GIT_STATUS_TIMEOUT_SECONDS", 5.0)
    config, session = _create(tmp_path)
    service = ReminderService(config)
    gate = asyncio.Event()
    original_prepare = service._prepare

    async def delayed_prepare(scope):
        await gate.wait()
        await original_prepare(scope)

    monkeypatch.setattr(service, "_prepare", delayed_prepare)
    service.prewarm_session(session.session_id)
    claim = asyncio.create_task(service.claim_initial(session.session_id, "turn-1"))
    await asyncio.sleep(0)
    assert not claim.done()
    gate.set()
    content = await claim
    assert content is not None
    assert "<runtime-context>" in content
    assert 'reason="session-baseline"' in content
    assert "<system-reminder>" not in content
    await service.acknowledge_initial(session.session_id, "turn-1", "execution-1")
    assert await service.claim_initial(session.session_id, "turn-2") is None
    assert (
        service.store.load_bootstrap(service.scope(session.session_id))["status"]
        == BootstrapStatus.CONSUMED.value
    )
    await service.close()


@pytest.mark.asyncio
async def test_git_observation_reports_only_changed_item(tmp_path, monkeypatch) -> None:
    config, session = _create(tmp_path)
    service = ReminderService(config)
    initial = await service.claim_initial(session.session_id, "turn-1")
    assert initial is not None
    await service.acknowledge_initial(session.session_id, "turn-1", "execution-1")
    (tmp_path / "changed.txt").write_text("changed", encoding="utf-8")
    monkeypatch.setattr(
        "lora.runtime.reminders.git_context.adaptive_check_interval",
        lambda _state: 0.0,
    )
    immediate = await service.observe_after_tools(
        session.session_id,
        bash_commands=(),
        file_mutation=True,
        has_results=True,
    )
    assert immediate is None
    await service._observations[session.session_id]
    delta = await service.collect_pending(session.session_id)
    assert delta is not None
    assert 'mode="delta"' in delta
    assert 'path="changed.txt"' in delta
    await service.close()


def test_git_adaptive_interval_caps_amortized_cost() -> None:
    assert adaptive_check_interval({"last_check_duration_ms": 25}) == 10.0
    assert adaptive_check_interval({"last_check_duration_ms": 1000}) == 20.0
    assert adaptive_check_interval({"last_check_duration_ms": 5000}) == 60.0


@pytest.mark.asyncio
async def test_tool_boundary_does_not_wait_for_git_observation(
    tmp_path, monkeypatch
) -> None:
    config, session = _create(tmp_path)
    service = ReminderService(config)
    await service.claim_initial(session.session_id, "turn-1")
    await service.acknowledge_initial(session.session_id, "turn-1", "execution-1")
    started = threading.Event()
    release = threading.Event()

    def slow_git(_scope):
        started.set()
        release.wait()
        return None

    monkeypatch.setattr(service, "_observe_git_sync", slow_git)
    result = await asyncio.wait_for(
        service.observe_after_tools(
            session.session_id,
            bash_commands=(),
            file_mutation=False,
            has_results=True,
        ),
        timeout=0.1,
    )
    assert result is None
    await asyncio.to_thread(started.wait, 1)
    release.set()
    await service._observations[session.session_id]
    await service.close()
