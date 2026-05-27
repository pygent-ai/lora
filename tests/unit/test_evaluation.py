from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.case import CaseManager
from lora.evaluation import Evaluator
from lora.schema import CaseDefinition, RunConfig
from lora.session import SessionManager
from lora.trace import EventStore


class EvaluatorTests(unittest.TestCase):
    def test_passes_answer_tool_and_file_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("stable", encoding="utf-8")
            config = RunConfig(workspace_root=root, lora_root=root / ".lora")
            manager = SessionManager(config)
            session = manager.create("case-a")
            run = manager.start_case_run(session.session_id, "case-a", run_config=config)
            case = CaseDefinition.from_dict(
                {
                    "id": "case-a",
                    "workspace": {"root": str(root)},
                    "input": {"content": "hello"},
                    "expect": {
                        "answer": {"contains": ["done"]},
                        "tool_calls": {"required": [{"name": "read_text_file"}]},
                        "files": {"unchanged": ["README.md"]},
                    },
                }
            )
            CaseManager(root).prepare_workspace(case, run)
            store = EventStore(run)
            store.append("tool.call", actor="assistant", payload={"tool_name": "read_text_file"}, turn_id="turn-0001")
            store.append(
                "conversation.assistant_message",
                actor="assistant",
                payload={"role": "assistant", "content": "done"},
                turn_id="turn-0001",
            )

            result = Evaluator().evaluate(case, run)

            self.assertEqual(result.status, "passed")
            self.assertTrue((Path(run.run_dir) / "metrics.json").exists())
            self.assertTrue((Path(run.run_dir) / "verdict.json").exists())

    def test_fails_missing_answer_and_required_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            session = manager.create("case-a")
            run = manager.start_case_run(session.session_id, "case-a")
            EventStore(run).append(
                "conversation.assistant_message",
                actor="assistant",
                payload={"role": "assistant", "content": "nope"},
                turn_id="turn-0001",
            )
            case = CaseDefinition.from_dict(
                {
                    "id": "case-a",
                    "input": {"content": "hello"},
                    "expect": {
                        "answer": {"contains": ["done"]},
                        "tool_calls": {"required": [{"name": "read_text_file"}]},
                    },
                }
            )

            result = Evaluator().evaluate(case, run)

            self.assertEqual(result.status, "failed")
            self.assertEqual({failure["type"] for failure in result.verdict["failures"]}, {"answer.contains", "tool.required"})


if __name__ == "__main__":
    unittest.main()
