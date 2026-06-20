from __future__ import annotations

import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lora-api", description="Run the Lora local FastAPI service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--config", dest="config_path", default=None)
    parser.add_argument("--agent", dest="agent_alias", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    args = parser.parse_args(argv)

    import uvicorn

    from lora_api.app import create_app

    app = create_app(
        workspace_root=args.workspace_root,
        config_path=args.config_path,
        agent_alias=args.agent_alias,
        model=args.model,
        max_steps=args.max_steps,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
