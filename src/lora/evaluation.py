from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .schema import CaseDefinition, CaseRunRef, EvaluationResult
from .trace import EventStore


class Evaluator:
    def evaluate(self, case: CaseDefinition, case_run_ref: CaseRunRef) -> EvaluationResult:
        run_dir = Path(case_run_ref.run_dir)
        events = EventStore(case_run_ref).list_by_run()
        failures: list[dict[str, Any]] = []
        errors = [event for event in events if event.type in {"error", "runtime.error"}]
        final_answer = _final_answer(events)
        tool_calls = _tool_call_names(run_dir)

        for expected in case.expect.get("answer", {}).get("contains", []) or []:
            if str(expected) not in final_answer:
                failures.append(
                    {
                        "type": "answer.contains",
                        "message": f"final answer does not contain {expected!r}",
                        "expected": expected,
                    }
                )

        for expected in case.expect.get("tool_calls", {}).get("required", []) or []:
            name = expected.get("name") if isinstance(expected, dict) else None
            if name and name not in tool_calls:
                failures.append(
                    {
                        "type": "tool.required",
                        "message": f"required tool was not called: {name}",
                        "expected": name,
                    }
                )

        failures.extend(_file_failures(case, run_dir))

        max_turns = case.metrics.get("max_turns")
        turn_count = len({event.turn_id for event in events if event.turn_id})
        if max_turns is not None and turn_count > int(max_turns):
            failures.append(
                {
                    "type": "metrics.max_turns",
                    "message": f"turn count {turn_count} exceeded max_turns {max_turns}",
                    "actual": turn_count,
                    "expected": max_turns,
                }
            )

        max_tool_calls = case.metrics.get("max_tool_calls")
        if max_tool_calls is not None and len(tool_calls) > int(max_tool_calls):
            failures.append(
                {
                    "type": "metrics.max_tool_calls",
                    "message": f"tool call count {len(tool_calls)} exceeded max_tool_calls {max_tool_calls}",
                    "actual": len(tool_calls),
                    "expected": max_tool_calls,
                }
            )

        metrics = {
            "event_count": len(events),
            "message_count": len([event for event in events if event.type.startswith("conversation.")]),
            "tool_call_count": len(tool_calls),
            "turn_count": turn_count,
            "final_answer_length": len(final_answer),
        }

        if errors:
            status = "error"
        elif failures:
            status = "failed"
        else:
            status = "passed"

        verdict = {
            "status": status,
            "final_answer": final_answer,
            "failures": failures,
            "errors": [event.payload for event in errors],
        }
        _write_json(run_dir / "metrics.json", metrics)
        _write_json(run_dir / "verdict.json", verdict)
        return EvaluationResult(status=status, metrics=metrics, verdict=verdict)


def _final_answer(events: list[Any]) -> str:
    return "".join(
        str(event.payload.get("content", ""))
        for event in events
        if event.type == "conversation.assistant_message"
    )


def _tool_call_names(run_dir: Path) -> list[str]:
    path = run_dir / "tool_calls.jsonl"
    if not path.exists():
        return []
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("tool_name"):
                names.append(str(row["tool_name"]))
    return names


def _file_failures(case: CaseDefinition, run_dir: Path) -> list[dict[str, Any]]:
    baseline_path = run_dir / "workspace" / "before_hashes.json"
    if not baseline_path.exists():
        return []
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    workspace_root_value = case.workspace.get("root")
    if not workspace_root_value and (run_dir / "run_config.json").exists():
        workspace_root_value = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8")).get("workspace_root")
    workspace_root = Path(workspace_root_value or ".").expanduser().resolve()
    failures: list[dict[str, Any]] = []
    for raw_path in case.expect.get("files", {}).get("unchanged", []) or []:
        current = _file_snapshot((workspace_root / str(raw_path)).resolve())
        expected = baseline.get(str(raw_path))
        if expected is not None and current != expected:
            failures.append(
                {
                    "type": "files.unchanged",
                    "message": f"file changed unexpectedly: {raw_path}",
                    "path": raw_path,
                    "before": expected,
                    "after": current,
                }
            )
    return failures


def _file_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "content_hash": None, "size": None}
    data = path.read_bytes()
    return {"exists": True, "content_hash": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
