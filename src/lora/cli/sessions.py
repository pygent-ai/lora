from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from lora.config import load_run_config
from lora.core.io import plain_object
from lora.orchestration import LocalExecutionHost, ManagedSessionTurn
from lora.schema import RunConfig
from lora.sessions import SessionManager


def register_session_parser(subparsers: Any) -> None:
    session = subparsers.add_parser("session", help="Create, inspect, and run sessions")
    commands = session.add_subparsers(dest="session_command", required=True)

    create = commands.add_parser("create", help="Create a session")
    create.add_argument("--case", required=True, dest="case_id")
    create.add_argument("--mode", default="e2e")
    create.set_defaults(handler=_session_create)

    list_command = commands.add_parser("list", help="List sessions")
    list_command.add_argument("--mode", default=None, help="Filter by session mode")
    list_command.set_defaults(handler=_session_list)

    show = commands.add_parser("show", help="Show a session")
    show.add_argument("session_id")
    show.set_defaults(handler=_session_show)

    run = commands.add_parser("run", help="Run one agent turn and return JSON")
    _add_session_target(run)
    run.add_argument("-m", "--message", required=True, help="Message for the agent")
    run.set_defaults(handler=_session_run)

    chat = commands.add_parser("chat", help="Open an interactive agent chat")
    _add_session_target(chat)
    chat.set_defaults(handler=_session_chat)

    send = commands.add_parser("send", help="Queue a message for an existing session")
    send.add_argument("session_id", help="Target session ID")
    _add_collaboration_submission(send)
    send.set_defaults(handler=_session_send)

    start = commands.add_parser(
        "start", help="Create a session and queue a background task"
    )
    _add_collaboration_submission(start)
    start.set_defaults(handler=_session_start)

    status = commands.add_parser("status", help="Show an operation or agent message")
    status.add_argument("collaboration_id", metavar="operation_or_message_id")
    status.set_defaults(handler=_session_status)

    wait = commands.add_parser("wait", help="Wait for an operation or agent message")
    wait.add_argument("collaboration_id", metavar="operation_or_message_id")
    wait.set_defaults(handler=_session_wait)


def _add_session_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("session_id", nargs="?", help="Existing session ID")
    parser.add_argument("--new", action="store_true", help="Create a new chat session")


def _add_collaboration_submission(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-m", "--message", required=True, help="Message for the target agent"
    )
    parser.add_argument(
        "--submission-id",
        default=None,
        help="Stable idempotency key for retrying the same submission",
    )
    parser.add_argument(
        "--source-session",
        default=None,
        help="Session ID of the sending agent; omit for an external sender",
    )


def _validate_session_target(args: argparse.Namespace) -> None:
    if args.new and args.session_id:
        raise ValueError("session_id and --new cannot be used together")
    if not args.new and not args.session_id:
        raise ValueError("session_id is required unless --new is used")


def _session_create(args: argparse.Namespace) -> dict[str, Any]:
    return _manager(args).create(args.case_id, mode=args.mode).to_dict()


def _session_list(args: argparse.Namespace) -> dict[str, Any]:
    return {"sessions": _manager(args).list_sessions(mode=args.mode)}


def _session_show(args: argparse.Namespace) -> dict[str, Any]:
    return _manager(args).show(args.session_id)


def _session_run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_session_target(args)
    return asyncio.run(_run_session_turn(args))


def _session_send(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_enqueue_agent_message(args))


def _session_start(args: argparse.Namespace) -> dict[str, Any]:
    operation, config = asyncio.run(_enqueue_collaboration_start(args))
    _spawn_collaboration_worker(args, operation.operation_id, config)
    return operation.to_dict()


async def _enqueue_agent_message(args: argparse.Namespace) -> dict[str, Any]:
    config, manager = _collaboration_config_and_manager(args)
    async with LocalExecutionHost() as host:
        message = await host.collaboration.send(
            config=config,
            manager=manager,
            target_session_id=args.session_id,
            message=args.message,
            source_session_id=args.source_session,
            source_agent_alias=(config.agent_alias if args.source_session else None),
            submission_id=args.submission_id,
        )
        return message.to_dict()


async def _enqueue_collaboration_start(
    args: argparse.Namespace,
) -> tuple[Any, RunConfig]:
    config, manager = _collaboration_config_and_manager(args)
    async with LocalExecutionHost() as host:
        values = {
            "config": config,
            "manager": manager,
            "message": args.message,
            "source_session_id": args.source_session,
            "submission_id": args.submission_id,
        }
        operation = await host.collaboration.enqueue_start(**values)
        return operation, config


def _session_status(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_read_collaboration_status(args))


async def _read_collaboration_status(args: argparse.Namespace) -> dict[str, Any]:
    config, _manager = _collaboration_config_and_manager(args)
    async with LocalExecutionHost() as host:
        if args.collaboration_id.startswith("msg-"):
            message = await host.collaboration.message_status(
                config=config,
                message_id=args.collaboration_id,
            )
            return message.to_dict()
        operation = await host.collaboration.status(
            config=config, operation_id=args.collaboration_id
        )
        return operation.to_dict()


def _session_wait(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_wait_for_collaboration(args))


async def _wait_for_collaboration(args: argparse.Namespace) -> dict[str, Any]:
    config, manager = _collaboration_config_and_manager(args)
    async with LocalExecutionHost() as host:
        if args.collaboration_id.startswith("msg-"):
            message = await host.collaboration.wait_message(
                config=config,
                message_id=args.collaboration_id,
            )
            return message.to_dict()
        await host.collaboration.resume_operation(
            config=config,
            manager=manager,
            operation_id=args.collaboration_id,
        )
        operation = await host.collaboration.wait(
            config=config,
            operation_id=args.collaboration_id,
        )
        return operation.to_dict()


def _spawn_collaboration_worker(
    args: argparse.Namespace,
    operation_id: str,
    config: RunConfig,
) -> None:
    command = [
        sys.executable,
        "-m",
        "lora.cli.collaboration_worker",
        "--workspace-root",
        config.workspace_root,
        "--agent",
        config.agent_alias,
    ]
    if args.max_steps is not None:
        command.extend(("--max-steps", str(args.max_steps)))
    command.append(operation_id)
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)  # noqa: S603 - arguments are a fixed list.


async def _run_session_turn(args: argparse.Namespace) -> dict[str, Any]:
    config, manager = _chat_config_and_manager(args)
    async with LocalExecutionHost() as host:
        turn = await host.turns.submit(
            config=config,
            manager=manager,
            session_id=args.session_id,
            message=args.message,
            turn_id="turn-0001",
            interactive_approvals=False,
        )
        output, _ = await turn.result()
        return _turn_result_payload(turn, output)


def _session_chat(args: argparse.Namespace) -> None:
    _validate_session_target(args)
    return asyncio.run(_interactive_session_chat(args))


async def _interactive_session_chat(args: argparse.Namespace) -> None:
    config, manager = _chat_config_and_manager(args)
    session_id = args.session_id
    if session_id is None:
        session_id = manager.create("chat", mode="chat").session_id
    else:
        manager.load(session_id)

    async with LocalExecutionHost() as host:
        await host.turns.prewarm_session(
            config=config,
            manager=manager,
            session_id=session_id,
        )
        print(f"lora session chat: {session_id}")
        print("Type /exit or /quit to end.")
        turn_index = 1
        while True:
            try:
                user_input = await asyncio.to_thread(input, "> ")
            except EOFError:
                break
            if user_input.strip() in {"/exit", "/quit"}:
                break
            if not user_input.strip():
                continue
            turn = await host.turns.submit(
                config=config,
                manager=manager,
                session_id=session_id,
                message=user_input,
                turn_id=f"turn-{turn_index:04d}",
                interactive_approvals=True,
            )
            await turn.wait_ready()
            streamed = await _render_turn_events(turn)
            output, _ = await turn.result()
            result = plain_object(plain_object(output.data).get("result"))
            if streamed:
                print()
            elif result.get("final_answer"):
                print(result["final_answer"])
            if result.get("error"):
                print(f"agent error: {result['error']}", file=sys.stderr)
                break
            turn_index += 1


async def _render_turn_events(turn: ManagedSessionTurn) -> bool:
    if turn.execution_handle is None:
        return False
    streamed = False
    async with turn.subscribe() as execution_events:
        async for event in execution_events:
            data = plain_object(event.data)
            if (
                event.kind == "model.text.delta"
                and ".foreground.react.model.model" in event.module_path
            ):
                chunk = str(data.get("text") or "")
                if chunk:
                    streamed = True
                    print(chunk, end="", flush=True)
            elif event.kind == "lora.approval.requested":
                answer = await asyncio.to_thread(
                    input,
                    f"Approve {data.get('tool_name')} {data.get('arguments')}? [y/N] ",
                )
                await turn.runtime_service.deliver_approval(
                    str(data["approval_id"]),
                    approved=answer.strip().lower() in {"y", "yes"},
                    comment="interactive CLI decision",
                )
    return streamed


def _turn_result_payload(turn: ManagedSessionTurn, output: Any) -> dict[str, Any]:
    run_ref = turn.run_ref
    if run_ref is None:
        raise RuntimeError("session turn has no case run")
    result = plain_object(plain_object(output.data).get("result"))
    payload = {
        "session_id": run_ref.session_id,
        "case_run_id": run_ref.case_run_id,
        "execution_id": turn.execution_id,
        "status": str(result.get("status") or turn.status),
        "final_answer": str(result.get("final_answer") or ""),
        "run_dir": str(Path(run_ref.run_dir).resolve()),
    }
    if result.get("error"):
        payload["error"] = str(result["error"])
    return payload


def _chat_config_and_manager(
    args: argparse.Namespace,
) -> tuple[RunConfig, SessionManager]:
    config = load_run_config(
        workspace_root=args.workspace_root,
        session_id=args.session_id,
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )
    return config, SessionManager(config)


def _collaboration_config_and_manager(
    args: argparse.Namespace,
) -> tuple[RunConfig, SessionManager]:
    config = load_run_config(
        workspace_root=args.workspace_root,
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )
    return config, SessionManager(config)


def _manager(args: argparse.Namespace) -> SessionManager:
    return SessionManager(
        load_run_config(
            workspace_root=args.workspace_root,
            session_id=getattr(args, "session_id", None),
            agent_alias=args.agent_alias,
            max_steps=args.max_steps,
        )
    )


__all__ = ["register_session_parser"]
