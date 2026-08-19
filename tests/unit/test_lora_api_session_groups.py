from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from lora.core.io import read_json, write_json
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora.tracing.events import EventStore
from lora_api.dependencies import ApiContext
from lora_api.services.session_service import SessionService, _first_user_message_from_events


def test_session_groups_are_partitioned_by_remembered_project(tmp_path: Path) -> None:
    from lora_api.routers.sessions import list_session_groups

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _create_titled_chat(project_a, "Project A chat")
    _create_titled_chat(project_b, "Project B chat")

    context = ApiContext(workspace_root=str(project_a), state_path=str(tmp_path / "state.json"))
    context.remember_project(project_a)
    context.remember_project(project_b)

    response = list_session_groups(context=context)

    groups = {group.scope.scope_id: group for group in response.groups}
    scope_a = f"project:{project_a.resolve()}"
    scope_b = f"project:{project_b.resolve()}"
    assert response.active_scope_id == scope_a
    assert groups[scope_a].scope.label == "project-a"
    assert groups[scope_b].scope.label == "project-b"
    assert groups["conversation"].scope.label == "Chat"
    assert [record.title for record in groups[scope_a].sessions] == ["Project A chat"]
    assert [record.title for record in groups[scope_b].sessions] == ["Project B chat"]


def test_update_settings_remembers_switched_workspace_for_project_list(tmp_path: Path) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.projects import list_projects
    from lora_api.routers.settings import update_settings

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    context = ApiContext(workspace_root=str(project_a), state_path=str(tmp_path / "state.json"))
    context.remember_project(project_a)

    asyncio.run(update_settings(UpdateSettingsRequest(workspace_root=str(project_b), max_steps=-1), context=context))

    response = list_projects(context=context)

    assert response.active.scope_id == f"project:{project_b.resolve()}"
    assert [project.scope_id for project in response.projects] == [
        f"project:{project_b.resolve()}",
        f"project:{project_a.resolve()}",
    ]


def test_project_list_omits_missing_recent_directories(tmp_path: Path) -> None:
    from lora_api.routers.projects import list_projects

    project = tmp_path / "project"
    missing = tmp_path / "missing"
    project.mkdir()
    context = ApiContext(workspace_root=str(project), state_path=str(tmp_path / "state.json"))
    context.project_state.recent_project_paths = [str(missing), str(project)]
    context.project_state.default_project_path = str(missing)
    context.project_state.save()

    response = list_projects(context=context)

    assert [item.scope_id for item in response.projects] == [f"project:{project.resolve()}"]


def test_session_groups_ignore_project_lora_yaml(tmp_path: Path) -> None:
    from lora_api.routers.sessions import list_session_groups

    active_project = tmp_path / "active-project"
    invalid_project = tmp_path / "invalid-project"
    _create_titled_chat(active_project, "Active chat")
    invalid_project.mkdir()
    (invalid_project / "lora.yaml").write_text(
        "\n".join(
            [
                "agents:",
                "  - alias: dev",
                "    model_request:",
                "      routes:",
                "        - id: primary",
                "          provider: openai",
                "          model_name: test-model",
                "          base_url: https://example.test",
                "          api_key_env: TEST_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context = ApiContext(workspace_root=str(active_project), state_path=str(tmp_path / "state.json"))
    context.remember_project(invalid_project)
    context.remember_project(active_project)

    response = list_session_groups(context=context)

    assert [group.scope.scope_id for group in response.groups] == [
        f"project:{active_project.resolve()}",
        f"project:{invalid_project.resolve()}",
        "conversation",
    ]


def test_session_list_uses_first_user_message_as_title_when_metadata_title_is_missing(tmp_path: Path) -> None:
    manager = SessionManager(
        RunConfig(
            workspace_root=str(tmp_path),
            lora_root=str((tmp_path / ".lora").resolve()),
        )
    )
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "  Build a LoRA training script\nwith resume support  "},
    ]
    manager.save(session)

    records = SessionService(manager).list_chat_sessions()

    assert records[0].title == "Build a LoRA training script with resume support"


def test_title_lookup_stops_after_first_historical_user_message(tmp_path: Path) -> None:
    first = tmp_path / "cases" / "chat" / "runs" / "run-001" / "events.jsonl"
    later = tmp_path / "cases" / "chat" / "runs" / "run-002" / "events.jsonl"
    first.parent.mkdir(parents=True)
    later.parent.mkdir(parents=True)
    first.touch()
    later.touch()

    def iter_events(path: Path):
        if path == later:
            raise AssertionError("title lookup scanned a later run after finding the title")
        return iter(
            [
                {
                    "type": "conversation.user_message",
                    "payload": {"raw_content": "First historical message"},
                }
            ]
        )

    with patch.object(EventStore, "iter_jsonl", side_effect=iter_events) as mocked:
        assert _first_user_message_from_events(tmp_path) == "First historical message"

    mocked.assert_called_once_with(first)


def _create_titled_chat(workspace_root: Path, title: str) -> str:
    workspace_root.mkdir(parents=True, exist_ok=True)
    manager = SessionManager(
        RunConfig(
            workspace_root=str(workspace_root),
            lora_root=str((workspace_root / ".lora").resolve()),
        )
    )
    ref = manager.create("chat", mode="chat")
    metadata_path = Path(ref.session_dir) / "metadata.json"
    metadata = read_json(metadata_path)
    metadata["title"] = title
    write_json(metadata_path, metadata)
    return ref.session_id
