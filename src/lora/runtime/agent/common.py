from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pygent import Context as PygentContext
from pygent import Message as PygentMessage
from pygent.runtime.codec import message_from_dict, message_to_dict

from lora.runtime.context import LoraContext
from lora.runtime.context_compression import load_model_context

def _to_pygent_message(message: dict[str, Any]) -> PygentMessage | None:
    if message.get("role") == "system":
        return None
    return message_from_dict(message)


def _message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    return getattr(content, "data", content) or ""

def _serialize_tool_payload_for_model(payload: dict[str, Any]) -> str:
    ordered = {
        "status": payload.get("status"),
        "result": payload.get("result"),
        "error": payload.get("error"),
        "tool_call_id": payload.get("tool_call_id"),
    }
    for key, value in payload.items():
        if key not in ordered:
            ordered[key] = value
    return json.dumps(ordered, ensure_ascii=False)


def _initial_lora_context(*, context: LoraContext, session_dir: Path) -> tuple[LoraContext, bool]:
    state = load_model_context(session_dir)
    if state and state.get("is_compacted") is True:
        messages = [
            item
            for item in state.get("messages", [])
            if isinstance(item, dict) and item.get("role") in {"system", "user", "assistant", "tool"}
        ]
        history_cutoff = int(state.get("history_cutoff") or len(context.history))
        compacted_context = _pygent_context_from_model_messages(
            context,
            [
                {"role": str(item.get("role")), "content": str(item.get("content") or "")}
                for item in messages
            ]
        )
        for message in context.history[history_cutoff:]:
            converted = _to_pygent_message(message)
            if converted is not None:
                compacted_context = compacted_context + converted
        return replace(compacted_context, model_context_compacted=True), True

    converted_messages: list[PygentMessage] = []
    for message in context.history:
        converted = _to_pygent_message(message)
        if converted is not None:
            converted_messages.append(converted)
    return replace(context, messages=tuple(converted_messages)), False


def _pygent_context_from_model_messages(
    context: LoraContext,
    messages: list[dict[str, str]],
) -> LoraContext:
    system_prompt = next(
        (str(message.get("content") or "") for message in messages if message.get("role") == "system"),
        "",
    )
    converted = tuple(
        item
        for message in messages
        if message.get("role") != "system"
        and (item := _to_pygent_message(message)) is not None
    )
    return replace(context, system_prompt=system_prompt, messages=converted)


def _pygent_context_messages(context: PygentContext) -> list[dict[str, Any]]:
    messages = [message_to_dict(message) for message in context.messages]
    if context.system_prompt:
        messages.insert(0, {"role": "system", "content": context.system_prompt})
    return messages


def _split_current_message(context: PygentContext) -> tuple[PygentContext, PygentMessage]:
    if not context.messages:
        raise RuntimeError("model context does not contain a current message")
    return replace(context, messages=context.messages[:-1]), context.messages[-1]


def _latest_user_input_hash(history: list[dict[str, Any]]) -> str | None:
    for message in reversed(history):
        if message.get("role") == "user":
            return _hash_text(str(message.get("content", "")))
    return None


def _hash_json(data: Any) -> str:
    return _hash_text(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str))


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _file_lock(path: Path, timeout: float = 5.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for lock: {path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _session_dir_for_run(run_dir: Path) -> Path:
    for parent in [run_dir, *run_dir.parents]:
        if (parent / "session.json").exists():
            return parent
    raise ValueError(f"Cannot find session root for run directory: {run_dir}")
