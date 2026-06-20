from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.config import load_run_config
from lora.sessions import SessionManager
from lora.tracing import EventStore

from gui.session_model import (
    ChatSessionRecord,
    ChatSessionStore,
    GuiProjectState,
    SessionGroupRecord,
    build_session_scopes,
)


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

            self.assertEqual([scope.scope_id for scope in scopes], [f"project:{project.resolve()}", "conversation"])
            self.assertEqual(scopes[0].label, "repo")
            self.assertIsNone(scopes[1].workspace_root)
            self.assertEqual(scopes[1].label, "对话")
            self.assertEqual(scopes[0].workspace_root, str(project.resolve()))

    def test_chat_sessions_are_isolated_by_scope_lora_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            state = GuiProjectState.load(state_path)
            state.remember_project(project)
            conversation_scope = next(scope for scope in build_session_scopes(state) if scope.scope_id == "conversation")
            project_scope = next(scope for scope in build_session_scopes(state) if scope.scope_id.startswith("project:"))

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

    def test_list_chat_sessions_uses_first_user_question_as_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            EventStore(run).append(
                "conversation.user_message",
                actor="user",
                payload={
                    "role": "user",
                    "content": "<user-context><user-message>wrapped</user-message></user-context>",
                    "raw_content": "帮我写一个 LoRA 训练脚本\n要求支持断点续训",
                },
                turn_id="turn-0001",
            )

            records = ChatSessionStore(manager).list_chat_sessions()

            self.assertEqual(records[0].title, "帮我写一个 LoRA 训练脚本 要求支持断点续训")

    def test_save_chat_session_title_from_user_input_persists_single_line_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_run_config(workspace_root=tmp)
            manager = SessionManager(config)
            record = ChatSessionStore(manager).create_chat_session()

            ChatSessionStore(manager).save_user_title(record.session_id, "  第一行问题\r\n第二行问题  ")

            records = ChatSessionStore(manager).list_chat_sessions()
            self.assertEqual(records[0].title, "第一行问题 第二行问题")

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

    def test_project_state_saves_scope_order_and_collapsed_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = str((Path(tmp) / "repo").resolve())
            state = GuiProjectState.load(state_path)

            state.remember_scope_order([f"project:{project}", "conversation"])
            state.set_scope_collapsed("conversation", True)
            state.set_scope_collapsed(f"project:{project}", False)
            restored = GuiProjectState.load(state_path)

            self.assertEqual(restored.scope_order, [f"project:{project}", "conversation"])
            self.assertEqual(restored.collapsed_scope_ids, ["conversation"])

    def test_build_session_scopes_applies_persisted_scope_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            state = GuiProjectState.load(state_path)
            state.remember_project(first)
            state.remember_project(second)
            state.remember_scope_order([f"project:{first.resolve()}", "conversation"])

            scopes = build_session_scopes(state)

            self.assertEqual(
                [scope.scope_id for scope in scopes],
                [f"project:{first.resolve()}", "conversation", f"project:{second.resolve()}"],
            )

    def test_build_session_scopes_ignores_stale_scope_order_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            state = GuiProjectState.load(state_path)
            state.remember_project(project)
            state.remember_scope_order(["project:E:/missing", "conversation"])

            scopes = build_session_scopes(state)

            self.assertEqual([scope.scope_id for scope in scopes], ["conversation", f"project:{project.resolve()}"])

    def test_session_group_record_carries_scope_records_and_collapsed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = GuiProjectState.load(Path(tmp) / "state.json")
            scope = build_session_scopes(state)[0]
            record = ChatSessionRecord(
                session_id="chat-one",
                session_dir="sessions/chat-one",
                case_id="chat",
                mode="chat",
                created_at="1",
                updated_at="2",
                title="Chat one",
            )

            group = SessionGroupRecord(scope=scope, records=[record], collapsed=True)

            self.assertEqual(group.scope.scope_id, "conversation")
            self.assertEqual(group.records, [record])
            self.assertTrue(group.collapsed)

    def test_forget_project_removes_from_recent_scope_order_and_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            state = GuiProjectState.load(state_path)
            state.remember_project(first)
            state.remember_project(second)
            state.remember_scope_order([f"project:{first.resolve()}", "conversation"])
            state.set_scope_collapsed(f"project:{first.resolve()}", True)

            state.forget_project(first)

            self.assertEqual(state.recent_project_paths, [str(second.resolve())])
            self.assertEqual(state.default_project_path, str(second.resolve()))
            self.assertEqual(state.scope_order, ["conversation"])
            self.assertEqual(state.collapsed_scope_ids, [])


if __name__ == "__main__":
    unittest.main()
