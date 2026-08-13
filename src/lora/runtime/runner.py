"""Compatibility facade for case workflows moved out of the runtime domain."""

from __future__ import annotations

from typing import Any


def execute_case_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from lora.workflows.case_run import execute_case_run as run

    return run(*args, **kwargs)


def load_case_session(*args: Any, **kwargs: Any) -> Any:
    from lora.workflows.case_run import load_case_session as load

    return load(*args, **kwargs)


__all__ = ["execute_case_run", "load_case_session"]
