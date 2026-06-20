from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from lora.evaluation import CaseManager
from lora.core.io import read_json, write_json
from lora.evaluation import RegressionRunner
from lora.schema import CaseRunRef, RunConfig
from lora.sessions import SessionManager
from lora.tracing import EventStore


RepairStatus = Literal["planned", "skipped"]
GateStatus = Literal["passed", "failed", "error", "skipped"]


@dataclass(slots=True)
class RepairPlan:
    repair_id: str
    session_id: str
    case_id: str
    case_run_id: str
    status: RepairStatus
    failure_summary: str
    suspected_files: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    root_causes: list[dict[str, Any]] = field(default_factory=list)
    recommended_checks: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    plan_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepairPlan":
        return cls(
            repair_id=str(data["repair_id"]),
            session_id=str(data["session_id"]),
            case_id=str(data["case_id"]),
            case_run_id=str(data["case_run_id"]),
            status=data.get("status", "planned"),
            failure_summary=str(data.get("failure_summary", "")),
            suspected_files=list(data.get("suspected_files") or []),
            failures=list(data.get("failures") or []),
            errors=list(data.get("errors") or []),
            root_causes=list(data.get("root_causes") or []),
            recommended_checks=list(data.get("recommended_checks") or []),
            gate=dict(data.get("gate") or {}),
            plan_path=data.get("plan_path"),
            created_at=str(data.get("created_at") or datetime.now().astimezone().isoformat()),
        )


class RepairWorkflow:
    def __init__(self, *, config: RunConfig, session_manager: SessionManager) -> None:
        self.config = config
        self.session_manager = session_manager
        self.workspace_root = Path(config.workspace_root)
        self.lora_root = Path(config.lora_root)

    def plan(self, session_id: str, case_run_id: str) -> dict[str, Any]:
        ref = self.find_case_run(session_id, case_run_id)
        run_dir = Path(ref.run_dir)
        verdict = _read_json(run_dir / "verdict.json", default={})
        status = str(verdict.get("status") or _read_json(run_dir / "run_metadata.json", default={}).get("status") or "error")
        if status == "passed":
            return {
                "status": "skipped",
                "reason": "case run already passed",
                "session_id": session_id,
                "case_run_id": case_run_id,
            }

        repair_id = self._new_repair_id(case_run_id)
        repair_dir = self._repair_dir(session_id, repair_id)
        analysis = _read_json(run_dir / "analysis.json", default={})
        events = list(EventStore(ref).iter_jsonl(run_dir / "events.jsonl"))
        plan = RepairPlan(
            repair_id=repair_id,
            session_id=session_id,
            case_id=ref.case_id,
            case_run_id=case_run_id,
            status="planned",
            failure_summary=_failure_summary(verdict, analysis),
            suspected_files=_suspected_files(verdict, analysis, events),
            failures=list(verdict.get("failures") or []),
            errors=list(verdict.get("errors") or []),
            root_causes=list(analysis.get("root_causes") or []),
            recommended_checks=_recommended_checks(verdict, analysis, self.workspace_root),
            gate=_gate_config(self.workspace_root, self.lora_root),
        )
        plan.plan_path = str(repair_dir / "repair_plan.json")
        write_json(repair_dir / "repair_plan.json", plan.to_dict())
        EventStore(ref).append(
            "repair.started",
            actor="system",
            payload={
                "repair_id": repair_id,
                "repair_plan_path": plan.plan_path,
                "source_case_run_id": case_run_id,
                "status": plan.status,
            },
            turn_id=None,
        )
        return plan.to_dict()

    def apply(self, repair_plan_path: str | Path) -> dict[str, Any]:
        plan_path = Path(repair_plan_path).expanduser().resolve()
        plan = RepairPlan.from_dict(_read_json(plan_path))
        ref = self.find_case_run(plan.session_id, plan.case_run_id)
        attempt_id = self._new_attempt_id(plan.repair_id)
        attempt_dir = self._repair_dir(plan.session_id, plan.repair_id) / "attempts" / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=False)

        diff = _git_diff(self.workspace_root)
        patch_path = attempt_dir / "patch.diff"
        patch_path.write_text(diff["stdout"], encoding="utf-8")
        metadata = {
            "attempt_id": attempt_id,
            "repair_id": plan.repair_id,
            "session_id": plan.session_id,
            "case_id": plan.case_id,
            "case_run_id": plan.case_run_id,
            "repair_plan_path": str(plan_path),
            "patch_path": str(patch_path),
            "created_at": datetime.now().astimezone().isoformat(),
            "diff_command": diff["command"],
            "diff_exit_code": diff["exit_code"],
            "diff_stderr": _summarize(diff["stderr"]),
        }
        write_json(attempt_dir / "metadata.json", metadata)
        EventStore(ref).append(
            "repair.patch_created",
            actor="system",
            payload={
                "repair_id": plan.repair_id,
                "attempt_id": attempt_id,
                "repair_plan_path": str(plan_path),
                "patch_path": str(patch_path),
                "patch_bytes": len(diff["stdout"].encode("utf-8")),
                "diff_exit_code": diff["exit_code"],
            },
            turn_id=None,
        )
        return {**metadata, "patch_diff": str(patch_path)}

    def gate(self, repair_attempt_id: str) -> dict[str, Any]:
        attempt_dir = self.find_attempt_dir(repair_attempt_id)
        metadata = _read_json(attempt_dir / "metadata.json")
        plan = RepairPlan.from_dict(_read_json(metadata["repair_plan_path"]))
        ref = self.find_case_run(plan.session_id, plan.case_run_id)

        commands = _normalise_commands(plan.gate.get("commands"))
        regression_manifest = self.lora_root / "regression.json"
        results: list[dict[str, Any]] = []
        if commands:
            for command in commands:
                results.append(_run_command(command, cwd=self.workspace_root))
            status = _gate_status(results)
        elif regression_manifest.exists():
            regression = RegressionRunner(
                config=self.config,
                session_manager=self.session_manager,
                case_manager=CaseManager(self.workspace_root),
            ).run(regression_manifest)
            status = regression["status"]
            results.append({"kind": "regression", "status": status, "result": regression})
        else:
            status = "skipped"
            results.append({"kind": "none", "status": "skipped", "reason": "no gate commands or regression manifest found"})

        gate_result = {
            "attempt_id": repair_attempt_id,
            "repair_id": plan.repair_id,
            "session_id": plan.session_id,
            "case_run_id": plan.case_run_id,
            "status": status,
            "commands": results,
            "created_at": datetime.now().astimezone().isoformat(),
        }
        write_json(attempt_dir / "gate_result.json", gate_result)
        EventStore(ref).append(
            "repair.finished",
            actor="system",
            payload={
                "repair_id": plan.repair_id,
                "attempt_id": repair_attempt_id,
                "status": status,
                "gate_result_path": str(attempt_dir / "gate_result.json"),
            },
            turn_id=None,
        )
        return gate_result

    def find_case_run(self, session_id: str, case_run_id: str) -> CaseRunRef:
        return self.session_manager.find_case_run(session_id, case_run_id)

    def find_attempt_dir(self, attempt_id: str) -> Path:
        matches = list((self.lora_root / "sessions").glob(f"*/repairs/*/attempts/{attempt_id}"))
        if not matches:
            raise FileNotFoundError(f"Repair attempt {attempt_id!r} does not exist")
        if len(matches) > 1:
            raise ValueError(f"Repair attempt {attempt_id!r} is ambiguous")
        return matches[0]

    def _repair_dir(self, session_id: str, repair_id: str) -> Path:
        session_dir = Path(self.session_manager.show(session_id)["session"]["session_dir"])
        path = session_dir / "repairs" / repair_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _new_repair_id(case_run_id: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return f"repair-{case_run_id}-{stamp}"

    @staticmethod
    def _new_attempt_id(repair_id: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return f"attempt-{repair_id}-{stamp}"


def _failure_summary(verdict: dict[str, Any], analysis: dict[str, Any]) -> str:
    root_causes = analysis.get("root_causes") or []
    if root_causes:
        return str(root_causes[0].get("summary") or root_causes[0].get("type") or "Run failed")
    failures = verdict.get("failures") or []
    if failures:
        return "; ".join(str(item.get("message", item)) for item in failures[:3])
    errors = verdict.get("errors") or []
    if errors:
        return "; ".join(str(item.get("error", item)) for item in errors[:3])
    return f"Run status is {verdict.get('status', 'unknown')}"


def _suspected_files(verdict: dict[str, Any], analysis: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    files: list[str] = []
    for root_cause in analysis.get("root_causes") or []:
        for value in root_cause.get("suspected_files") or []:
            if isinstance(value, str):
                files.append(value)
    for item in list(verdict.get("failures") or []) + list(verdict.get("errors") or []):
        for key in ("path", "file", "actual_path", "expected_path"):
            value = item.get(key) if isinstance(item, dict) else None
            if isinstance(value, str):
                files.append(value)
    for event in events:
        if str(event.get("type", "")).startswith("file."):
            value = (event.get("payload") or {}).get("path")
            if isinstance(value, str):
                files.append(value)
    return sorted(dict.fromkeys(files))


def _recommended_checks(verdict: dict[str, Any], analysis: dict[str, Any], workspace_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for root_cause in analysis.get("root_causes") or []:
        checks.extend(item for item in root_cause.get("recommended_tests") or [] if isinstance(item, dict))
    if (workspace_root / "tests").exists():
        checks.append(
            {
                "kind": "command",
                "description": "Run the local unit and scenario suite.",
                "command": [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            }
        )
    if not checks and verdict.get("failures"):
        checks.append({"kind": "manual", "description": "Add or run a deterministic check for the failed assertion."})
    return checks


def _gate_config(workspace_root: Path, lora_root: Path) -> dict[str, Any]:
    repair_manifest = lora_root / "repair.json"
    if repair_manifest.exists():
        data = _read_json(repair_manifest)
        return {"commands": data.get("commands", [])}
    if (lora_root / "regression.json").exists():
        return {"regression_manifest": str(lora_root / "regression.json")}
    if (workspace_root / "tests").exists():
        return {"commands": [[sys.executable, "-m", "unittest", "discover", "-s", "tests"]]}
    return {"commands": []}


def _normalise_commands(value: Any) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("gate.commands must be a list")
    commands: list[list[str]] = []
    for item in value:
        if isinstance(item, str):
            commands.append([item])
        elif isinstance(item, list) and all(isinstance(part, str) for part in item):
            commands.append(list(item))
        elif isinstance(item, dict) and isinstance(item.get("command"), list):
            command = item["command"]
            if not all(isinstance(part, str) for part in command):
                raise ValueError("gate command parts must be strings")
            commands.append(list(command))
        else:
            raise ValueError("gate.commands entries must be strings, string lists, or {command: [...]}")
    return commands


def _run_command(command: Sequence[str], *, cwd: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=300, check=False)
        status: GateStatus = "passed" if completed.returncode == 0 else "failed"
        return {
            "kind": "command",
            "command": list(command),
            "status": status,
            "exit_code": completed.returncode,
            "stdout": _summarize(completed.stdout),
            "stderr": _summarize(completed.stderr),
        }
    except Exception as exc:  # noqa: BLE001 - gate result must preserve command failures.
        return {
            "kind": "command",
            "command": list(command),
            "status": "error",
            "exit_code": None,
            "stdout": "",
            "stderr": _summarize(str(exc)),
        }


def _gate_status(results: list[dict[str, Any]]) -> GateStatus:
    if not results:
        return "skipped"
    statuses = {item.get("status") for item in results}
    if "error" in statuses:
        return "error"
    if "failed" in statuses:
        return "failed"
    if statuses == {"skipped"}:
        return "skipped"
    return "passed"


def _git_diff(workspace_root: Path) -> dict[str, Any]:
    command = ["git", "diff", "--binary", "--no-ext-diff"]
    try:
        completed = subprocess.run(command, cwd=workspace_root, capture_output=True, text=True, check=False)
        return {
            "command": command,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except OSError as exc:
        return {"command": command, "exit_code": 127, "stdout": "", "stderr": str(exc)}


def _summarize(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _read_json(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    return read_json(path, default=default)
