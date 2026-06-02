from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.config import load_run_config
from lora.session import SessionManager

from gui.session_model import ChatSessionRecord, ChatSessionStore


class GuiSessionModelTests(unittest.TestCase):
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
