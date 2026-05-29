from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .case import CaseManager
from .regression import RegressionManifest
from .schema import CaseDefinition, CaseRunRef, RunConfig
from .session import SessionManager
from .trace import EventStore


@dataclass(slots=True)
class GeneratedTestResult:
    status: str
    session_id: str
    case_id: str
    case_run_id: str
    generated_path: str | None = None
    metadata_path: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "status": self.status,
            "session_id": self.session_id,
            "case_id": self.case_id,
            "case_run_id": self.case_run_id,
            "generated_path": self.generated_path,
            "metadata_path": self.metadata_path,
            "reason": self.reason,
        }
        return {key: value for key, value in data.items() if value is not None}


class TestGenerator:
    def __init__(self, *, config: RunConfig, session_manager: SessionManager) -> None:
        self.config = config
        self.session_manager = session_manager

    def generate(self, session_id: str, case_run_id: str) -> GeneratedTestResult:
        ref = find_case_run(self.session_manager, session_id, case_run_id)
        run_dir = Path(ref.run_dir)
        verdict = _read_json(run_dir / "verdict.json")
        status = str(verdict.get("status") or "error")
        if status == "passed":
            return GeneratedTestResult(
                status="skipped",
                session_id=session_id,
                case_id=ref.case_id,
                case_run_id=case_run_id,
                reason="source run already passed",
            )

        source_case = CaseManager(self.config.workspace_root).load(run_dir / "case.yaml")
        analysis = _read_optional_json(run_dir / "analysis.json")
        generated_case = _generated_case(source_case, verdict)
        generated_dir = Path(self.session_manager.show(session_id)["session"]["session_dir"]) / "generated_tests" / case_run_id
        generated_dir.mkdir(parents=True, exist_ok=True)
        generated_path = generated_dir / "generated_case.yaml"
        metadata_path = generated_dir / "metadata.json"
        generated_path.write_text(_dump_yaml(generated_case.to_dict()), encoding="utf-8")

        metadata = {
            "source_session_id": session_id,
            "source_case_id": ref.case_id,
            "source_case_run_id": case_run_id,
            "source_status": status,
            "generated_path": str(generated_path),
            "registered": False,
            "verdict": verdict,
            "recommended_tests": _recommended_tests(analysis),
        }
        _write_json(metadata_path, metadata)
        EventStore(ref).append(
            "test.generated",
            actor="system",
            payload={
                "source_session_id": session_id,
                "source_case_id": ref.case_id,
                "source_case_run_id": case_run_id,
                "generated_path": str(generated_path),
                "metadata_path": str(metadata_path),
                "registered": False,
                "status": "generated",
            },
            turn_id=None,
        )
        return GeneratedTestResult(
            status="generated",
            session_id=session_id,
            case_id=ref.case_id,
            case_run_id=case_run_id,
            generated_path=str(generated_path),
            metadata_path=str(metadata_path),
        )


class RegressionRegistrar:
    def __init__(self, *, config: RunConfig) -> None:
        self.config = config
        self.manifest_path = Path(config.lora_root) / "regression.json"

    def register(self, case_file: str | Path) -> dict[str, Any]:
        case_path = Path(case_file).expanduser()
        if not case_path.is_absolute():
            case_path = Path(self.config.workspace_root) / case_path
        case_path = case_path.resolve()
        CaseManager(self.config.workspace_root).load(case_path)
        normalized_path = _normalized_case_path(case_path, workspace_root=Path(self.config.workspace_root))

        manifest = self._load_manifest()
        existing = [case.to_dict() for case in manifest.cases]
        already_registered = any(_normalize_manifest_path(item["path"]) == normalized_path for item in existing)
        if not already_registered:
            existing.append({"path": normalized_path, "session_mode": "new"})
        existing = sorted(existing, key=lambda item: (str(item.get("path")), str(item.get("session_mode") or "")))

        payload = {
            "version": manifest.version,
            "fail_fast": manifest.fail_fast,
            "cases": existing,
        }
        _write_json(self.manifest_path, payload)
        return {
            "status": "registered" if not already_registered else "unchanged",
            "manifest": str(self.manifest_path),
            "case_path": normalized_path,
            "total": len(existing),
        }

    def _load_manifest(self) -> RegressionManifest:
        if not self.manifest_path.exists():
            return RegressionManifest()
        return RegressionManifest.from_file(self.manifest_path)


def find_case_run(manager: SessionManager, session_id: str, case_run_id: str) -> CaseRunRef:
    session_dir = Path(manager.show(session_id)["session"]["session_dir"])
    matches = list((session_dir / "cases").glob(f"*/runs/{case_run_id}"))
    if not matches:
        raise FileNotFoundError(f"Case run {case_run_id!r} does not exist under session {session_id!r}")
    run_dir = matches[0]
    metadata = _read_json(run_dir / "run_metadata.json")
    return CaseRunRef(
        session_id=session_id,
        case_id=metadata["case_id"],
        case_run_id=case_run_id,
        run_dir=str(run_dir),
    )


def _generated_case(source: CaseDefinition, verdict: dict[str, Any]) -> CaseDefinition:
    return CaseDefinition(
        id=f"{source.id}-generated",
        title=f"Generated regression for {source.id}",
        type=source.type,
        session={"mode": "new"},
        workspace=_copy_workspace_setup(source.workspace),
        input=dict(source.input),
        expect=_narrow_expect(source.expect, verdict),
        metrics=_narrow_metrics(source.metrics, verdict),
    )


def _copy_workspace_setup(workspace: dict[str, Any]) -> dict[str, Any]:
    setup = workspace.get("setup")
    if not setup:
        return {}
    return {"setup": setup}


def _narrow_expect(source_expect: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    expect: dict[str, Any] = {}
    for failure in _failures(verdict, "answer.contains"):
        expected = failure.get("expected")
        if expected is not None:
            expect.setdefault("answer", {}).setdefault("contains", []).append(expected)
    for failure in _failures(verdict, "tool.required"):
        expected = failure.get("expected")
        if expected is not None:
            expect.setdefault("tool_calls", {}).setdefault("required", []).append({"name": expected})
    for failure in _failures(verdict, "files.unchanged"):
        path = failure.get("path") or failure.get("expected")
        if path is not None:
            expect.setdefault("files", {}).setdefault("unchanged", []).append(path)
    return _dedupe_expect(expect) or dict(source_expect)


def _narrow_metrics(source_metrics: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for failure in _failures(verdict, "metrics.max_turns"):
        expected = failure.get("expected")
        if isinstance(expected, int):
            metrics["max_turns"] = expected
    for failure in _failures(verdict, "metrics.max_tool_calls"):
        expected = failure.get("expected")
        if isinstance(expected, int):
            metrics["max_tool_calls"] = expected
    return metrics or dict(source_metrics)


def _failures(verdict: dict[str, Any], failure_type: str) -> list[dict[str, Any]]:
    return [
        failure
        for failure in verdict.get("failures") or []
        if isinstance(failure, dict) and failure.get("type") == failure_type
    ]


def _dedupe_expect(expect: dict[str, Any]) -> dict[str, Any]:
    answer_contains = expect.get("answer", {}).get("contains")
    if isinstance(answer_contains, list):
        expect["answer"]["contains"] = list(dict.fromkeys(answer_contains))
    required = expect.get("tool_calls", {}).get("required")
    if isinstance(required, list):
        seen = set()
        deduped = []
        for item in required:
            key = item.get("name") if isinstance(item, dict) else item
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        expect["tool_calls"]["required"] = deduped
    unchanged = expect.get("files", {}).get("unchanged")
    if isinstance(unchanged, list):
        expect["files"]["unchanged"] = list(dict.fromkeys(unchanged))
    return expect


def _recommended_tests(analysis: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not analysis:
        return []
    recommended: list[dict[str, Any]] = []
    for root_cause in analysis.get("root_causes") or []:
        if isinstance(root_cause, dict):
            recommended.extend(item for item in root_cause.get("recommended_tests") or [] if isinstance(item, dict))
    return recommended


def _normalized_case_path(case_path: Path, *, workspace_root: Path) -> str:
    try:
        relative = case_path.relative_to(workspace_root.resolve())
    except ValueError:
        relative = case_path
    return relative.as_posix()


def _normalize_manifest_path(value: str) -> str:
    return Path(value).as_posix()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dump_yaml(data: dict[str, Any]) -> str:
    return "".join(_dump_node(data, 0))


def _dump_node(value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if item in ({}, [], None):
                continue
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:\n")
                lines.extend(_dump_node(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_dump_scalar(item)}\n")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{prefix}-\n")
                lines.extend(_dump_node(item, indent + 2))
            elif isinstance(item, list):
                lines.append(f"{prefix}-\n")
                lines.extend(_dump_node(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_dump_scalar(item)}\n")
        return lines
    return [f"{prefix}{_dump_scalar(value)}\n"]


def _dump_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text or text.strip() != text or any(char in text for char in ":#\n'\""):
        return json.dumps(text, ensure_ascii=False)
    return text
