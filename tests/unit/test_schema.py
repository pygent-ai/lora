from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.schema import CaseDefinition, CaseRunRef, CaseRunResult, ContextEvent, ResolvedAgentConfig, RunConfig, SessionRef


class SchemaTests(unittest.TestCase):
    def test_run_config_normalizes_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=".lora", case_file="case.yaml")

        self.assertTrue(Path(config.workspace_root).is_absolute())
        self.assertTrue(Path(config.lora_root).is_absolute())
        self.assertTrue(Path(config.case_file or "").is_absolute())

    def test_run_config_round_trip_keeps_safe_agent_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(
                workspace_root=tmp,
                lora_root=Path(tmp) / ".lora",
                agent_alias="dev",
                model_name="profile-model",
                api_key_source="env:DEV_API_KEY",
                base_url="https://api.example/v1",
                resolved_agent=ResolvedAgentConfig(
                    alias="dev",
                    model_name="profile-model",
                    api_key="raw-secret",
                    api_key_source="env:DEV_API_KEY",
                    base_url="https://api.example/v1",
                ),
            )

            data = config.to_dict()
            restored = RunConfig.from_dict(data)

        self.assertEqual(data["agent_alias"], "dev")
        self.assertEqual(data["model_name"], "profile-model")
        self.assertEqual(data["api_key_source"], "env:DEV_API_KEY")
        self.assertNotIn("resolved_agent", data)
        self.assertNotIn("raw-secret", str(data))
        self.assertEqual(restored.agent_alias, "dev")

    def test_refs_round_trip(self) -> None:
        session = SessionRef(session_id="s1", session_dir=".", workspace_root=".")
        run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=".")

        self.assertEqual(SessionRef.from_dict(session.to_dict()).session_id, "s1")
        self.assertEqual(CaseRunRef.from_dict(run.to_dict()).case_run_id, "r1")

    def test_event_validates_actor_and_payload(self) -> None:
        event = ContextEvent(
            id="evt_1",
            session_id="s1",
            case_id="c1",
            case_run_id="r1",
            turn_id=None,
            type="case.started",
            timestamp="2026-05-27T00:00:00Z",
            actor="system",
            payload={"ok": True},
        )
        self.assertEqual(event.to_dict()["payload"], {"ok": True})

        with self.assertRaises(ValueError):
            ContextEvent(
                id="evt_2",
                session_id="s1",
                case_id=None,
                case_run_id=None,
                turn_id=None,
                type="bad",
                timestamp="2026-05-27T00:00:00Z",
                actor="nobody",  # type: ignore[arg-type]
                payload={},
            )

    def test_case_definition_defaults_optional_sections(self) -> None:
        case = CaseDefinition.from_dict({"id": "read-file-basic"})
        self.assertEqual(case.title, "read-file-basic")
        self.assertEqual(case.type, "e2e")
        self.assertEqual(case.metrics, {})

    def test_case_run_result_round_trip(self) -> None:
        result = CaseRunResult(session_id="s1", case_id="c1", case_run_id="r1", status="passed")
        self.assertEqual(CaseRunResult.from_dict(result.to_dict()).status, "passed")


if __name__ == "__main__":
    unittest.main()
