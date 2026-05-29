from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .schema import ContextEvent


RootCauseType = Literal[
    "RUNTIME_ERROR",
    "TOOL_EXECUTION_ERROR",
    "MISSING_TOOL_CALL",
    "ANSWER_ASSERTION_FAILED",
    "UNEXPECTED_FILE_MUTATION",
    "BUDGET_EXCEEDED",
    "ASSERTION_FAILED",
    "UNKNOWN_FAILURE",
]


@dataclass(slots=True)
class RootCause:
    type: RootCauseType
    summary: str
    evidence: list[str] = field(default_factory=list)
    suspected_modules: list[str] = field(default_factory=list)
    recommended_tests: list[dict[str, Any]] = field(default_factory=list)
    suspected_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["suspected_files"]:
            data.pop("suspected_files")
        return data


@dataclass(slots=True)
class AnalysisResult:
    status: str
    root_causes: list[RootCause] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root_causes": [root_cause.to_dict() for root_cause in self.root_causes],
        }


class FailureAnalyzer:
    def analyze(
        self,
        *,
        verdict: dict[str, Any],
        events: list[ContextEvent],
        run_dir: Path,
    ) -> AnalysisResult:
        status = str(verdict.get("status") or "error")
        if status == "passed":
            return AnalysisResult(status=status, root_causes=[])

        root_causes: list[RootCause] = []
        root_causes.extend(self._runtime_errors(verdict, events))
        root_causes.extend(self._tool_errors(events))
        root_causes.extend(self._failure_causes(verdict))

        if not root_causes:
            root_causes.append(
                RootCause(
                    type="UNKNOWN_FAILURE",
                    summary="Run did not pass, but no specific failure rule matched.",
                    evidence=self._generic_evidence(verdict, run_dir),
                    suspected_modules=["runtime", "evaluation"],
                    recommended_tests=[
                        {
                            "kind": "scenario",
                            "description": "Replay the failing case and add a deterministic assertion for the observed failure.",
                        }
                    ],
                )
            )
        return AnalysisResult(status=status, root_causes=root_causes)

    def _runtime_errors(self, verdict: dict[str, Any], events: list[ContextEvent]) -> list[RootCause]:
        error_events = [event for event in events if event.type in {"runtime.error", "error"}]
        verdict_errors = [item for item in verdict.get("errors") or [] if isinstance(item, dict)]
        if not error_events and not verdict_errors:
            return []
        evidence = [_event_evidence(event) for event in error_events]
        evidence.extend(_json_evidence(item) for item in verdict_errors)
        return [
            RootCause(
                type="RUNTIME_ERROR",
                summary="Run produced a runtime error event.",
                evidence=_dedupe(evidence),
                suspected_modules=["runtime"],
                recommended_tests=[
                    {
                        "kind": "scenario",
                        "description": "Replay the failing case and assert the runtime error is preserved in trace evidence.",
                    }
                ],
            )
        ]

    def _tool_errors(self, events: list[ContextEvent]) -> list[RootCause]:
        failures = [
            event
            for event in events
            if event.type == "tool.result" and str(event.payload.get("status", "")).lower() == "error"
        ]
        if not failures:
            return []
        return [
            RootCause(
                type="TOOL_EXECUTION_ERROR",
                summary="A tool call returned an error result.",
                evidence=[_event_evidence(event) for event in failures],
                suspected_modules=["tools", "runtime"],
                recommended_tests=[
                    {
                        "kind": "unit",
                        "description": "Cover the failing tool result path and verify error payloads are recorded.",
                    }
                ],
            )
        ]

    def _failure_causes(self, verdict: dict[str, Any]) -> list[RootCause]:
        failures = [item for item in verdict.get("failures") or [] if isinstance(item, dict)]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for failure in failures:
            grouped.setdefault(str(failure.get("type") or "assertion"), []).append(failure)

        root_causes: list[RootCause] = []
        if grouped.get("tool.required"):
            root_causes.append(self._missing_tool_call(grouped["tool.required"]))
        if grouped.get("answer.contains"):
            root_causes.append(self._answer_assertion(grouped["answer.contains"]))
        if grouped.get("files.unchanged"):
            root_causes.append(self._file_mutation(grouped["files.unchanged"]))

        budget_failures = []
        budget_failures.extend(grouped.get("metrics.max_turns") or [])
        budget_failures.extend(grouped.get("metrics.max_tool_calls") or [])
        if budget_failures:
            root_causes.append(self._budget_exceeded(budget_failures))

        known = {"tool.required", "answer.contains", "files.unchanged", "metrics.max_turns", "metrics.max_tool_calls"}
        other_failures = [failure for failure in failures if str(failure.get("type") or "assertion") not in known]
        if other_failures:
            root_causes.append(
                RootCause(
                    type="ASSERTION_FAILED",
                    summary="Run output did not satisfy deterministic case assertions.",
                    evidence=[_failure_evidence(failure) for failure in other_failures],
                    suspected_modules=["evaluation", "runtime"],
                    recommended_tests=[
                        {"kind": "unit", "description": "Cover the failed assertion in Evaluator."}
                    ],
                )
            )
        return root_causes

    @staticmethod
    def _missing_tool_call(failures: list[dict[str, Any]]) -> RootCause:
        expected = [str(item.get("expected")) for item in failures if item.get("expected") is not None]
        return RootCause(
            type="MISSING_TOOL_CALL",
            summary="The case required a tool call that did not occur.",
            evidence=[_failure_evidence(failure) for failure in failures],
            suspected_modules=["runtime", "tools"],
            recommended_tests=[
                {
                    "kind": "scenario",
                    "description": f"Run a case that requires tool calls and assert the call sequence includes {', '.join(expected) or 'the required tool'}.",
                }
            ],
        )

    @staticmethod
    def _answer_assertion(failures: list[dict[str, Any]]) -> RootCause:
        return RootCause(
            type="ANSWER_ASSERTION_FAILED",
            summary="The final answer did not satisfy expected answer assertions.",
            evidence=[_failure_evidence(failure) for failure in failures],
            suspected_modules=["evaluation", "runtime"],
            recommended_tests=[
                {"kind": "unit", "description": "Cover answer.contains verdict mapping in FailureAnalyzer."}
            ],
        )

    @staticmethod
    def _file_mutation(failures: list[dict[str, Any]]) -> RootCause:
        paths = [str(item.get("path")) for item in failures if item.get("path") is not None]
        return RootCause(
            type="UNEXPECTED_FILE_MUTATION",
            summary="A file expected to remain unchanged was modified.",
            evidence=[_failure_evidence(failure) for failure in failures],
            suspected_modules=["tools", "workspace", "evaluation"],
            recommended_tests=[
                {"kind": "unit", "description": "Cover files.unchanged failure analysis with changed file evidence."}
            ],
            suspected_files=_dedupe(paths),
        )

    @staticmethod
    def _budget_exceeded(failures: list[dict[str, Any]]) -> RootCause:
        return RootCause(
            type="BUDGET_EXCEEDED",
            summary="The run exceeded a configured turn or tool-call budget.",
            evidence=[_failure_evidence(failure) for failure in failures],
            suspected_modules=["runtime", "evaluation"],
            recommended_tests=[
                {"kind": "unit", "description": "Cover metrics budget verdict mapping in FailureAnalyzer."}
            ],
        )

    @staticmethod
    def _generic_evidence(verdict: dict[str, Any], run_dir: Path) -> list[str]:
        evidence = [_json_evidence(verdict)] if verdict else []
        verdict_path = run_dir / "verdict.json"
        if verdict_path.exists():
            evidence.append(f"verdict_path={verdict_path}")
        return evidence or ["verdict is empty"]


def _event_evidence(event: ContextEvent) -> str:
    return f"{event.type}: {_json_evidence(event.payload)}"


def _failure_evidence(failure: dict[str, Any]) -> str:
    message = failure.get("message")
    if message is not None:
        return str(message)
    return _json_evidence(failure)


def _json_evidence(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
