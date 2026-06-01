from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pygent.module.tool import BaseTool

from .schema import CaseRunRef
from .trace import EventStore

MAX_DIFF_SNAPSHOT_BYTES = 1_000_000
MAX_INLINE_PATCH_CHARS = 20_000
MAX_INLINE_PATCH_LINES = 200


@dataclass(slots=True)
class DiffSnapshotContent:
    content: str | None
    available: bool
    reason: str | None = None


@dataclass(slots=True)
class DiffArtifact:
    diff_id: str
    patch_available: bool
    patch_path: str | None
    patch_char_count: int = 0
    patch_line_count: int = 0
    reason: str | None = None


class DiffRecorder:
    def __init__(self, store: EventStore, workspace_root: str | Path):
        self.store = store
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.diffs_dir = self.store.run_dir / "diffs"

    def record_effect(self, effect: Any, *, turn_id: str | None) -> None:
        if effect.type not in {"file.write", "file.edit", "file.delete"}:
            return
        path = Path(effect.path).expanduser().resolve()
        relative_path = _relative_workspace_path(path, self.workspace_root)
        before = DiffSnapshotContent(
            content=getattr(effect, "before_content", None),
            available=bool(getattr(effect, "before_content_available", False)),
            reason=getattr(effect, "before_content_unavailable_reason", None),
        )
        after = DiffSnapshotContent(
            content=getattr(effect, "after_content", None),
            available=bool(getattr(effect, "after_content_available", False)),
            reason=getattr(effect, "after_content_unavailable_reason", None),
        )
        artifact = self._write_artifact(effect, relative_path=relative_path, before=before, after=after)
        payload = {
            "diff_id": artifact.diff_id,
            "tool_call_id": effect.tool_call_id,
            "tool_name": effect.tool_name,
            "path": str(path),
            "relative_path": relative_path,
            "change_type": _change_type(effect.type),
            "before_exists": effect.before_exists,
            "after_exists": effect.after_exists,
            "before_hash": effect.before_hash,
            "after_hash": effect.after_hash,
            "snapshot_before_path": self._snapshot_path(effect.tool_call_id, "before", relative_path)
            if before.available
            else None,
            "snapshot_after_path": self._snapshot_path(effect.tool_call_id, "after", relative_path)
            if after.available
            else None,
            "patch_available": artifact.patch_available,
            "patch_path": artifact.patch_path,
            "patch_char_count": artifact.patch_char_count,
            "patch_line_count": artifact.patch_line_count,
        }
        if artifact.reason is not None:
            payload["patch_unavailable_reason"] = artifact.reason
        self.store.append("diff.created", actor="tool", payload=payload, turn_id=turn_id)

    def _write_artifact(
        self,
        effect: Any,
        *,
        relative_path: str,
        before: DiffSnapshotContent,
        after: DiffSnapshotContent,
    ) -> DiffArtifact:
        diff_id = f"diff_{uuid.uuid4().hex}"
        if before.available and before.content is not None:
            _write_text(Path(self._snapshot_path(effect.tool_call_id, "before", relative_path)), before.content)
        if after.available and after.content is not None:
            _write_text(Path(self._snapshot_path(effect.tool_call_id, "after", relative_path)), after.content)

        missing_reason = _missing_patch_reason(effect.type, before, after)
        if missing_reason is not None:
            return DiffArtifact(diff_id=diff_id, patch_available=False, patch_path=None, reason=missing_reason)

        before_lines = [] if effect.before_exists is False else _split_patch_lines(before.content or "")
        after_lines = [] if effect.after_exists is False else _split_patch_lines(after.content or "")
        fromfile = "/dev/null" if effect.before_exists is False else f"a/{relative_path}"
        tofile = "/dev/null" if effect.after_exists is False else f"b/{relative_path}"
        patch_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=fromfile,
                tofile=tofile,
                lineterm="",
            )
        )
        patch = _normalize_patch_newlines("\n".join(patch_lines))
        patch_path = self.diffs_dir / "patches" / f"{diff_id}.patch"
        _write_text(patch_path, patch)
        return DiffArtifact(
            diff_id=diff_id,
            patch_available=True,
            patch_path=str(patch_path.resolve()),
            patch_char_count=len(patch),
            patch_line_count=len(patch.splitlines()),
        )

    def _snapshot_path(self, tool_call_id: str, side: Literal["before", "after"], relative_path: str) -> str:
        target = self.diffs_dir / "snapshots" / tool_call_id / side
        for part in Path(relative_path).parts:
            if part in {"", ".", ".."}:
                raise ValueError(f"Unsafe diff snapshot path: {relative_path}")
            target /= part
        return str(target.resolve())


class DiffTool(BaseTool):
    def __init__(
        self,
        *,
        case_run_ref: CaseRunRef,
        workspace_root: str | Path,
        turn_id: str | None = None,
    ):
        self.case_run_ref = case_run_ref
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.turn_id = turn_id
        self.store = EventStore(case_run_ref)
        super().__init__(
            name="diff",
            description=(
                "Show persisted file diffs for the current Lora session or run. "
                "Uses recorded file effects and stored snapshots, so results survive chat restarts."
            ),
            include_call_description_parameter=False,
            parameters_additional_properties=False,
        )
        self.parameters.data.update(
            {
                "scope": {
                    "name": "scope",
                    "type": "string",
                    "description": "Which persisted changes to inspect. Defaults to run.",
                    "required": False,
                    "default": "run",
                    "enum": ["turn", "run", "session"],
                },
                "path": {
                    "name": "path",
                    "type": "string",
                    "description": "Optional workspace-relative or absolute file path filter.",
                    "required": False,
                    "default": None,
                },
                "tool_call_id": {
                    "name": "tool_call_id",
                    "type": "string",
                    "description": "Optional tool call id to inspect a single tool's file changes.",
                    "required": False,
                    "default": None,
                },
                "format": {
                    "name": "format",
                    "type": "string",
                    "description": "Output format. summary lists changed files; patch returns unified diff; json returns structured records.",
                    "required": False,
                    "default": "summary",
                    "enum": ["summary", "patch", "json"],
                },
                "limit": {
                    "name": "limit",
                    "type": "integer",
                    "description": "Maximum number of diff records or patch files to return.",
                    "required": False,
                    "default": 20,
                    "min_value": 1,
                },
            }
        )

    def forward(
        self,
        scope: Literal["turn", "run", "session"] = "run",
        path: str | None = None,
        tool_call_id: str | None = None,
        format: Literal["summary", "patch", "json"] = "summary",
        limit: int = 20,
    ) -> dict[str, Any]:
        records = self._records(scope)
        if scope == "turn":
            records = [record for record in records if record.get("turn_id") == self.turn_id]
        if path:
            normalized = _normalize_filter_path(path, self.workspace_root)
            records = [
                record
                for record in records
                if record.get("path") == normalized or record.get("relative_path") == _relative_workspace_path(Path(normalized), self.workspace_root)
            ]
        if tool_call_id:
            records = [record for record in records if record.get("tool_call_id") == tool_call_id]
        records = records[: max(0, int(limit))]

        if format == "json":
            return {"scope": scope, "count": len(records), "diffs": records}
        if format == "patch":
            return self._patch_result(scope, records)
        return {
            "scope": scope,
            "count": len(records),
            "diffs": [
                {
                    "diff_id": record.get("diff_id"),
                    "change_type": record.get("change_type"),
                    "path": record.get("relative_path") or record.get("path"),
                    "tool_name": record.get("tool_name"),
                    "tool_call_id": record.get("tool_call_id"),
                    "patch_available": record.get("patch_available"),
                    "patch_path": record.get("patch_path"),
                }
                for record in records
            ],
        }

    def _records(self, scope: str) -> list[dict[str, Any]]:
        if scope in {"turn", "run"}:
            path = self.store.run_dir / "diffs" / "diff_events.jsonl"
        elif scope == "session":
            if self.store.session_dir is None:
                return []
            path = self.store.session_dir / "logs" / "diff_events.jsonl"
        else:
            raise ValueError(f"Unsupported diff scope: {scope}")
        return list(EventStore.iter_jsonl(path))

    def _patch_result(self, scope: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        patches: list[str] = []
        warnings: list[dict[str, Any]] = []
        for record in records:
            patch_path = record.get("patch_path")
            if not record.get("patch_available") or not patch_path:
                warnings.append(
                    {
                        "diff_id": record.get("diff_id"),
                        "path": record.get("relative_path") or record.get("path"),
                        "reason": record.get("patch_unavailable_reason") or "patch artifact is unavailable",
                    }
                )
                continue
            patches.append(Path(patch_path).read_text(encoding="utf-8"))
        patch = "\n".join(patch.rstrip("\n") for patch in patches if patch)
        result: dict[str, Any] = {"scope": scope, "count": len(records), "patch": patch}
        if warnings:
            result["warnings"] = warnings
        if len(patch) > MAX_INLINE_PATCH_CHARS or len(patch.splitlines()) > MAX_INLINE_PATCH_LINES:
            result.update(
                {
                    "status": "truncated",
                    "truncated": True,
                    "char_count": len(patch),
                    "line_count": len(patch.splitlines()),
                    "preview": "\n".join(patch.splitlines()[:MAX_INLINE_PATCH_LINES]),
                    "patch_paths": [record.get("patch_path") for record in records if record.get("patch_path")],
                }
            )
            result.pop("patch", None)
        return result


def read_snapshot_content(path: Path) -> DiffSnapshotContent:
    try:
        stat = path.stat()
    except OSError as exc:
        return DiffSnapshotContent(content=None, available=False, reason=str(exc))
    if stat.st_size > MAX_DIFF_SNAPSHOT_BYTES:
        return DiffSnapshotContent(content=None, available=False, reason="file exceeds max snapshot size")
    try:
        return DiffSnapshotContent(content=path.read_text(encoding="utf-8"), available=True)
    except UnicodeDecodeError:
        return DiffSnapshotContent(content=None, available=False, reason="file is not UTF-8 text")
    except OSError as exc:
        return DiffSnapshotContent(content=None, available=False, reason=str(exc))


def _relative_workspace_path(path: Path, workspace_root: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Path is outside workspace: {path}") from exc


def _normalize_filter_path(path: str, workspace_root: Path) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()
    _relative_workspace_path(resolved, workspace_root)
    return str(resolved)


def _change_type(event_type: str) -> str:
    return event_type.removeprefix("file.")


def _missing_patch_reason(effect_type: str, before: DiffSnapshotContent, after: DiffSnapshotContent) -> str | None:
    if effect_type in {"file.edit", "file.delete"} and not before.available:
        return before.reason or "before snapshot is unavailable"
    if effect_type in {"file.edit", "file.write"} and not after.available:
        return after.reason or "after snapshot is unavailable"
    return None


def _split_patch_lines(content: str) -> list[str]:
    return content.splitlines()


def _normalize_patch_newlines(patch: str) -> str:
    if not patch:
        return ""
    return patch if patch.endswith("\n") else f"{patch}\n"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
