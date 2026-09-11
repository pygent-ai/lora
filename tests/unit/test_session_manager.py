from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lora.schema import (
    ModelRouteConfig,
    ResolvedAgentConfig,
    RunConfig,
    SessionSpec,
)
from lora.sessions import SessionManager


class SessionManagerTests(unittest.TestCase):
    def test_list_sessions_filters_mode_and_orders_latest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                RunConfig(workspace_root=tmp, lora_root=str(Path(tmp) / ".lora"))
            )
            chat = manager.create("chat", mode="chat")
            agent = manager.create("collaboration", mode="agent")
            manager.save_title_from_user_input(agent.session_id, "Agent task")

            all_sessions = manager.list_sessions()
            agent_sessions = manager.list_sessions(mode="agent")

            self.assertEqual(
                {record["session_id"] for record in all_sessions},
                {chat.session_id, agent.session_id},
            )
            self.assertEqual(
                [record["session_id"] for record in agent_sessions],
                [agent.session_id],
            )
            self.assertEqual(agent_sessions[0]["title"], "Agent task")

    def test_create_and_load_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            )
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

    def test_load_run_config_rehydrates_credentials_without_persisting_them(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret = "sk-runtime-secret"
            route = ModelRouteConfig(
                id="primary",
                provider="openai",
                model_name="model",
                base_url="https://example.invalid",
                api_key_env="MODEL_API_KEY",
                api_key=secret,
                api_key_source="config",
            )
            config = RunConfig(
                workspace_root=tmp,
                lora_root=Path(tmp) / ".lora",
                max_steps=7,
                resolved_agent=ResolvedAgentConfig(alias="default", routes=(route,)),
            )
            manager = SessionManager(config)
            session = manager.create("case-a")
            run = manager.start_case_run(session.session_id, "case-a")

            persisted = (Path(run.run_dir) / "run_config.json").read_text(
                encoding="utf-8"
            )
            restored = manager.load_run_config(run, credential_source=config)

            self.assertNotIn(secret, persisted)
            self.assertEqual(restored.max_steps, 7)
            self.assertEqual(restored.resolved_agent.routes[0].api_key, secret)

    def test_finish_case_run_updates_metadata_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            )
            session = manager.create("case-a")
            run = manager.start_case_run(session.session_id, "case-a")
            manager.finish_case_run(run, "passed")

            metadata = json.loads(
                (Path(run.run_dir) / "run_metadata.json").read_text(encoding="utf-8")
            )
            session_metadata = json.loads(
                (Path(session.session_dir) / "metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            loaded = manager.load(session.session_id)
            self.assertEqual(metadata["status"], "passed")
            self.assertEqual(session_metadata["last_case_run_id"], run.case_run_id)
            self.assertEqual(session_metadata["last_case_run_status"], "passed")
            self.assertEqual(loaded.metadata["last_case_run_id"], run.case_run_id)

    def test_find_case_run_returns_ref_from_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            )
            session = manager.create("case-a")
            run = manager.start_case_run(session.session_id, "case-a")

            found = manager.find_case_run(session.session_id, run.case_run_id)

            self.assertEqual(found, run)

    def test_save_redacts_secrets_from_model_visible_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            )
            ref = manager.create("case-a")
            session = manager.load(ref.session_id)
            secret = "sk-1234567890abcdef1234567890abcdef"
            session.history = [
                {
                    "role": "tool",
                    "content": json.dumps(
                        {
                            "status": "success",
                            "result": {"content": f"DEEPSEEK_API_KEY={secret}"},
                        },
                    ),
                }
            ]

            manager.save(session)

            lora_session_text = (Path(ref.session_dir) / "session.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(secret, lora_session_text)
            self.assertIn("DEEPSEEK_API_KEY=[REDACTED]", lora_session_text)
            self.assertFalse((Path(tmp) / "sessions").exists())

    def test_load_or_create_resume_requires_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            )
            with self.assertRaises(ValueError):
                manager.load_or_create(SessionSpec(case_id="case-a", mode="resume"))

    def test_fork_copies_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            )
            source = manager.create("case-a")
            source_dir = Path(source.session_dir)
            inherited = {
                "memory/memory.sqlite3": "formal-memory",
                "raw-history/events.jsonl": "raw-evidence",
                "agent-history/extractor/conversation.jsonl": "extractor-history",
            }
            for name, content in inherited.items():
                path = source_dir / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            forked = manager.fork(source.session_id)
            loaded = manager.load(forked.session_id)
            fork_metadata = json.loads(
                (Path(forked.session_dir) / "metadata.json").read_text(encoding="utf-8")
            )

            self.assertEqual(loaded.metadata["forked_from"], source.session_id)
            self.assertEqual(loaded.metadata["mode"], "fork")
            self.assertEqual(loaded.created_at, fork_metadata["created_at"])
            self.assertNotEqual(forked.session_id, source.session_id)
            for name, content in inherited.items():
                self.assertEqual(
                    (Path(forked.session_dir) / name).read_text(encoding="utf-8"),
                    content,
                )


if __name__ == "__main__":
    unittest.main()


def test_running_session_exposes_current_run_and_recovery_checkpoint(tmp_path):
    from lora.core.io import read_json, write_json
    from lora_api.services.session_service import SessionService

    manager = SessionManager(
        RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / "data"))
    )
    session = manager.create("chat", mode="chat")
    previous = manager.start_case_run(session.session_id, "chat")
    manager.append_history_checkpoint(
        previous,
        turn_id="turn-previous",
        checkpoint_id="previous-user",
        message={"role": "user", "content": "previous"},
    )
    manager.append_history_checkpoint(
        previous,
        turn_id="turn-previous",
        checkpoint_id="previous-assistant",
        message={"role": "assistant", "content": "done", "usage": {}},
    )
    manager.finish_case_run(previous, "passed")
    run = manager.start_case_run(session.session_id, "chat")
    path = Path(run.run_dir) / "run_metadata.json"
    metadata = read_json(path)
    metadata["runtime_execution_id"] = "execution-to-resume"
    write_json(path, metadata)
    detail = SessionService(manager).load_detail(session.session_id)
    assert detail.session.last_case_run_id == run.case_run_id
    assert detail.session.last_case_run_status == "running"
    assert detail.runtime_execution_id == "execution-to-resume"
    assert detail.run_history_start_index == 2
    manager.finish_case_run(run, "passed")
    assert (
        SessionService(manager).load_detail(session.session_id).runtime_execution_id
        is None
    )
