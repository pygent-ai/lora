from __future__ import annotations

from pathlib import Path

from lora.core.io import read_json, write_json
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora_api.dependencies import ApiContext
from lora_api.services.session_service import SessionService


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

    update_settings(UpdateSettingsRequest(workspace_root=str(project_b), max_steps=-1), context=context)

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
