from pathlib import Path

import pytest

from lora.config import load_run_config
from lora.core.paths import project_lora_root
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora_api.services.project_state import GuiProjectState, build_session_scopes
from lora_api.services.session_service import SessionService


def test_user_storage_isolates_projects_and_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    user_root = home / ".lora"
    user_root.mkdir(parents=True)
    example = Path(__file__).resolve().parents[2] / "user-config.yaml.example"
    (user_root / "config.yaml").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    projects = [tmp_path / "a" / "project", tmp_path / "b" / "project"]
    managers = []
    for project in projects:
        project.mkdir(parents=True)
        config = load_run_config(workspace_root=project)
        assert config.lora_root == RunConfig(workspace_root=str(project)).lora_root
        manager = SessionManager(config)
        ref = manager.create("chat", mode="chat")
        assert manager.load(ref.session_id).workspace_root == str(project.resolve())
        assert Path(ref.session_dir).is_relative_to(home / ".lora" / "projects")
        assert Path(config.runtime_durability.history_path).parent == Path(config.lora_root) / "runtime"
        assert Path(config.runtime_capacity.coordinator_path).parent == Path(config.lora_root) / "runtime"
        assert not (project / ".lora").exists()
        managers.append(manager)

    assert managers[0].sessions_root != managers[1].sessions_root
    first_id = SessionService(managers[0]).list_chat_sessions()[0].session_id
    with pytest.raises(FileNotFoundError):
        managers[1].load(first_id)
    assert not SessionService(managers[1]).delete_session(first_id)
    assert managers[0].load(first_id)
    assert project_lora_root(projects[0] / ".." / "project") == Path(managers[0].config.lora_root)

    state = GuiProjectState(tmp_path / "state.json", recent_project_paths=[str(p) for p in projects])
    scopes = build_session_scopes(state)
    assert [scope.lora_root for scope in scopes[:2]] == [manager.config.lora_root for manager in managers]
    chat = scopes[-1]
    assert Path(chat.lora_root) == home / ".lora" / "conversations"
    chat_manager = SessionManager(RunConfig(workspace_root=chat.runtime_workspace_root, lora_root=chat.lora_root))
    assert SessionService(chat_manager).list_chat_sessions() == []
    chat_manager.create("chat", mode="chat")
    assert len(SessionService(chat_manager).list_chat_sessions()) == 1
    assert all(len(SessionService(manager).list_chat_sessions()) == 1 for manager in managers)
