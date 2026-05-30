from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .analysis import FailureAnalyzer
from .case import CaseManager
from .config import load_run_config
from .evaluation import Evaluator
from .regression import RegressionRunner
from .repair import RepairWorkflow
from .runner import execute_case_run
from .runtime import AgentRuntimeAdapter, RuntimeMessage
from .session import SessionManager
from .test_generation import RegressionRegistrar, TestGenerator
from .trace import EventStore


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
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
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
    parser.add_argument("--config", default=None, help="Path to lora.yaml.")
    parser.add_argument("--agent", dest="agent_alias", default=None, help="Agent profile alias.")
    parser.add_argument("--model", default=None, help="Model override.")
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
    config = load_run_config(
        workspace_root=args.workspace_root,
        config_file=args.config,
        session_id=getattr(args, "session_id", None),
        model=args.model,
        agent_alias=args.agent_alias,
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
            streamed = False
            pending_delta_text = ""

            def print_chunk(chunk: str) -> None:
                nonlocal pending_delta_text, streamed
                streamed = True
                pending_delta_text += chunk
                print(chunk, end="", flush=True)

            def print_runtime_message(message: RuntimeMessage) -> None:
                nonlocal pending_delta_text, streamed
                lines = _format_runtime_message_for_chat(message, pending_delta_text=pending_delta_text)
                if message.role == "assistant":
                    pending_delta_text = ""
                if not lines:
                    return
                if streamed:
                    print()
                for line in lines:
                    print(line)
                streamed = True

            result = asyncio.run(
                adapter.run_turn(
                    session=session,
                    user_input=user_input,
                    case_run_ref=run_ref,
                    turn_id=f"turn-{turn_index:04d}",
                    on_assistant_delta=print_chunk,
                    on_runtime_message=print_runtime_message,
                )
            )
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
    return None


def _format_runtime_message_for_chat(
    message: RuntimeMessage,
    *,
    pending_delta_text: str = "",
    max_tool_result_lines: int = 5,
) -> list[str]:
    if message.role == "assistant":
        lines: list[str] = []
        content = message.content.strip()
        if content and content != pending_delta_text:
            lines.extend(content.splitlines())
        for tool_call in _message_tool_calls(message):
            name = _tool_call_name(tool_call)
            arguments = _tool_call_arguments(tool_call)
            suffix = f" {arguments}" if arguments else ""
            lines.append(f"[tool call] {name}{suffix}")
        return lines

    if message.role != "tool":
        return []

    payload = _parse_json_object(message.content)
    status = str(payload.get("status") or "result") if payload else "result"
    tool_call_id = str((message.payload or {}).get("tool_call_id") or payload.get("tool_call_id") or "").strip()
    heading = f"[tool result] {tool_call_id} {status}".strip()
    preview_source = payload.get("result") if payload and "result" in payload else message.content
    if payload and payload.get("error"):
        preview_source = payload["error"]
    preview_lines = _preview_lines(preview_source, max_lines=max_tool_result_lines)
    return [heading, *preview_lines]


def _message_tool_calls(message: RuntimeMessage) -> list[dict[str, Any]]:
    payload = message.payload or {}
    tool_calls = payload.get("tool_calls") or []
    return [tool_call for tool_call in tool_calls if isinstance(tool_call, dict)]


def _tool_call_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(tool_call.get("name") or tool_call.get("tool_name") or "tool")


def _tool_call_arguments(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("arguments") is not None:
        return str(function["arguments"])
    arguments = tool_call.get("arguments")
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _preview_lines(value: Any, *, max_lines: int) -> list[str]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    lines = text.splitlines() or [text]
    if len(lines) > max_lines:
        return [*lines[: max_lines - 1], "..."]
    return lines[:max_lines]


def _manager(args: argparse.Namespace) -> SessionManager:
    return SessionManager(
        load_run_config(
            workspace_root=args.workspace_root,
            config_file=args.config,
            model=args.model,
            agent_alias=args.agent_alias,
            max_steps=args.max_steps,
        )
    )


def _repair_workflow(args: argparse.Namespace) -> RepairWorkflow:
    manager = _manager(args)
    return RepairWorkflow(config=manager.config, session_manager=manager)


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
    return FailureAnalyzer().analyze(verdict=verdict, events=events, run_dir=Path(".")).to_dict()["root_causes"]
