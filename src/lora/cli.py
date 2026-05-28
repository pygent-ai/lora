from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

from .case import CaseManager
from .config import load_run_config
from .evaluation import Evaluator
from .runtime import AgentRuntimeAdapter
from .schema import SessionSpec
from .session import SessionManager
from .trace import EventStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as exc:  # noqa: BLE001 - CLI boundary should render readable errors.
        print(f"lora: error: {exc}", file=sys.stderr)
        return 2
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lora", description="Agent self-optimization harness CLI")
    parser.add_argument("--workspace-root", default=None, help="Workspace root. Defaults to cwd or LORA_WORKSPACE_ROOT.")
    parser.add_argument("--config", default=None, help="Path to lora.yaml.")
    parser.add_argument("--model", default=None, help="Model override.")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum agent steps.")

    sub = parser.add_subparsers(dest="command", required=True)
    session = sub.add_parser("session", help="Manage sessions")
    session_sub = session.add_subparsers(dest="session_command", required=True)

    create = session_sub.add_parser("create", help="Create a session")
    create.add_argument("--case", required=True, dest="case_id")
    create.add_argument("--mode", default="e2e")
    create.set_defaults(handler=_session_create)

    show = session_sub.add_parser("show", help="Show a session")
    show.add_argument("session_id")
    show.set_defaults(handler=_session_show)

    resume = session_sub.add_parser("resume", help="Validate that a session can be resumed")
    resume.add_argument("session_id")
    resume.set_defaults(handler=_session_show)

    case = sub.add_parser("case", help="Run or inspect cases")
    case_sub = case.add_subparsers(dest="case_command", required=True)

    run = case_sub.add_parser("run", help="Create a case run under a session")
    run.add_argument("case_file")
    run.add_argument("--session", dest="session_id", default=None)
    run.set_defaults(handler=_case_run)

    analyze = case_sub.add_parser("analyze", help="Analyze a case run")
    analyze.add_argument("session_id")
    analyze.add_argument("case_run_id")
    analyze.set_defaults(handler=_case_analyze)

    replay = case_sub.add_parser("replay", help="Replay a case run")
    replay.add_argument("session_id")
    replay.add_argument("case_run_id")
    replay.set_defaults(handler=_case_replay)

    regression = sub.add_parser("regression", help="Run regression suite")
    regression_sub = regression.add_subparsers(dest="regression_command", required=True)
    regression_run = regression_sub.add_parser("run")
    regression_run.set_defaults(handler=_regression_run)

    optimize = sub.add_parser("optimize", help="Run full optimize flow")
    optimize.add_argument("case_file")
    optimize.set_defaults(handler=_optimize)

    chat = sub.add_parser("chat", help="Chat with an agent")
    chat.add_argument("-m", "--message", default=None, help="Run one chat turn and print the result as JSON.")
    chat.add_argument("--session", dest="session_id", default=None, help="Resume an existing session.")
    chat.add_argument("--new", action="store_true", help="Start a new chat session even when config has a session_id.")
    chat.set_defaults(handler=_chat)
    return parser


def _session_create(args: argparse.Namespace) -> dict[str, Any]:
    manager = _manager(args)
    return manager.create(args.case_id, mode=args.mode).to_dict()


def _session_show(args: argparse.Namespace) -> dict[str, Any]:
    return _manager(args).show(args.session_id)


def _case_run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_run_config(
        workspace_root=args.workspace_root,
        config_file=args.config,
        session_id=getattr(args, "session_id", None),
        case_file=args.case_file,
        model=args.model,
        max_steps=args.max_steps,
    )
    manager = SessionManager(config)
    case_manager = CaseManager(config.workspace_root)
    case = case_manager.load(args.case_file)
    session = _load_case_session(manager, case, getattr(args, "session_id", None))
    session_id = session.session_id

    ref = manager.start_case_run(session_id, case.id, run_config=config)
    run_dir = Path(ref.run_dir)
    shutil.copy2(config.case_file or args.case_file, run_dir / "case.yaml")
    case_manager.prepare_workspace(case, ref)
    result = asyncio.run(
        AgentRuntimeAdapter(config=config, session_manager=manager).run_case(
            session=session,
            case=case,
            case_run_ref=ref,
        )
    )
    evaluation = Evaluator().evaluate(case, ref)
    if result.status != "error":
        result.status = evaluation.status  # type: ignore[assignment]
    (run_dir / "result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manager.finish_case_run(ref, result.status)
    return {
        **ref.to_dict(),
        **result.to_dict(),
        "metrics": evaluation.metrics,
        "verdict": evaluation.verdict,
    }


def _case_replay(args: argparse.Namespace) -> dict[str, Any]:
    ref = _find_case_run(_manager(args), args.session_id, args.case_run_id)
    events = [event.to_dict() for event in EventStore(ref).list_by_run()]
    return {"session_id": args.session_id, "case_run_id": args.case_run_id, "events": events}


def _case_analyze(args: argparse.Namespace) -> dict[str, Any]:
    ref = _find_case_run(_manager(args), args.session_id, args.case_run_id)
    run_dir = Path(ref.run_dir)
    verdict_path = run_dir / "verdict.json"
    if not verdict_path.exists():
        case = CaseManager(_manager(args).workspace_root).load(run_dir / "case.yaml")
        Evaluator().evaluate(case, ref)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    events = EventStore(ref).list_by_run()
    root_causes = _root_causes(verdict, events)
    analysis = {
        "session_id": ref.session_id,
        "case_id": ref.case_id,
        "case_run_id": ref.case_run_id,
        "status": verdict.get("status", "error"),
        "root_causes": root_causes,
    }
    (run_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    EventStore(ref).append("analysis.created", actor="system", payload=analysis, turn_id=None)
    return analysis


def _regression_run(args: argparse.Namespace) -> dict[str, Any]:
    manager = _manager(args)
    manifest = Path(manager.config.lora_root) / "regression.json"
    if not manifest.exists():
        return {"status": "skipped", "reason": "regression manifest not found", "manifest": str(manifest)}
    return {"status": "skipped", "reason": "regression execution is not configured in MVP", "manifest": str(manifest)}


def _optimize(args: argparse.Namespace) -> dict[str, Any]:
    run_payload = _case_run(args)
    if run_payload["status"] == "passed":
        return {"run": run_payload, "analysis": None}
    analysis_args = argparse.Namespace(
        **{
            **vars(args),
            "session_id": run_payload["session_id"],
            "case_run_id": run_payload["case_run_id"],
        }
    )
    return {"run": run_payload, "analysis": _case_analyze(analysis_args)}


def _chat(args: argparse.Namespace) -> dict[str, Any] | None:
    config = load_run_config(
        workspace_root=args.workspace_root,
        config_file=args.config,
        session_id=getattr(args, "session_id", None),
        model=args.model,
        max_steps=args.max_steps,
    )
    manager = SessionManager(config)
    session_id = None if args.new else (getattr(args, "session_id", None) or config.session_id)
    if session_id is None:
        session_id = manager.create("chat", mode="chat").session_id

    session = manager.load(session_id)
    run_ref = manager.start_case_run(session_id, "chat", run_config=config)
    store = EventStore(run_ref)
    store.append("chat.started", actor="system", payload={"interactive": args.message is None}, turn_id=None)
    adapter = AgentRuntimeAdapter(config=config, session_manager=manager)
    status = "passed"

    try:
        if args.message is not None:
            result = asyncio.run(
                adapter.run_turn(
                    session=session,
                    user_input=args.message,
                    case_run_ref=run_ref,
                    turn_id="turn-0001",
                )
            )
            status = result["status"]
            return {**run_ref.to_dict(), **result}

        print(f"lora chat session: {session_id}")
        print("Type /exit or /quit to end.")
        turn_index = 1
        while True:
            try:
                user_input = input("> ")
            except EOFError:
                break
            if user_input.strip() in {"/exit", "/quit"}:
                break
            if not user_input.strip():
                continue
            result = asyncio.run(
                adapter.run_turn(
                    session=session,
                    user_input=user_input,
                    case_run_ref=run_ref,
                    turn_id=f"turn-{turn_index:04d}",
                )
            )
            status = result["status"]
            if result["final_answer"]:
                print(result["final_answer"])
            if result["error"]:
                print(f"agent error: {result['error']}", file=sys.stderr)
                break
            turn_index += 1
    finally:
        store.append("chat.finished", actor="system", payload={"status": status}, turn_id=None)
        manager.finish_case_run(run_ref, status)
    return None


def _manager(args: argparse.Namespace) -> SessionManager:
    return SessionManager(
        load_run_config(
            workspace_root=args.workspace_root,
            config_file=args.config,
            model=args.model,
            max_steps=args.max_steps,
        )
    )


def _load_case_session(manager: SessionManager, case: Any, cli_session_id: str | None):
    case_session = case.session
    mode = str(case_session.get("mode") or "new")
    if mode not in {"new", "resume", "fork", "shared"}:
        raise ValueError("case.session.mode must be one of new, resume, fork, shared")

    if cli_session_id:
        if mode == "fork":
            return manager.load_or_create(
                SessionSpec(case_id=case.id, mode="fork", source_session_id=cli_session_id)
            )
        return manager.load_or_create(SessionSpec(case_id=case.id, mode="resume", session_id=cli_session_id))

    if mode == "new":
        return manager.load(manager.create(case.id, mode=case.type).session_id)
    if mode in {"resume", "shared"}:
        session_id = case_session.get("session_id") or case_session.get("id")
        return manager.load_or_create(SessionSpec(case_id=case.id, mode=mode, session_id=session_id))

    source_session_id = (
        case_session.get("source_session_id")
        or case_session.get("source")
        or case_session.get("session_id")
        or case_session.get("id")
    )
    return manager.load_or_create(
        SessionSpec(case_id=case.id, mode="fork", source_session_id=source_session_id)
    )


def _find_case_run(manager: SessionManager, session_id: str, case_run_id: str):
    session_dir = Path(manager.show(session_id)["session"]["session_dir"])
    matches = list((session_dir / "cases").glob(f"*/runs/{case_run_id}"))
    if not matches:
        raise FileNotFoundError(f"Case run {case_run_id!r} does not exist under session {session_id!r}")
    run_dir = matches[0]
    metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    from .schema import CaseRunRef

    return CaseRunRef(
        session_id=session_id,
        case_id=metadata["case_id"],
        case_run_id=case_run_id,
        run_dir=str(run_dir),
    )


def _root_causes(verdict: dict[str, Any], events: list[Any]) -> list[dict[str, Any]]:
    if verdict.get("status") == "passed":
        return []
    if verdict.get("errors"):
        return [
            {
                "type": "TOOL_EXECUTION_ERROR" if any(event.type == "tool.result" for event in events) else "ASSERTION_FAILED",
                "summary": "Run produced an error event.",
                "evidence": [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in verdict["errors"]],
                "suspected_modules": ["runtime"],
                "recommended_tests": [{"kind": "scenario", "description": "Replay the failing case and assert the error is preserved."}],
            }
        ]
    return [
        {
            "type": "ASSERTION_FAILED",
            "summary": "Run output did not satisfy deterministic case assertions.",
            "evidence": [failure.get("message", str(failure)) for failure in verdict.get("failures", [])],
            "suspected_modules": ["evaluation", "runtime"],
            "recommended_tests": [{"kind": "unit", "description": "Cover the failed assertion in Evaluator."}],
        }
    ]
