from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lora.schema import RunConfig, SessionSpec
from lora.sessions import SessionManager


class SessionManagerTests(unittest.TestCase):
    def test_create_and_load_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora"))
            ref = manager.create("read-file-basic")
            loaded = manager.load(ref.session_id)

            self.assertEqual(loaded.session_id, ref.session_id)
            self.assertTrue((Path(ref.session_dir) / "metadata.json").exists())
            self.assertFalse((Path(tmp) / "sessions").exists())

    def test_multiple_runs_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            session = manager.create("case-a")
            first = manager.start_case_run(session.session_id, "case-a")
            second = manager.start_case_run(session.session_id, "case-a")

            self.assertNotEqual(first.case_run_id, second.case_run_id)
            self.assertTrue((Path(first.run_dir) / "run_config.json").exists())
            self.assertTrue((Path(second.run_dir) / "run_config.json").exists())

    def test_finish_case_run_updates_metadata_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora"))
            session = manager.create("case-a")
            run = manager.start_case_run(session.session_id, "case-a")
            manager.finish_case_run(run, "passed")

            metadata = json.loads((Path(run.run_dir) / "run_metadata.json").read_text(encoding="utf-8"))
            session_metadata = json.loads((Path(session.session_dir) / "metadata.json").read_text(encoding="utf-8"))
            loaded = manager.load(session.session_id)
            self.assertEqual(metadata["status"], "passed")
            self.assertEqual(session_metadata["last_case_run_id"], run.case_run_id)
            self.assertEqual(session_metadata["last_case_run_status"], "passed")
            self.assertEqual(loaded.metadata["last_case_run_id"], run.case_run_id)

    def test_find_case_run_returns_ref_from_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora"))
            session = manager.create("case-a")
            run = manager.start_case_run(session.session_id, "case-a")

            found = manager.find_case_run(session.session_id, run.case_run_id)

            self.assertEqual(found, run)

    def test_save_redacts_secrets_from_session_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora"))
            ref = manager.create("case-a")
            session = manager.load(ref.session_id)
            secret = "sk-1234567890abcdef1234567890abcdef"
            session.history = [
                {
                    "role": "tool",
                    "content": json.dumps(
                        {"status": "success", "result": {"content": f"DEEPSEEK_API_KEY={secret}"}},
                    ),
                }
            ]

            manager.save(session)

            lora_session_text = (Path(ref.session_dir) / "session.json").read_text(encoding="utf-8")
            self.assertNotIn(secret, lora_session_text)
            self.assertIn("DEEPSEEK_API_KEY=[REDACTED]", lora_session_text)
            self.assertFalse((Path(tmp) / "sessions").exists())

    def test_load_or_create_resume_requires_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora"))
            with self.assertRaises(ValueError):
                manager.load_or_create(SessionSpec(case_id="case-a", mode="resume"))

    def test_fork_copies_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora"))
            source = manager.create("case-a")
            forked = manager.fork(source.session_id)
            loaded = manager.load(forked.session_id)

            self.assertEqual(loaded.metadata["forked_from"], source.session_id)
            self.assertNotEqual(forked.session_id, source.session_id)


if __name__ == "__main__":
    unittest.main()
