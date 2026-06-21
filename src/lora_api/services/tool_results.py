from __future__ import annotations

from pathlib import Path
from typing import Any

from lora.tracing import EventStore

from lora_api.dependencies import ApiContext


def find_tool_result(context: ApiContext, tool_call_id: str) -> dict[str, Any] | None:
    call_id = tool_call_id.strip()
    if not call_id:
        return None

    sessions_root = context.manager.sessions_root
    if not sessions_root.exists():
        return None

    for result_path in _tool_result_paths(sessions_root):
        trace_call_id = _trace_call_id_for(result_path, call_id)
        for row in EventStore.iter_jsonl(result_path) or []:
            row_call_id = str(row.get("tool_call_id") or "")
            row_model_call_id = str(row.get("model_tool_call_id") or "")
            if call_id not in {row_call_id, row_model_call_id, trace_call_id}:
                continue
            tool_name = _find_tool_name(result_path, call_id)
            result = row.get("result")
            error = row.get("error")
            return {
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "status": str(row.get("status") or ("error" if error else "success")),
                "result": result,
                "error": None if error is None else str(error),
                "result_size": _serialized_size(result if error is None else error),
                "created_at": row.get("created_at"),
            }
    return None


def _tool_result_paths(sessions_root: Path) -> list[Path]:
    run_paths = list(sessions_root.glob("*/cases/*/runs/*/tool_results.jsonl"))
    session_paths = list(sessions_root.glob("*/logs/tool_results.jsonl"))
    return [*run_paths, *session_paths]


def _find_tool_name(result_path: Path, tool_call_id: str) -> str:
    candidates = [
        result_path.with_name("tool_calls.jsonl"),
        result_path.parent.parent / "logs" / "tool_calls.jsonl",
    ]
    for path in candidates:
        for row in EventStore.iter_jsonl(path) or []:
            if tool_call_id in {
                str(row.get("event_id") or ""),
                str(row.get("tool_call_id") or ""),
                str(row.get("model_tool_call_id") or ""),
            }:
                return str(row.get("tool_name") or "tool")
    return "tool"


def _trace_call_id_for(result_path: Path, tool_call_id: str) -> str:
    candidates = [
        result_path.with_name("tool_calls.jsonl"),
        result_path.parent.parent / "logs" / "tool_calls.jsonl",
    ]
    for path in candidates:
        for row in EventStore.iter_jsonl(path) or []:
            if tool_call_id in {
                str(row.get("event_id") or ""),
                str(row.get("tool_call_id") or ""),
                str(row.get("model_tool_call_id") or ""),
            }:
                return str(row.get("event_id") or row.get("tool_call_id") or "")
    return ""


def _serialized_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        text = value
    else:
        import json

        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return len(text.encode("utf-8"))
