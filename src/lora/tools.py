from __future__ import annotations

import hashlib
import inspect
import json
import os
import shlex
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from .io import utc_now, write_json
from .redaction import redact_secrets
from .schema import CaseRunRef
from .trace import EventStore

MAX_BASH_RESULT_CHARS = 20_000
MAX_BASH_RESULT_LINES = 200
BASH_RESULT_PREVIEW_LINES = 120
BASH_RESULT_PREVIEW_CHARS = 12_000

FILE_UNCHANGED_STUB = (
    "File unchanged since last read. The content from the earlier read tool_result in this conversation "
    "is still current. Refer to that instead of re-reading."
)
FILE_RANGE_CONTAINED_STUB = (
    "Requested range is contained in an earlier read tool_result for the same file version. "
    "Refer to the earlier result instead of re-reading."
)
FILE_RANGE_OVERLAP_STUB = (
    "Requested range partially overlaps an earlier read tool_result for the same file version. "
    "Returning only the unread portion."
)


@dataclass(slots=True)
class ReadRange:
    unit: Literal["line", "full"] = "full"
    start: int = 1
    end: int | Literal["EOF"] = "EOF"

    def covers(self, other: "ReadRange") -> bool:
        if self.unit == "full":
            return True
        if other.unit == "full" or self.unit != other.unit:
            return False
        return self.start <= other.start and _end_value(self.end) >= _end_value(other.end)

    def overlaps(self, other: "ReadRange") -> bool:
        if self.unit == "full" or other.unit == "full":
            return True
        if self.unit != other.unit:
            return False
        return self.start <= _end_value(other.end) and other.start <= _end_value(self.end)


@dataclass(slots=True)
class ReadDedupDecision:
    action: Literal["read", "stub", "partial"]
    reason: Literal["none", "exact", "contained", "overlap"]
    content: str | None = None
    previous_event_id: str | None = None


@dataclass(slots=True)
class ToolContext:
    case_run_ref: CaseRunRef
    turn_id: str | None = None


@dataclass(slots=True)
class ToolResult:
    status: Literal["success", "error", "stubbed", "partial"]
    result: Any = None
    error: str | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FileSnapshot:
    path: str
    exists: bool
    kind: Literal["file", "dir", "other", "missing"]
    size: int | None = None
    mtime_ns: int | None = None
    content_hash: str | None = None


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


class FileStateTracker:
    def __init__(self, session_state_dir: str | Path):
        self.state_dir = Path(session_state_dir)
        self.file_state_path = self.state_dir / "file_state.json"
        self.read_state_path = self.state_dir / "read_state.json"

    def record_read(self, path: str | Path, content_hash: str, read_range: ReadRange, event_id: str) -> None:
        normalized = _normalize(path)
        read_state = self._read_json(self.read_state_path)
        ranges = [
            row
            for row in read_state.get(normalized, [])
            if row.get("content_hash") == content_hash
        ]
        ranges.append(
            {
                "event_id": event_id,
                "content_hash": content_hash,
                "unit": read_range.unit,
                "start": read_range.start,
                "end": read_range.end,
                "read_at": utc_now(),
            }
        )
        read_state[normalized] = ranges
        write_json(self.read_state_path, read_state)

        file_state = self._read_json(self.file_state_path)
        file_state[normalized] = {
            "path": normalized,
            "exists": True,
            "content_hash": content_hash,
            "last_read_event_id": event_id,
            "read_ranges": ranges,
            "last_known_at": utc_now(),
        }
        write_json(self.file_state_path, file_state)

    def should_stub_read(
        self,
        path: str | Path,
        content_hash: str,
        read_range: ReadRange,
        dedup_level: Literal["exact", "contained", "overlap"] = "contained",
    ) -> ReadDedupDecision:
        ranges = self._read_json(self.read_state_path).get(_normalize(path), [])
        for row in ranges:
            if row.get("content_hash") != content_hash:
                continue
            previous = ReadRange(unit=row["unit"], start=int(row["start"]), end=row["end"])
            if previous.unit == read_range.unit and previous.start == read_range.start and previous.end == read_range.end:
                return ReadDedupDecision("stub", "exact", FILE_UNCHANGED_STUB, row.get("event_id"))
            if dedup_level in {"contained", "overlap"} and previous.covers(read_range):
                return ReadDedupDecision("stub", "contained", FILE_RANGE_CONTAINED_STUB, row.get("event_id"))
            if dedup_level == "overlap" and previous.overlaps(read_range):
                return ReadDedupDecision("partial", "overlap", FILE_RANGE_OVERLAP_STUB, row.get("event_id"))
        return ReadDedupDecision("read", "none")

    def read_text_file(
        self,
        path: str | Path,
        *,
        offset: int | None = None,
        limit: int | None = None,
        dedup_level: Literal["exact", "contained", "overlap"] = "contained",
        event_store: EventStore | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        target = Path(path).expanduser().resolve()
        content = target.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        read_range = _line_range(content, offset, limit)
        decision = self.should_stub_read(target, content_hash, read_range, dedup_level)
        if decision.action == "stub":
            return {"status": "stubbed", "content": decision.content, "dedup": decision.reason}

        selected = _select_lines(content, read_range)
        if decision.action == "partial":
            selected = f"{decision.content}\n\n{selected}"
        returned_range = asdict(read_range)
        event_payload = {
            "path": str(target),
            "content_hash": content_hash,
            "size": len(content.encode("utf-8")),
            "requested_range": asdict(read_range),
            "returned_range": returned_range,
            "dedup": decision.reason,
            "returned_content": selected,
        }
        event_id = (
            event_store.append("file.read", actor="tool", payload=event_payload, turn_id=turn_id)
            if event_store is not None
            else f"read_{uuid.uuid4().hex}"
        )
        self.record_read(target, content_hash, read_range, event_id)
        return {
            "status": "partial" if decision.action == "partial" else "success",
            "content": selected,
            "content_hash": content_hash,
            "range": asdict(read_range),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

class FileEffectTracker:
    IGNORED_DIRS = frozenset({".git", ".lora", ".venv", "__pycache__", ".pytest_cache", "sessions"})
    PATH_ARG_NAMES = ("file_path", "path", "relative_path")
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
            snapshots[normalized] = FileSnapshot(
                path=normalized,
                exists=True,
                kind="file",
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash=content_hash,
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
                    )
                )
        return sorted(effects, key=lambda effect: (_effect_type_order(effect.type), effect.path))

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

            detected_by = _merge_detected_by(declared_effect.detected_by, observed_effect.detected_by)
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
        return sorted(deduped, key=lambda effect: (_effect_type_order(effect.type), effect.path))

    def append_effects(
        self,
        effects: list[FileEffect],
        *,
        turn_id: str | None,
    ) -> None:
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

    def _path_from_args(self, args: dict[str, Any], *, allow_outside_workspace: bool = False) -> str | None:
        for name in self.PATH_ARG_NAMES:
            value = args.get(name)
            if isinstance(value, str) and value:
                return self._resolve_workspace_path(value, allow_outside_workspace=allow_outside_workspace)
        return None

    def _resolve_workspace_path(self, path: str | Path, *, allow_outside_workspace: bool = False) -> str:
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

    def _bash_read_effects(self, args: dict[str, Any], tool_call_id: str) -> list[FileEffect]:
        command = args.get("command") or args.get("cmd")
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


class ToolInterceptor:
    def __init__(
        self,
        store: EventStore,
        *,
        workspace_root: str | Path | None = None,
        track_file_effects: bool = False,
        allow_read_outside_workspace: bool = True,
    ):
        self.store = store
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

    def call_tool(self, name: str, args: dict[str, Any], ctx: ToolContext, tool: Callable[..., Any]) -> ToolResult:
        call_id = self.store.append(
            "tool.call",
            actor="assistant",
            payload={"tool_name": name, "args": args},
            turn_id=ctx.turn_id,
        )
        argument_error = _unsupported_argument_error(name, args, tool)
        if argument_error is not None:
            payload = {
                "tool_call_id": call_id,
                "status": "error",
                "error": argument_error,
                "error_type": "ToolArgumentError",
            }
            self.store.append("tool.result", actor="tool", payload=payload, turn_id=ctx.turn_id)
            return ToolResult(status="error", error=argument_error, tool_call_id=call_id)
        before: dict[str, FileSnapshot] | None = None
        declared: list[FileEffect] = []
        if self.file_effect_tracker is not None:
            before = self.file_effect_tracker.snapshot_workspace()
            declared = self.file_effect_tracker.declared_effects(name, args, call_id)
        try:
            result = tool(**args)
        except Exception as exc:  # noqa: BLE001 - tool boundary records all failures.
            self._append_file_effects_after_tool(
                before,
                declared,
                tool_name=name,
                tool_call_id=call_id,
                turn_id=ctx.turn_id,
            )
            payload = {"tool_call_id": call_id, "status": "error", "error": str(exc), "error_type": type(exc).__name__}
            self.store.append("tool.result", actor="tool", payload=payload, turn_id=ctx.turn_id)
            return ToolResult(status="error", error=str(exc), tool_call_id=call_id)

        tool_error = _structured_tool_error_payload(result)
        if tool_error is not None:
            self._append_file_effects_after_tool(
                before,
                declared,
                tool_name=name,
                tool_call_id=call_id,
                turn_id=ctx.turn_id,
                include_declared=False,
            )
            payload = {
                "tool_call_id": call_id,
                "status": "error",
                "error": tool_error["error"],
                "error_type": tool_error["error_type"],
            }
            if tool_error.get("details") is not None:
                payload["details"] = tool_error["details"]
            self.store.append("tool.result", actor="tool", payload=payload, turn_id=ctx.turn_id)
            return ToolResult(status="error", error=tool_error["error"], tool_call_id=call_id)

        self._append_file_effects_after_tool(
            before,
            declared,
            tool_name=name,
            tool_call_id=call_id,
            turn_id=ctx.turn_id,
        )
        status = "success"
        if isinstance(result, dict) and result.get("status") in {"stubbed", "partial"}:
            status = result["status"]
        result = _spool_large_bash_result(
            tool_name=name,
            result=result,
            tool_call_id=call_id,
            run_dir=self.store.run_dir,
        )
        self.store.append(
            "tool.result",
            actor="tool",
            payload={"tool_call_id": call_id, "status": status, "result": result},
            turn_id=ctx.turn_id,
        )
        return ToolResult(status=status, result=result, tool_call_id=call_id)  # type: ignore[arg-type]

    def _append_file_effects_after_tool(
        self,
        before: dict[str, FileSnapshot] | None,
        declared: list[FileEffect],
        *,
        tool_name: str,
        tool_call_id: str,
        turn_id: str | None,
        include_declared: bool = True,
    ) -> None:
        if self.file_effect_tracker is None or before is None:
            return
        after = self.file_effect_tracker.snapshot_workspace()
        observed = self.file_effect_tracker.observed_effects(
            before,
            after,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
        if not include_declared:
            declared = []
        self.file_effect_tracker.append_effects(
            self.file_effect_tracker.merge_effects(declared, observed),
            turn_id=turn_id,
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
    return {"file.write": 0, "file.edit": 1, "file.delete": 2, "file.read": 3}.get(effect_type, 99)


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


def _line_range(content: str, offset: int | None, limit: int | None) -> ReadRange:
    if offset is None and limit is None:
        return ReadRange()
    line_count = max(1, len(content.splitlines()))
    start = max(1, int(offset or 1))
    end: int | Literal["EOF"]
    if limit is None:
        end = "EOF"
    else:
        end = min(line_count, start + max(0, int(limit)) - 1)
    return ReadRange(unit="line", start=start, end=end)


def _select_lines(content: str, read_range: ReadRange) -> str:
    if read_range.unit == "full":
        return content
    lines = content.splitlines()
    end = len(lines) if read_range.end == "EOF" else int(read_range.end)
    return "\n".join(lines[read_range.start - 1 : end])


def _unsupported_argument_error(name: str, args: dict[str, Any], tool: Callable[..., Any]) -> str | None:
    try:
        signature = inspect.signature(tool)
    except (TypeError, ValueError):
        return None
    parameters = signature.parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return None
    allowed = {
        parameter_name
        for parameter_name, parameter in parameters.items()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }
    unknown = sorted(set(args) - allowed)
    if not unknown:
        return None
    allowed_text = ", ".join(sorted(allowed)) or "none"
    unknown_text = ", ".join(unknown)
    return f"Tool {name!r} received unsupported argument(s): {unknown_text}. Supported arguments: {allowed_text}."


def _structured_tool_error_payload(result: Any) -> dict[str, Any] | None:
    error_type = getattr(result, "error_type", None)
    details = getattr(result, "details", None)
    if isinstance(error_type, str) and error_type:
        return {
            "error": str(result),
            "error_type": error_type,
            "details": details if isinstance(details, dict) else None,
        }
    if isinstance(result, dict) and result.get("ok") is False:
        error = result.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("error") or result)
            return {
                "error": message,
                "error_type": str(error.get("type") or error.get("error_type") or "ToolError"),
                "details": error.get("details") if isinstance(error.get("details"), dict) else None,
            }
        return {"error": str(error or result), "error_type": "ToolError", "details": None}
    return None


def _spool_large_bash_result(*, tool_name: str, result: Any, tool_call_id: str, run_dir: Path) -> Any:
    if tool_name != "bash" or not isinstance(result, str):
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
        "reason": "bash output exceeded the model-visible result limit",
        "char_count": len(result),
        "line_count": len(lines),
        "preview_line_count": len(preview.splitlines()),
        "preview": preview,
        "full_output_path": str(output_path.resolve()),
        "next_step": (
            "Full bash output was written to full_output_path. "
            "Use the read tool with file_path=full_output_path and offset/limit "
            "to inspect additional chunks only if needed."
        ),
    }


def _end_value(value: int | Literal["EOF"]) -> int:
    return 2**31 - 1 if value == "EOF" else int(value)
