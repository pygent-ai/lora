from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .case import CaseManager
from .config import load_mapping_file
from .evaluation import Evaluator
from .runner import execute_case_run
from .runtime import AgentRuntimeAdapter
from .schema import CaseRunRef, RunConfig
from .session import SessionManager
from .trace import EventStore


RegressionStatus = Literal["passed", "failed", "error", "skipped"]


@dataclass(slots=True)
class RegressionCaseSpec:
    path: str
    session_mode: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RegressionCaseSpec":
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("regression cases[].path must be a non-empty string")
        session_mode = data.get("session_mode")
        if session_mode is not None and session_mode not in {"new", "resume", "fork", "shared"}:
            raise ValueError("regression cases[].session_mode must be one of new, resume, fork, shared")
        return cls(path=path, session_mode=session_mode)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path}
        if self.session_mode is not None:
            data["session_mode"] = self.session_mode
        return data


@dataclass(slots=True)
class RegressionManifest:
    version: str = "1.0"
    cases: list[RegressionCaseSpec] = field(default_factory=list)
    fail_fast: bool = False

    @classmethod
    def from_file(cls, path: str | Path) -> "RegressionManifest":
        manifest_path = Path(path)
        if manifest_path.suffix.lower() == ".json":
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            data = load_mapping_file(manifest_path)
        if not isinstance(data, dict):
            raise ValueError("regression manifest must be a mapping")
        version = str(data.get("version") or "1.0")
        if version != "1.0":
            raise ValueError("regression manifest version must be 1.0")
        cases_data = data.get("cases", [])
        if not isinstance(cases_data, list):
            raise ValueError("regression manifest cases must be a list")
        if any(not isinstance(item, dict) for item in cases_data):
            raise ValueError("regression manifest cases entries must be mappings")
        return cls(
            version=version,
            cases=[RegressionCaseSpec.from_dict(item) for item in cases_data],
            fail_fast=bool(data.get("fail_fast", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fail_fast": self.fail_fast,
            "cases": [case.to_dict() for case in self.cases],
        }


class RegressionRunner:
    def __init__(
        self,
        *,
        config: RunConfig,
        session_manager: SessionManager,
        case_manager: CaseManager,
        runtime_adapter: AgentRuntimeAdapter | None = None,
        evaluator: Evaluator | None = None,
    ) -> None:
        self.config = config
        self.session_manager = session_manager
        self.case_manager = case_manager
        self.runtime_adapter = runtime_adapter
        self.evaluator = evaluator
        self.regressions_root = Path(config.lora_root) / "regressions"

    def run(self, manifest_path: str | Path) -> dict[str, Any]:
        manifest = RegressionManifest.from_file(manifest_path)
        run_id = self._new_regression_run_id()
        run_dir = self.regressions_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        ref = CaseRunRef(
            session_id=run_id,
            case_id="regression",
            case_run_id=run_id,
            run_dir=str(run_dir),
        )
        store = EventStore(ref)
        config_payload = {
            "regression_run_id": run_id,
            "manifest_path": str(Path(manifest_path).resolve()),
            "manifest": manifest.to_dict(),
            "run_config": self.config.to_dict(),
        }
        _write_json(run_dir / "regression_config.json", config_payload)
        store.append(
            "regression.started",
            actor="system",
            payload={"regression_run_id": run_id, "total": len(manifest.cases)},
            turn_id=None,
        )

        case_results: list[dict[str, Any]] = []
        status: RegressionStatus = "skipped" if not manifest.cases else "passed"
        try:
            for index, case_spec in enumerate(manifest.cases):
                case_result = self._run_case(case_spec, manifest_path=Path(manifest_path), index=index)
                case_results.append(case_result)
                _append_jsonl(run_dir / "case_runs.jsonl", case_result)
                status = _combined_status(case_results)
                if manifest.fail_fast and case_result["status"] in {"failed", "error"}:
                    break
        finally:
            result = _result_payload(
                run_id=run_id,
                run_dir=run_dir,
                manifest_path=Path(manifest_path),
                status=status,
                total=len(manifest.cases),
                cases=case_results,
            )
            _write_json(run_dir / "result.json", result)
            store.append(
                "regression.finished",
                actor="system",
                payload={key: value for key, value in result.items() if key != "cases"},
                turn_id=None,
            )
        return result

    def _run_case(self, case_spec: RegressionCaseSpec, *, manifest_path: Path, index: int) -> dict[str, Any]:
        case_path = _resolve_case_path(case_spec.path, workspace_root=Path(self.config.workspace_root), manifest_path=manifest_path)
        try:
            payload = execute_case_run(
                config=self.config,
                manager=self.session_manager,
                case_manager=self.case_manager,
                case_file=case_path,
                session_mode=case_spec.session_mode,
                runtime_adapter=self.runtime_adapter,
                evaluator=self.evaluator,
            )
            return _case_summary(case_spec, case_path, payload)
        except Exception as exc:  # noqa: BLE001 - suite should preserve case errors and continue.
            error_payload = self._record_case_error(case_spec, case_path, index, exc)
            return error_payload

    def _record_case_error(
        self,
        case_spec: RegressionCaseSpec,
        case_path: Path,
        index: int,
        exc: BaseException,
    ) -> dict[str, Any]:
        case_id = f"manifest-case-{index + 1}"
        session = self.session_manager.create(case_id, mode="regression-error")
        ref = self.session_manager.start_case_run(session.session_id, case_id, run_config=self.config)
        store = EventStore(ref)
        store.append_error(
            exc,
            event_type="runtime.error",
            payload={"case_path": str(case_path), "manifest_case": case_spec.to_dict()},
            turn_id="turn-0001",
        )
        verdict = {"status": "error", "failures": [], "errors": [{"error": str(exc), "error_type": type(exc).__name__}]}
        metrics = {"event_count": len(store.list_by_run()), "message_count": 0, "tool_call_count": 0, "turn_count": 1}
        _write_json(Path(ref.run_dir) / "metrics.json", metrics)
        _write_json(Path(ref.run_dir) / "verdict.json", verdict)
        _write_json(
            Path(ref.run_dir) / "result.json",
            {
                **ref.to_dict(),
                "status": "error",
                "final_answer": "",
                "error": str(exc),
                "event_count": metrics["event_count"],
                "message_count": 0,
            },
        )
        self.session_manager.finish_case_run(ref, "error")
        return {
            "path": case_spec.path,
            "resolved_path": str(case_path),
            "session_mode": case_spec.session_mode,
            "session_id": ref.session_id,
            "case_id": ref.case_id,
            "case_run_id": ref.case_run_id,
            "run_dir": ref.run_dir,
            "status": "error",
            "metrics": metrics,
            "verdict": verdict,
            "error": str(exc),
        }

    def _new_regression_run_id(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return f"regression-{stamp}"


def _case_summary(case_spec: RegressionCaseSpec, case_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": case_spec.path,
        "resolved_path": str(case_path),
        "session_mode": case_spec.session_mode,
        "session_id": payload["session_id"],
        "case_id": payload["case_id"],
        "case_run_id": payload["case_run_id"],
        "run_dir": payload["run_dir"],
        "status": payload["status"],
        "metrics": payload.get("metrics", {}),
        "verdict": payload.get("verdict", {}),
        "error": payload.get("error"),
    }


def _resolve_case_path(case_path: str, *, workspace_root: Path, manifest_path: Path) -> Path:
    path = Path(case_path).expanduser()
    if not path.is_absolute():
        root_relative = (workspace_root / path).resolve()
        if root_relative.exists():
            return root_relative
        return (manifest_path.parent / path).resolve()
    return path.resolve()


def _combined_status(cases: list[dict[str, Any]]) -> RegressionStatus:
    if not cases:
        return "skipped"
    statuses = {case.get("status") for case in cases}
    if "error" in statuses:
        return "error"
    if "failed" in statuses:
        return "failed"
    if statuses == {"skipped"}:
        return "skipped"
    return "passed"


def _result_payload(
    *,
    run_id: str,
    run_dir: Path,
    manifest_path: Path,
    status: RegressionStatus,
    total: int,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = {
        "total": total,
        "passed": sum(1 for case in cases if case.get("status") == "passed"),
        "failed": sum(1 for case in cases if case.get("status") == "failed"),
        "error": sum(1 for case in cases if case.get("status") == "error"),
        "skipped": sum(1 for case in cases if case.get("status") == "skipped") + max(total - len(cases), 0),
    }
    return {
        "regression_run_id": run_id,
        "run_dir": str(run_dir),
        "manifest": str(manifest_path.resolve()),
        "result_path": str(run_dir / "result.json"),
        "case_runs_path": str(run_dir / "case_runs.jsonl"),
        "status": status,
        **counts,
        "cases": cases,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
