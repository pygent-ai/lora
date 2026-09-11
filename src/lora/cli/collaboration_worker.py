from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from lora.config import load_run_config
from lora.orchestration import LocalExecutionHost
from lora.sessions import SessionManager


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one queued Lora collaboration operation"
    )
    parser.add_argument("operation_id")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--agent", dest="agent_alias", required=True)
    parser.add_argument("--max-steps", type=int, default=None)
    args = parser.parse_args(argv)
    asyncio.run(_run(args))
    return 0


async def _run(args: argparse.Namespace) -> None:
    config = load_run_config(
        workspace_root=args.workspace_root,
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )
    manager = SessionManager(config)
    async with LocalExecutionHost() as host:
        await host.collaboration.resume_operation(
            config=config,
            manager=manager,
            operation_id=args.operation_id,
        )
        await host.collaboration.wait(
            config=config,
            operation_id=args.operation_id,
        )


if __name__ == "__main__":
    raise SystemExit(main())
