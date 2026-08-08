from __future__ import annotations

import hashlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pygent import ToolResult as PygentToolResult, thaw_json

from lora.core.io import plain_data
from lora.tracing import DiffRecorder, read_snapshot_content
from lora.core.redaction import redact_secrets
from lora.tracing import EventStore
from .file_effects import DeferredFileEffectJob

MAX_BASH_RESULT_CHARS = 20_000
MAX_BASH_RESULT_LINES = 200
BASH_RESULT_PREVIEW_LINES = 120
BASH_RESULT_PREVIEW_CHARS = 12_000
SPOOLED_TEXT_TOOL_NAMES = frozenset({"bash", "grep"})


@dataclass(slots=True)
class FileSnapshot:
    path: str
    exists: bool
    kind: Literal["file", "dir", "other", "missing"]
    size: int | None = None
    mtime_ns: int | None = None
    content_hash: str | None = None
    content: str | None = None
    content_available: bool = False
    content_unavailable_reason: str | None = None


@dataclass(slots=True)
class FileEffect:
    type: Literal["file.read", "file.write", "file.edit", "file.delete"]
    path: str
    tool_call_id: str
    tool_name: str
    detected_by: list[Literal["tool_args", "snapshot_diff", "bash_command_parse"]]
    confidence: Literal["declared", "observed", "inferred"]
    before_hash: str | None = None
    after_hash: str | None = None
    before_exists: bool | None = None
    after_exists: bool | None = None
    before_content: str | None = None
    after_content: str | None = None
    before_content_available: bool = False
    after_content_available: bool = False
    before_content_unavailable_reason: str | None = None
    after_content_unavailable_reason: str | None = None

    def key(self) -> tuple[Any, ...]:
        return (
            self.tool_call_id,
            self.path,
            self.type,
            self.before_hash,
            self.after_hash,
            self.before_exists,
            self.after_exists,
        )


class FileEffectTracker:
    IGNORED_DIRS = frozenset(
        {".git", ".lora", ".venv", "__pycache__", ".pytest_cache", "sessions"}
    )
    PATH_ARG_NAMES = ("file_path",)
    WRITE_TYPES = frozenset({"file.write", "file.edit", "file.delete"})

    def __init__(
        self,
        workspace_root: str | Path,
        store: EventStore,
        *,
        allow_read_outside_workspace: bool = True,
    ):
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.store = store
        self.allow_read_outside_workspace = allow_read_outside_workspace
        self._appended_keys: set[tuple[Any, ...]] = set()

    def snapshot_workspace(self) -> dict[str, FileSnapshot]:
        snapshots: dict[str, FileSnapshot] = {}
        if not self.workspace_root.exists():
            return snapshots
        for path in sorted(self.workspace_root.rglob("*"), key=lambda item: str(item)):
            if self._is_ignored(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            normalized = str(path.resolve())
            try:
                content_hash = _hash_file(path)
            except OSError:
                continue
            content = read_snapshot_content(path)
            snapshots[normalized] = FileSnapshot(
                path=normalized,
                exists=True,
                kind="file",
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash=content_hash,
                content=content.content,
                content_available=content.available,
                content_unavailable_reason=content.reason,
            )
        return snapshots

    def declared_effects(
        self,
        tool_name: str,
        args: dict[str, Any],
        tool_call_id: str,
    ) -> list[FileEffect]:
        if tool_name == "bash":
            return self._bash_read_effects(args, tool_call_id)
        path = self._path_from_args(args, allow_outside_workspace=tool_name == "read")
        if path is None:
            return []
        if tool_name == "read":
            return [
                FileEffect(
                    type="file.read",
                    path=path,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    detected_by=["tool_args"],
                    confidence="declared",
                )
            ]
        if tool_name == "write":
            return [
                FileEffect(
                    type="file.write",
                    path=path,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    detected_by=["tool_args"],
                    confidence="declared",
                )
            ]
        if tool_name == "edit":
            return [
                FileEffect(
                    type="file.edit",
                    path=path,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    detected_by=["tool_args"],
                    confidence="declared",
                )
            ]
        return []

    def observed_effects(
        self,
        before: dict[str, FileSnapshot],
        after: dict[str, FileSnapshot],
        *,
        tool_name: str,
        tool_call_id: str,
    ) -> list[FileEffect]:
        effects: list[FileEffect] = []
        for path in sorted(set(before) | set(after)):
            before_snapshot = before.get(path)
            after_snapshot = after.get(path)
            if before_snapshot is None and after_snapshot is not None:
                effects.append(
                    FileEffect(
                        type="file.write",
                        path=path,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        detected_by=["snapshot_diff"],
                        confidence="observed",
                        after_hash=after_snapshot.content_hash,
                        before_exists=False,
                        after_exists=True,
                        after_content=after_snapshot.content,
                        after_content_available=after_snapshot.content_available,
                        after_content_unavailable_reason=after_snapshot.content_unavailable_reason,
                    )
                )
            elif before_snapshot is not None and after_snapshot is None:
                effects.append(
                    FileEffect(
                        type="file.delete",
                        path=path,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        detected_by=["snapshot_diff"],
                        confidence="observed",
                        before_hash=before_snapshot.content_hash,
                        before_exists=True,
                        after_exists=False,
                        before_content=before_snapshot.content,
                        before_content_available=before_snapshot.content_available,
                        before_content_unavailable_reason=before_snapshot.content_unavailable_reason,
                    )
                )
            elif (
                before_snapshot is not None
                and after_snapshot is not None
                and before_snapshot.content_hash != after_snapshot.content_hash
            ):
                effects.append(
                    FileEffect(
                        type="file.edit",
                        path=path,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        detected_by=["snapshot_diff"],
                        confidence="observed",
                        before_hash=before_snapshot.content_hash,
                        after_hash=after_snapshot.content_hash,
                        before_exists=True,
                        after_exists=True,
                        before_content=before_snapshot.content,
                        after_content=after_snapshot.content,
                        before_content_available=before_snapshot.content_available,
                        after_content_available=after_snapshot.content_available,
                        before_content_unavailable_reason=before_snapshot.content_unavailable_reason,
                        after_content_unavailable_reason=after_snapshot.content_unavailable_reason,
                    )
                )
        return sorted(
            effects, key=lambda effect: (_effect_type_order(effect.type), effect.path)
        )

    def merge_effects(
        self,
        declared: list[FileEffect],
        observed: list[FileEffect],
    ) -> list[FileEffect]:
        observed_by_path = {effect.path: effect for effect in observed}
        declared_by_path: dict[str, FileEffect] = {}
        for effect in declared:
            declared_by_path.setdefault(effect.path, effect)

        merged: list[FileEffect] = []
        for path in sorted(set(declared_by_path) | set(observed_by_path)):
            declared_effect = declared_by_path.get(path)
            observed_effect = observed_by_path.get(path)
            if observed_effect is None:
                if declared_effect is not None:
                    merged.append(declared_effect)
                continue

            if declared_effect is None:
                merged.append(observed_effect)
                continue

            detected_by = _merge_detected_by(
                declared_effect.detected_by, observed_effect.detected_by
            )
            merged.append(
                FileEffect(
                    type=observed_effect.type,
                    path=observed_effect.path,
                    tool_call_id=observed_effect.tool_call_id,
                    tool_name=observed_effect.tool_name,
                    detected_by=detected_by,
                    confidence=observed_effect.confidence,
                    before_hash=observed_effect.before_hash,
                    after_hash=observed_effect.after_hash,
                    before_exists=observed_effect.before_exists,
                    after_exists=observed_effect.after_exists,
                    before_content=observed_effect.before_content,
                    after_content=observed_effect.after_content,
                    before_content_available=observed_effect.before_content_available,
                    after_content_available=observed_effect.after_content_available,
                    before_content_unavailable_reason=observed_effect.before_content_unavailable_reason,
                    after_content_unavailable_reason=observed_effect.after_content_unavailable_reason,
                )
            )

        write_seen: set[str] = set()
        deduped: list[FileEffect] = []
        for effect in merged:
            if effect.type in self.WRITE_TYPES:
                if effect.path in write_seen:
                    continue
                write_seen.add(effect.path)
            deduped.append(effect)
        return sorted(
            deduped, key=lambda effect: (_effect_type_order(effect.type), effect.path)
        )

    def append_effects(
        self,
        effects: list[FileEffect],
        *,
        turn_id: str | None,
    ) -> None:
        diff_recorder = DiffRecorder(self.store, self.workspace_root)
        for effect in effects:
            key = effect.key()
            if key in self._appended_keys:
                continue
            self._appended_keys.add(key)
            self.store.append(
                effect.type,
                actor="tool",
                payload={
                    "path": effect.path,
                    "tool_call_id": effect.tool_call_id,
                    "tool_name": effect.tool_name,
                    "detected_by": effect.detected_by,
                    "confidence": effect.confidence,
                    "before_exists": effect.before_exists,
                    "after_exists": effect.after_exists,
                    "before_hash": effect.before_hash,
                    "after_hash": effect.after_hash,
                    "content_hash": effect.after_hash or effect.before_hash,
                },
                turn_id=turn_id,
            )
            diff_recorder.record_effect(effect, turn_id=turn_id)

    def _path_from_args(
        self, args: dict[str, Any], *, allow_outside_workspace: bool = False
    ) -> str | None:
        for name in self.PATH_ARG_NAMES:
            value = args.get(name)
            if isinstance(value, str) and value:
                return self._resolve_workspace_path(
                    value, allow_outside_workspace=allow_outside_workspace
                )
        return None

    def _resolve_workspace_path(
        self, path: str | Path, *, allow_outside_workspace: bool = False
    ) -> str:
        candidate = Path(_normalize_user_path(path)).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, self.workspace_root) and not (
            allow_outside_workspace and self.allow_read_outside_workspace
        ):
            raise ValueError(f"Path is outside workspace: {path}")
        return str(resolved)

    def _is_ignored(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.workspace_root)
        except ValueError:
            return True
        return any(part in self.IGNORED_DIRS for part in relative.parts)

    def _bash_read_effects(
        self, args: dict[str, Any], tool_call_id: str
    ) -> list[FileEffect]:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return []
        paths = self._parse_bash_read_paths(command)
        effects: list[FileEffect] = []
        seen: set[str] = set()
        for raw_path in paths:
            path = self._resolve_workspace_path(raw_path, allow_outside_workspace=True)
            if path in seen:
                continue
            seen.add(path)
            effects.append(
                FileEffect(
                    type="file.read",
                    path=path,
                    tool_call_id=tool_call_id,
                    tool_name="bash",
                    detected_by=["bash_command_parse"],
                    confidence="inferred",
                )
            )
        return effects

    @staticmethod
    def _parse_bash_read_paths(command: str) -> list[str]:
        paths: list[str] = []
        separators = ("&&", "||", ";", "|")
        normalized = command
        for separator in separators:
            normalized = normalized.replace(separator, "\n")
        for line in normalized.splitlines():
            try:
                tokens = shlex.split(line, posix=True)
            except ValueError:
                try:
                    tokens = shlex.split(line, posix=False)
                except ValueError:
                    continue
            if not tokens:
                continue
            executable = Path(tokens[0]).name.lower()
            if executable in {"cat", "type"}:
                paths.extend(_non_option_tokens(tokens[1:]))
            elif executable == "grep":
                candidates = _non_option_tokens(tokens[1:])
                if len(candidates) >= 2:
                    paths.extend(candidates[1:])
            elif executable in {"get-content", "gc"}:
                paths.extend(_powershell_path_args(tokens[1:]))
        return paths


class ToolObserver:
    def __init__(
        self,
        store: EventStore,
        *,
        workspace_root: str | Path | None = None,
        track_file_effects: bool = False,
        defer_file_effects: bool = False,
        allow_read_outside_workspace: bool = True,
        bash_full_output_allowlist: list[str] | None = None,
    ):
        self.store = store
        self.bash_full_output_allowlist = list(bash_full_output_allowlist or [])
        self.defer_file_effects = defer_file_effects
        self._file_effect_jobs: list[DeferredFileEffectJob] = []
        if track_file_effects and workspace_root is None:
            raise ValueError("workspace_root is required when track_file_effects=True")
        self.file_effect_tracker = (
            FileEffectTracker(
                workspace_root=workspace_root,
                store=store,
                allow_read_outside_workspace=allow_read_outside_workspace,
            )
            if track_file_effects and workspace_root is not None
            else None
        )

    def drain_file_effect_jobs(self) -> list[DeferredFileEffectJob]:
        jobs = list(self._file_effect_jobs)
        self._file_effect_jobs.clear()
        return jobs

    def record_framework_result(
        self,
        name: str,
        args: dict[str, Any],
        turn_id: str | None,
        result: PygentToolResult,
        *,
        available_tools: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Persist Lora audit facts after Pygent has executed a tool."""

        call_id = self.store.append(
            "tool.call",
            actor="assistant",
            payload={
                "tool_name": name,
                "args": args,
                "model_tool_call_id": result.call_id,
            },
            turn_id=turn_id,
        )
        declared = (
            self.file_effect_tracker.declared_effects(name, args, call_id)
            if self.file_effect_tracker is not None
            else []
        )
        if self.defer_file_effects:
            self._append_deferred_file_effect_job(
                tool_call_id=call_id,
                tool_name=name,
                args=args,
                turn_id=turn_id,
                declared=declared,
                include_declared=result.status == "succeeded",
            )

        if result.status == "succeeded":
            value = plain_data(thaw_json(result.output))
            value = _spool_large_tool_result(
                tool_name=name,
                args=args,
                result=value,
                tool_call_id=call_id,
                run_dir=self.store.run_dir,
                bash_full_output_allowlist=self.bash_full_output_allowlist,
            )
            payload = {"tool_call_id": call_id, "status": "success", "result": value}
        else:
            payload = {
                "tool_call_id": call_id,
                "status": "error",
                "error": result.error or f"tool {result.status}",
                "error_type": result.error_kind or "ToolError",
                "details": {
                    "framework_status": result.status,
                    "error_code": result.error_code,
                    "retryable": result.retryable,
                    "side_effect_committed": result.side_effect_committed,
                    "available_tools": list(available_tools),
                },
            }
        payload["model_tool_call_id"] = result.call_id
        self.store.append("tool.result", actor="tool", payload=payload, turn_id=turn_id)
        return payload

    def _append_deferred_file_effect_job(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        turn_id: str | None,
        declared: list[FileEffect],
        include_declared: bool = True,
    ) -> None:
        requires_snapshot = _tool_requires_deferred_snapshot(tool_name, args)
        if not requires_snapshot and (not include_declared or not declared):
            return
        self._file_effect_jobs.append(
            DeferredFileEffectJob(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=dict(args),
                turn_id=turn_id,
                declared=declared,
                include_declared=include_declared,
                requires_snapshot=requires_snapshot,
            )
        )


def _normalize(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _normalize_user_path(path: str | Path) -> str | Path:
    if os.name != "nt" or not isinstance(path, str):
        return path
    value = path.replace("\\", "/")
    parts = value.split("/")
    drive: str | None = None
    tail: list[str] = []
    if len(parts) >= 2 and parts[0] == "" and len(parts[1]) == 1 and parts[1].isalpha():
        drive = parts[1]
        tail = parts[2:]
    elif (
        len(parts) >= 3
        and parts[0] == ""
        and parts[1].lower() in {"mnt", "cygdrive"}
        and len(parts[2]) == 1
        and parts[2].isalpha()
    ):
        drive = parts[2]
        tail = parts[3:]
    if drive is None:
        return path
    suffix = "/".join(part for part in tail if part)
    return f"{drive.upper()}:/{suffix}" if suffix else f"{drive.upper()}:/"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _tool_requires_deferred_snapshot(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in {"write", "edit"}:
        return True
    if tool_name != "bash":
        return False
    command = args.get("command")
    return isinstance(command, str) and _bash_command_may_write(command)


def _bash_command_may_write(command: str) -> bool:
    lowered = command.lower()
    if re.search(r"(^|[^0-9])>{1,2}(?!&)", command):
        return True
    if re.search(
        r"\b(cat\s+>|tee|touch|mkdir|rm|rmdir|del|move|mv|copy|cp)\b", lowered
    ):
        return True
    if re.search(r"\bsed\b[^;&|]*\s-i\b", lowered):
        return True
    if re.search(r"\b(?:npm|pnpm|yarn)\s+(?:install|add)\b", lowered):
        return not re.search(r"(?:^|\s)(?:-g|--global)(?:\s|$)", lowered)
    return False


def _merge_detected_by(
    left: list[Literal["tool_args", "snapshot_diff", "bash_command_parse"]],
    right: list[Literal["tool_args", "snapshot_diff", "bash_command_parse"]],
) -> list[Literal["tool_args", "snapshot_diff", "bash_command_parse"]]:
    order: tuple[Literal["tool_args", "snapshot_diff", "bash_command_parse"], ...] = (
        "tool_args",
        "snapshot_diff",
        "bash_command_parse",
    )
    values = {*left, *right}
    return [value for value in order if value in values]


def _effect_type_order(effect_type: str) -> int:
    return {"file.write": 0, "file.edit": 1, "file.delete": 2, "file.read": 3}.get(
        effect_type, 99
    )


def _non_option_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token and not token.startswith("-")]


def _powershell_path_args(tokens: list[str]) -> list[str]:
    paths: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        lower = token.lower()
        if lower in {"-path", "-literalpath"}:
            skip_next = False
            continue
        if lower.startswith("-"):
            skip_next = lower in {"-encoding", "-totalcount", "-tail", "-readcount"}
            continue
        paths.append(token)
    return paths


def _spool_large_tool_result(
    *,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    tool_call_id: str,
    run_dir: Path,
    bash_full_output_allowlist: list[str],
) -> Any:
    if tool_name not in SPOOLED_TEXT_TOOL_NAMES or not isinstance(result, str):
        return result
    if tool_name == "bash":
        command = args.get("command")
        if isinstance(command, str) and _bash_command_matches_allowlist(
            command, bash_full_output_allowlist
        ):
            return result
    lines = result.splitlines()
    if len(result) <= MAX_BASH_RESULT_CHARS and len(lines) <= MAX_BASH_RESULT_LINES:
        return result

    output_dir = run_dir / "tool_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{tool_call_id}.txt"
    spooled_text = redact_secrets(result)
    output_path.write_text(spooled_text, encoding="utf-8")

    preview = "\n".join(lines[:BASH_RESULT_PREVIEW_LINES])
    if len(preview) > BASH_RESULT_PREVIEW_CHARS:
        preview = preview[:BASH_RESULT_PREVIEW_CHARS].rstrip()

    return {
        "status": "truncated",
        "truncated": True,
        "reason": f"{tool_name} output exceeded the model-visible result limit",
        "char_count": len(result),
        "line_count": len(lines),
        "preview_line_count": len(preview.splitlines()),
        "preview": preview,
        "full_output_path": str(output_path.resolve()),
        "next_step": (
            f"Full {tool_name} output was written to full_output_path. "
            "Use the read tool with file_path=full_output_path and offset/limit "
            "to inspect additional chunks only if needed."
        ),
    }


def _bash_command_matches_allowlist(command: str, allowlist: list[str]) -> bool:
    command = command.strip()
    if not command:
        return False
    for raw_entry in allowlist:
        entry = raw_entry.strip()
        if not entry:
            continue
        if command == entry or command.startswith(f"{entry} "):
            return True
    return False
