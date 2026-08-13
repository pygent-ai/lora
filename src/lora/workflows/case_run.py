from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from lora.core.io import write_json
from lora.evaluation.case import CaseManager
from lora.evaluation.evaluator import Evaluator
from lora.runtime.service import LoraRuntimeService
from lora.schema import CaseDefinition, CaseRunResult, RunConfig, SessionSpec
from lora.sessions import SessionManager


def execute_case_run(
    *,
    config: RunConfig,
    manager: SessionManager,
    case_manager: CaseManager,
    case_file: str | Path,
    session_id: str | None = None,
    session_mode: str | None = None,
    evaluator: Evaluator | None = None,
) -> dict[str, Any]:
    """Run and evaluate one case through the runtime application boundary."""

    case = case_manager.load(case_file)
    if session_mode is not None:
        case.session["mode"] = session_mode
    session = load_case_session(manager, case, session_id)

    ref = manager.start_case_run(session.session_id, case.id, run_config=config)
    run_dir = Path(ref.run_dir)
    shutil.copy2(case_file, run_dir / "case.yaml")
    case_manager.prepare_workspace(case, ref)

    async def managed_case() -> CaseRunResult:
        runtime = LoraRuntimeService(config)
        try:
            payload = await runtime.execute_case(
                manager=manager,
                session=session,
                case=case,
                run_ref=ref,
            )
            payload.pop("runtime_execution_id", None)
            payload.pop("turn_id", None)
            return CaseRunResult.from_dict(payload)
        finally:
            await runtime.close(cancel=True)

    result = asyncio.run(managed_case())
    evaluation = (evaluator or Evaluator()).evaluate(case, ref)
    if result.status != "error":
        result.status = evaluation.status  # type: ignore[assignment]
    write_json(run_dir / "result.json", result.to_dict())
    manager.finish_case_run(ref, result.status)
    return {
        **ref.to_dict(),
        **result.to_dict(),
        "metrics": evaluation.metrics,
        "verdict": evaluation.verdict,
    }


def load_case_session(
    manager: SessionManager,
    case: CaseDefinition,
    cli_session_id: str | None,
):
    case_session = case.session
    mode = str(case_session.get("mode") or "new")
    if mode not in {"new", "resume", "fork", "shared"}:
        raise ValueError("case.session.mode must be one of new, resume, fork, shared")

    if cli_session_id:
        if mode == "fork":
            return manager.load_or_create(
                SessionSpec(case_id=case.id, mode="fork", source_session_id=cli_session_id)
            )
        return manager.load_or_create(
            SessionSpec(case_id=case.id, mode="resume", session_id=cli_session_id)
        )

    if mode == "new":
        return manager.load(manager.create(case.id, mode=case.type).session_id)
    if mode in {"resume", "shared"}:
        session_id = case_session.get("session_id")
        return manager.load_or_create(
            SessionSpec(case_id=case.id, mode=mode, session_id=session_id)
        )

    source_session_id = case_session.get("source_session_id")
    return manager.load_or_create(
        SessionSpec(case_id=case.id, mode="fork", source_session_id=source_session_id)
    )
