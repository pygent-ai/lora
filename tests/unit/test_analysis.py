from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.analysis import FailureAnalyzer
from lora.schema import ContextEvent


class FailureAnalyzerTests(unittest.TestCase):
    def test_passed_verdict_has_no_root_causes(self) -> None:
        result = FailureAnalyzer().analyze(verdict={"status": "passed"}, events=[], run_dir=Path("."))

        self.assertEqual(result.to_dict(), {"status": "passed", "root_causes": []})

    def test_empty_verdict_returns_unknown_failure(self) -> None:
        result = FailureAnalyzer().analyze(verdict={}, events=[], run_dir=Path(".")).to_dict()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["root_causes"][0]["type"], "UNKNOWN_FAILURE")
        self.assertIn("verdict is empty", result["root_causes"][0]["evidence"])

    def test_runtime_error_maps_to_runtime_error(self) -> None:
        event = _event("runtime.error", {"error": "boom", "error_type": "RuntimeError"})

        result = FailureAnalyzer().analyze(
            verdict={"status": "error", "errors": [{"error": "boom"}]},
            events=[event],
            run_dir=Path("."),
        ).to_dict()

        self.assertEqual(result["root_causes"][0]["type"], "RUNTIME_ERROR")
        self.assertIn("runtime.error", result["root_causes"][0]["evidence"][0])
        self.assertIn("boom", result["root_causes"][0]["evidence"][0])

    def test_tool_result_error_maps_to_tool_execution_error_with_payload_evidence(self) -> None:
        tool_error = _event(
            "tool.result",
            {"tool_call_id": "call-1", "status": "error", "result": {"error": "permission denied"}},
            actor="tool",
        )

        result = FailureAnalyzer().analyze(
            verdict={"status": "failed", "failures": [], "errors": []},
            events=[tool_error],
            run_dir=Path("."),
        ).to_dict()

        self.assertEqual(result["root_causes"][0]["type"], "TOOL_EXECUTION_ERROR")
        self.assertIn("permission denied", result["root_causes"][0]["evidence"][0])
        self.assertIn("tool.result", result["root_causes"][0]["evidence"][0])

    def test_assertion_failures_map_to_specific_root_cause_types(self) -> None:
        verdict = {
            "status": "failed",
            "failures": [
                {"type": "tool.required", "message": "required tool was not called: read", "expected": "read"},
                {"type": "answer.contains", "message": "final answer does not contain 'done'", "expected": "done"},
                {"type": "metrics.max_turns", "message": "turn count 3 exceeded max_turns 2"},
            ],
            "errors": [],
        }

        result = FailureAnalyzer().analyze(verdict=verdict, events=[], run_dir=Path(".")).to_dict()

        self.assertEqual(
            [item["type"] for item in result["root_causes"]],
            ["MISSING_TOOL_CALL", "ANSWER_ASSERTION_FAILED", "BUDGET_EXCEEDED"],
        )

    def test_file_mutation_evidence_includes_changed_path(self) -> None:
        verdict = {
            "status": "failed",
            "failures": [
                {
                    "type": "files.unchanged",
                    "message": "file changed unexpectedly: README.md",
                    "path": "README.md",
                    "before": {"content_hash": "old"},
                    "after": {"content_hash": "new"},
                }
            ],
            "errors": [],
        }

        result = FailureAnalyzer().analyze(verdict=verdict, events=[], run_dir=Path(".")).to_dict()

        root_cause = result["root_causes"][0]
        self.assertEqual(root_cause["type"], "UNEXPECTED_FILE_MUTATION")
        self.assertIn("README.md", root_cause["evidence"][0])
        self.assertEqual(root_cause["suspected_files"], ["README.md"])

    def test_unknown_failure_uses_verdict_path_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "verdict.json").write_text("{}", encoding="utf-8")

            result = FailureAnalyzer().analyze(
                verdict={"status": "failed", "failures": [], "errors": []},
                events=[],
                run_dir=run_dir,
            ).to_dict()

            self.assertEqual(result["root_causes"][0]["type"], "UNKNOWN_FAILURE")
            self.assertIn("verdict_path=", result["root_causes"][0]["evidence"][-1])


def _event(event_type: str, payload: dict[str, object], actor: str = "system") -> ContextEvent:
    return ContextEvent(
        id=f"evt_{event_type.replace('.', '_')}",
        session_id="s1",
        case_id="c1",
        case_run_id="r1",
        turn_id="turn-0001",
        type=event_type,
        timestamp="2026-05-29T00:00:00+00:00",
        actor=actor,  # type: ignore[arg-type]
        payload=payload,
    )


if __name__ == "__main__":
    unittest.main()
