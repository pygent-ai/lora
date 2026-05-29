from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_mapping_file
from .schema import CaseDefinition, CaseRunRef, WorkspaceRef


class CaseManager:
    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).expanduser().resolve()

    def load(self, path: str | Path) -> CaseDefinition:
        case_path = Path(path).expanduser().resolve()
        data = load_mapping_file(case_path)
        case = CaseDefinition.from_dict(data)
        self.validate(case)
        return case

    def validate(self, case: CaseDefinition) -> None:
        _validate_path_id(case.id, "case id")
        messages = case.input.get("messages")
        prompt = case.input.get("content") or case.input.get("prompt")
        if messages is None and not prompt:
            raise ValueError("case input must define messages, content, or prompt")
        if messages is not None:
            if not isinstance(messages, list) or not messages:
                raise ValueError("case input.messages must be a non-empty list")
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    raise ValueError(f"case input.messages[{index}] must be a mapping")
                role = message.get("role", "user")
                if role not in {"user", "assistant"}:
                    raise ValueError(f"case input.messages[{index}].role must be user or assistant")
                if "content" not in message:
                    raise ValueError(f"case input.messages[{index}].content is required")

        required_tools = _required_tools(case)
        for index, item in enumerate(required_tools):
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError(f"expect.tool_calls.required[{index}].name is required")

        unchanged = case.expect.get("files", {}).get("unchanged", [])
        if unchanged is not None and not isinstance(unchanged, list):
            raise ValueError("expect.files.unchanged must be a list")

    def prepare_workspace(self, case: CaseDefinition, case_run_ref: CaseRunRef) -> WorkspaceRef:
        setup = case.workspace.get("setup", [])
        if setup is None:
            setup = []
        if not isinstance(setup, list):
            raise ValueError("workspace.setup must be a list")
        actions_path = Path(case_run_ref.run_dir) / "workspace" / "setup_actions.jsonl"
        for index, step in enumerate(setup):
            if not isinstance(step, dict):
                raise ValueError("workspace.setup entries must be mappings")
            self._run_setup_action(
                step,
                index=index,
                actions_path=actions_path,
                allow_commands=bool(case.workspace.get("allow_commands")),
            )

        baseline = self._write_baseline(case, case_run_ref)
        return WorkspaceRef(
            workspace_root=str(self.workspace_root),
            case_run_id=case_run_ref.case_run_id,
            baseline_path=str(baseline),
        )

    def case_hash(self, case: CaseDefinition) -> str:
        payload = json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _copy_fixture(self, step: dict[str, Any]) -> None:
        source = _resolve_workspace_path(
            self.workspace_root,
            step.get("from"),
            field_name="workspace.setup[].from",
        )
        target = _resolve_workspace_path(
            self.workspace_root,
            step.get("to"),
            field_name="workspace.setup[].to",
        )
        if not source.exists():
            raise FileNotFoundError(f"Fixture source does not exist: {source}")
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _write_file(self, step: dict[str, Any]) -> None:
        path = _resolve_workspace_path(
            self.workspace_root,
            step.get("path"),
            field_name="workspace.setup[].path",
        )
        if "content" not in step or not isinstance(step.get("content"), str):
            raise ValueError("workspace.setup write_file.content must be a string")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(step["content"], encoding="utf-8")

    def _mkdir(self, step: dict[str, Any]) -> None:
        path = _resolve_workspace_path(
            self.workspace_root,
            step.get("path"),
            field_name="workspace.setup[].path",
        )
        path.mkdir(parents=True, exist_ok=True)

    def _delete_path(self, step: dict[str, Any]) -> None:
        path = _resolve_workspace_path(
            self.workspace_root,
            step.get("path"),
            field_name="workspace.setup[].path",
        )
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink()

    def _run_command(self, step: dict[str, Any], *, allow_commands: bool) -> dict[str, Any]:
        if not allow_commands:
            raise ValueError("workspace.setup run_command requires workspace.allow_commands: true")
        command = step.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError("workspace.setup run_command.command must be a non-empty string")
        cwd = _resolve_workspace_path(
            self.workspace_root,
            step.get("cwd", "."),
            field_name="workspace.setup[].cwd",
        )
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError(f"workspace.setup run_command.cwd must be an existing directory: {step.get('cwd', '.')}")
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        details = {
            "command": command,
            "cwd": str(cwd),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        return details

    def _run_setup_action(
        self,
        step: dict[str, Any],
        *,
        index: int,
        actions_path: Path,
        allow_commands: bool,
    ) -> None:
        action_type = step.get("type")
        started_at = _now()
        record: dict[str, Any] = {
            "index": index,
            "type": action_type,
            "status": "success",
            "started_at": started_at,
        }
        try:
            if action_type == "copy_fixture":
                self._copy_fixture(step)
                record.update(
                    {
                        "from": str(
                            _resolve_workspace_path(
                                self.workspace_root,
                                step.get("from"),
                                field_name="workspace.setup[].from",
                            )
                        ),
                        "to": str(
                            _resolve_workspace_path(
                                self.workspace_root,
                                step.get("to"),
                                field_name="workspace.setup[].to",
                            )
                        ),
                    }
                )
            elif action_type == "write_file":
                self._write_file(step)
                record["path"] = str(
                    _resolve_workspace_path(
                        self.workspace_root,
                        step.get("path"),
                        field_name="workspace.setup[].path",
                    )
                )
            elif action_type == "mkdir":
                self._mkdir(step)
                record["path"] = str(
                    _resolve_workspace_path(
                        self.workspace_root,
                        step.get("path"),
                        field_name="workspace.setup[].path",
                    )
                )
            elif action_type == "delete_path":
                self._delete_path(step)
                record["path"] = str(
                    _resolve_workspace_path(
                        self.workspace_root,
                        step.get("path"),
                        field_name="workspace.setup[].path",
                    )
                )
            elif action_type == "run_command":
                record.update(self._run_command(step, allow_commands=allow_commands))
                if record["exit_code"] != 0:
                    raise RuntimeError(
                        f"workspace.setup run_command failed with exit code {record['exit_code']}: {record['command']}"
                    )
            else:
                raise ValueError(f"Unsupported workspace setup type: {action_type}")
        except Exception as exc:
            record.update(
                {
                    "status": "failure",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
            _append_jsonl(actions_path, record)
            raise
        record["finished_at"] = _now()
        _append_jsonl(actions_path, record)

    def _write_baseline(self, case: CaseDefinition, case_run_ref: CaseRunRef) -> Path:
        files = case.expect.get("files", {}).get("unchanged", []) or []
        baseline: dict[str, dict[str, Any]] = {}
        for raw_path in files:
            path = _resolve_workspace_path(
                self.workspace_root,
                raw_path,
                field_name="expect.files.unchanged",
            )
            baseline[str(raw_path)] = _file_snapshot(path)
        baseline_path = Path(case_run_ref.run_dir) / "workspace" / "before_hashes.json"
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return baseline_path


def _required_tools(case: CaseDefinition) -> list[Any]:
    return list(case.expect.get("tool_calls", {}).get("required", []) or [])


def _resolve_workspace_path(root: Path, value: Any, *, field_name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    workspace_root = root.expanduser().resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must stay within workspace_root: {value}") from exc
    return resolved


def _resolve_under_root(root: Path, value: Any) -> Path:
    return _resolve_workspace_path(root, value, field_name="workspace path")


def _validate_path_id(value: str, field_name: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "/" in value or "\\" in value:
        raise ValueError(f"{field_name} must not contain path traversal")


def _file_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "content_hash": None, "size": None}
    data = path.read_bytes()
    return {
        "exists": True,
        "content_hash": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    row = dict(data)
    row.setdefault("finished_at", _now())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
