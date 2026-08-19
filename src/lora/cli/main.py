from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from lora.evaluation import FailureAnalyzer
from lora.cli.credentials import register_credentials_parser
from lora.evaluation import CaseManager
from lora.config import load_run_config
from lora.evaluation import Evaluator
from lora.evaluation import RegressionRunner
from lora.repair import RepairWorkflow
from lora.workflows import execute_case_run
from lora.runtime.service import LoraRuntimeService
from pygent import thaw_json
from lora.sessions import SessionManager
from lora.evaluation import RegressionRegistrar, TestGenerator
from lora.tracing import EventStore


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
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


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lora", description="Agent self-optimization harness CLI")
    parser.add_argument("--workspace-root", default=None, help="Workspace root. Defaults to cwd or LORA_WORKSPACE_ROOT.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    parser.add_argument("--agent", dest="agent_alias", default=None, help="Agent profile alias.")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum agent steps; -1 means unlimited.")

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

    test = sub.add_parser("test", help="Generate and register regression tests")
    test_sub = test.add_subparsers(dest="test_command", required=True)

    test_generate = test_sub.add_parser("generate", help="Generate a deterministic regression case from a failed run")
    test_generate.add_argument("session_id")
    test_generate.add_argument("case_run_id")
    test_generate.set_defaults(handler=_test_generate)

    test_register = test_sub.add_parser("register", help="Register a case file in the regression manifest")
    test_register.add_argument("case_file")
    test_register.set_defaults(handler=_test_register)

    repair = sub.add_parser("repair", help="Plan, capture, and gate repair attempts")
    repair_sub = repair.add_subparsers(dest="repair_command", required=True)

    repair_plan = repair_sub.add_parser("plan", help="Create a deterministic repair plan for a failed run")
    repair_plan.add_argument("session_id")
    repair_plan.add_argument("case_run_id")
    repair_plan.set_defaults(handler=_repair_plan)

    repair_apply = repair_sub.add_parser("apply", help="Capture the current workspace diff as a repair attempt")
    repair_apply.add_argument("repair_plan_path")
    repair_apply.set_defaults(handler=_repair_apply)

    repair_gate = repair_sub.add_parser("gate", help="Run checks for a repair attempt")
    repair_gate.add_argument("repair_attempt_id")
    repair_gate.set_defaults(handler=_repair_gate)

    optimize = sub.add_parser("optimize", help="Run full optimize flow")
    optimize.add_argument("case_file")
    optimize.set_defaults(handler=_optimize)

    chat = sub.add_parser("chat", help="Chat with an agent")
    chat.add_argument("-m", "--message", default=None, help="Run one chat turn and print the result as JSON.")
    chat.add_argument("--session", dest="session_id", default=None, help="Resume an existing session.")
    chat.add_argument("--new", action="store_true", help="Start a new chat session even when config has a session_id.")
    chat.set_defaults(handler=_chat)

    register_credentials_parser(sub)
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
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )
    manager = SessionManager(config)
    case_manager = CaseManager(config.workspace_root)
    return execute_case_run(
        config=config,
        manager=manager,
        case_manager=case_manager,
        case_file=config.case_file or args.case_file,
        session_id=getattr(args, "session_id", None),
    )


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
    result = FailureAnalyzer().analyze(verdict=verdict, events=events, run_dir=run_dir).to_dict()
    analysis = {
        "session_id": ref.session_id,
        "case_id": ref.case_id,
        "case_run_id": ref.case_run_id,
        **result,
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
    return RegressionRunner(
        config=manager.config,
        session_manager=manager,
        case_manager=CaseManager(manager.config.workspace_root),
        evaluator=Evaluator(),
    ).run(manifest)


def _test_generate(args: argparse.Namespace) -> dict[str, Any]:
    manager = _manager(args)
    return TestGenerator(config=manager.config, session_manager=manager).generate(
        args.session_id,
        args.case_run_id,
    ).to_dict()


def _test_register(args: argparse.Namespace) -> dict[str, Any]:
    manager = _manager(args)
    return RegressionRegistrar(config=manager.config).register(args.case_file)


def _repair_plan(args: argparse.Namespace) -> dict[str, Any]:
    return _repair_workflow(args).plan(args.session_id, args.case_run_id)


def _repair_apply(args: argparse.Namespace) -> dict[str, Any]:
    return _repair_workflow(args).apply(args.repair_plan_path)


def _repair_gate(args: argparse.Namespace) -> dict[str, Any]:
    return _repair_workflow(args).gate(args.repair_attempt_id)


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
    return asyncio.run(_chat_async(args))


async def _chat_async(args: argparse.Namespace) -> dict[str, Any] | None:
    config = load_run_config(
        workspace_root=args.workspace_root,
        config_file=args.config,
        session_id=getattr(args, "session_id", None),
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )
    manager = SessionManager(config)
    session_id = None if args.new else (getattr(args, "session_id", None) or config.session_id)
    if session_id is None:
        session_id = manager.create("chat", mode="chat").session_id

    run_ref = manager.start_case_run(session_id, "chat", run_config=config)
    store = EventStore(run_ref)
    store.append("chat.started", actor="system", payload={"interactive": args.message is None}, turn_id=None)
    runtime = LoraRuntimeService(config)
    await runtime.initialize()
    status = "passed"

    try:
        if args.message is not None:
            handle = await runtime.start_turn(
                manager=manager,
                message=args.message,
                run_ref=run_ref,
                turn_id="turn-0001",
                interactive_approvals=False,
            )
            output, _ = await handle.result()
            result = dict(thaw_json(output.data).get("result") or {})
            result["runtime_execution_id"] = handle.execution_id
            status = result["status"]
            return _chat_message_payload(run_ref, result)

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
            streamed = False
            pending_delta_text = ""

            handle = await runtime.start_turn(
                manager=manager,
                message=user_input,
                run_ref=run_ref,
                turn_id=f"turn-{turn_index:04d}",
                interactive_approvals=True,
            )
            async with handle.subscribe() as execution_events:
                async for event in execution_events:
                    data = dict(thaw_json(event.data))
                    if event.kind == "model.text.delta" and event.module_path.endswith(".react.model.model"):
                        chunk = str(data.get("text") or "")
                        if chunk:
                            streamed = True
                            pending_delta_text += chunk
                            print(chunk, end="", flush=True)
                    elif event.kind == "lora.approval.requested":
                        answer = await asyncio.to_thread(
                            input,
                            f"Approve {data.get('tool_name')} {data.get('arguments')}? [y/N] ",
                        )
                        await runtime.deliver_approval(
                            str(data["approval_id"]),
                            approved=answer.strip().lower() in {"y", "yes"},
                            comment="interactive CLI decision",
                        )
            output, _ = await handle.result()
            result = dict(thaw_json(output.data).get("result") or {})
            status = result["status"]
            if streamed:
                print()
            elif result["final_answer"]:
                print(result["final_answer"])
            if result["error"]:
                print(f"agent error: {result['error']}", file=sys.stderr)
                break
            turn_index += 1
    finally:
        store.append("chat.finished", actor="system", payload={"status": status}, turn_id=None)
        manager.finish_case_run(run_ref, status)
        await runtime.close(cancel=True)
    return None


def _chat_message_payload(run_ref: Any, result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "final_answer": str(result.get("final_answer") or ""),
        "session_id": run_ref.session_id,
        "case_run_id": run_ref.case_run_id,
        "run_dir": str(run_ref.run_dir),
    }
    if result.get("error"):
        payload["error"] = str(result["error"])
    return payload


def _manager(args: argparse.Namespace) -> SessionManager:
    return SessionManager(
        load_run_config(
            workspace_root=args.workspace_root,
            config_file=args.config,
            agent_alias=args.agent_alias,
            max_steps=args.max_steps,
        )
    )


def _repair_workflow(args: argparse.Namespace) -> RepairWorkflow:
    manager = _manager(args)
    return RepairWorkflow(config=manager.config, session_manager=manager)


def _find_case_run(manager: SessionManager, session_id: str, case_run_id: str):
    return manager.find_case_run(session_id, case_run_id)
