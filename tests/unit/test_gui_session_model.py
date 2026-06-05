from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.config import load_run_config
from lora.session import SessionManager

from gui.session_model import ChatSessionRecord, ChatSessionStore, GuiProjectState, build_session_scopes


class GuiSessionModelTests(unittest.TestCase):
    def test_project_state_remembers_default_and_recent_project_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"

            state = GuiProjectState.load(state_path)
            state.remember_project(first)
            state.remember_project(second)
            restored = GuiProjectState.load(state_path)

            self.assertEqual(restored.default_project_path, str(second.resolve()))
            self.assertEqual(restored.recent_project_paths, [str(second.resolve()), str(first.resolve())])

    def test_build_session_scopes_includes_conversation_and_recent_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            state = GuiProjectState.load(state_path)
            state.remember_project(project)

            scopes = build_session_scopes(state)

            self.assertEqual([scope.scope_id for scope in scopes], ["conversation", f"project:{project.resolve()}"])
            self.assertEqual(scopes[0].label, "对话")
            self.assertIsNone(scopes[0].workspace_root)
            self.assertEqual(scopes[1].label, "repo")
            self.assertEqual(scopes[1].workspace_root, str(project.resolve()))

    def test_chat_sessions_are_isolated_by_scope_lora_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            state = GuiProjectState.load(state_path)
            state.remember_project(project)
            conversation_scope, project_scope = build_session_scopes(state)

            conversation_config = conversation_scope.to_run_config()
            project_config = project_scope.to_run_config()
            conversation_record = ChatSessionStore(SessionManager(conversation_config)).create_chat_session()
            project_record = ChatSessionStore(SessionManager(project_config)).create_chat_session()

            self.assertNotEqual(Path(conversation_record.session_dir).parents, Path(project_record.session_dir).parents)
            self.assertIn(".lora", conversation_record.session_dir)
            self.assertTrue(str(project.resolve()) in project_record.session_dir)

    def test_list_chat_sessions_returns_only_chat_mode_sorted_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)
            manager = SessionManager(config)
            older = manager.create("chat", mode="chat").session_id
            manager.create("demo", mode="e2e")
            newer = manager.create("chat", mode="chat").session_id

            records = ChatSessionStore(manager).list_chat_sessions()

            self.assertEqual([record.session_id for record in records], [newer, older])
            self.assertTrue(all(isinstance(record, ChatSessionRecord) for record in records))
            self.assertTrue(all(record.mode == "chat" for record in records))

    def test_create_chat_session_uses_chat_case_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)
            manager = SessionManager(config)

            record = ChatSessionStore(manager).create_chat_session()

            self.assertEqual(record.case_id, "chat")
            self.assertEqual(record.mode, "chat")
            self.assertTrue(Path(record.session_dir).exists())

    def test_delete_chat_session_removes_lora_and_workspace_session_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)
            manager = SessionManager(config)
            record = ChatSessionStore(manager).create_chat_session()
            mirror = Path(tmp) / "sessions" / record.session_id
            (mirror / "session.json").write_text("{}", encoding="utf-8")

            deleted = ChatSessionStore(manager).delete_chat_session(record.session_id)

            self.assertTrue(deleted)
            self.assertFalse(Path(record.session_dir).exists())
            self.assertFalse(mirror.exists())


if __name__ == "__main__":
    unittest.main()
