from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pygent import Message as PygentMessage
from pygent.runtime.codec import context_from_dict, message_from_dict

from lora.runtime.context import LORA_CONTEXT_CODECS, LoraContext

DEFAULT_REACT_MAX_STEPS = 500


def _to_pygent_message(message: dict[str, Any]) -> PygentMessage | None:
    if message.get("role") == "system":
        return None
    wire_message = dict(message)
    if wire_message.get("role") == "assistant":
        # Pygent 0.3 makes usage part of the authoritative AIMessage wire
        # contract. Lora sessions created by older releases legitimately lack
        # it, so normalize them at the storage boundary instead of weakening
        # Pygent's codec.
        wire_message.setdefault("usage", {})
    return message_from_dict(wire_message)


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


def _initial_lora_context(
    *,
    context: LoraContext,
    history: list[dict[str, Any]],
    checkpoint: object | None = None,
) -> tuple[LoraContext, bool]:
    if context.eternal_memory_enabled:
        if context.memory_covered_through > 0:
            # A published snapshot and its uncovered tail are delivered together
            # through ReplaceMessageProjection before inference starts.
            return context, False
        # The extractor runs asynchronously. Until it publishes a snapshot,
        # authoritative history must remain visible, even if a prior checkpoint
        # came from an empty eternal projection.
        messages = tuple(
            converted
            for item in history
            if (converted := _to_pygent_message(item)) is not None
        )
        return replace(context, messages=messages), False
    if (
        checkpoint is not None
        and (
            not isinstance(checkpoint, Mapping)
            or checkpoint.get("schema") != LoraContext.context_schema
            or checkpoint.get("version") != LoraContext.context_schema_version
        )
    ):
        # Pygent 0.3.3 replaced full_history with bounded native commits and
        # changed the base Agent context schema. Rebuild older checkpoints
        # from SessionManager's authoritative history.
        checkpoint = None
    if checkpoint is not None:
        restored = context_from_dict(checkpoint, registry=LORA_CONTEXT_CODECS)
        if not isinstance(restored, LoraContext):
            raise TypeError("session agent context is not a LoraContext")
        return (
            replace(
                context,
                messages=restored.messages,
                compression_count=restored.compression_count,
                input_token_scale_ppm=restored.input_token_scale_ppm,
                last_input_tokens=restored.last_input_tokens,
                projection_revision=restored.projection_revision,
            ),
            True,
        )

    converted_messages: list[PygentMessage] = []
    for message in history:
        converted = _to_pygent_message(message)
        if converted is not None:
            converted_messages.append(converted)
    return replace(context, messages=tuple(converted_messages)), False


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
