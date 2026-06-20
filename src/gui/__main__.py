from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gui",
        description="Launch the archived PySide GUI.",
    )
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--config", dest="config_path", default=None)
    parser.add_argument("--agent", dest="agent_alias", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--smoke", action="store_true", help="Construct the window and exit.")
    args = parser.parse_args(argv)

    try:
        from gui.app import run_app
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            print(
                "The archived PySide GUI requires the legacy-gui dependency group.\n"
                "Run: uv run --group legacy-gui python -m gui",
                file=sys.stderr,
            )
            return 2
        raise

    return run_app(
        workspace_root=args.workspace_root,
        config_path=args.config_path,
        agent_alias=args.agent_alias,
        model=args.model,
        max_steps=args.max_steps,
        smoke=args.smoke,
    )


if __name__ == "__main__":
    raise SystemExit(main())
