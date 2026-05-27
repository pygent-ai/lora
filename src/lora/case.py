from __future__ import annotations

import hashlib
import json
import shutil
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
        for step in setup:
            if not isinstance(step, dict):
                raise ValueError("workspace.setup entries must be mappings")
            if step.get("type") == "copy_fixture":
                self._copy_fixture(step)
            else:
                raise ValueError(f"Unsupported workspace setup type: {step.get('type')}")

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
        source = _resolve_under_root(self.workspace_root, step.get("from"))
        target = _resolve_under_root(self.workspace_root, step.get("to"))
        if not source.exists():
            raise FileNotFoundError(f"Fixture source does not exist: {source}")
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _write_baseline(self, case: CaseDefinition, case_run_ref: CaseRunRef) -> Path:
        files = case.expect.get("files", {}).get("unchanged", []) or []
        baseline: dict[str, dict[str, Any]] = {}
        for raw_path in files:
            path = _resolve_under_root(self.workspace_root, raw_path)
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


def _resolve_under_root(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("workspace path must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _file_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "content_hash": None, "size": None}
    data = path.read_bytes()
    return {
        "exists": True,
        "content_hash": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
