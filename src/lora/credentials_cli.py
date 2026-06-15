from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .config import load_run_config
from .secrets import (
    DEFAULT_API_KEY_ENV,
    credential_is_configured,
    delete_keyring_credential,
    delete_user_credential,
    list_user_credential_names,
    lookup_credential,
    prompt_for_secret,
    resolve_api_key_env_name,
    set_keyring_credential,
    set_user_credential,
    user_credentials_path,
)


def register_credentials_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    credentials = sub.add_parser("credentials", help="Manage API keys and other secrets")
    credentials_sub = credentials.add_subparsers(dest="credentials_command", required=True)

    list_cmd = credentials_sub.add_parser("list", help="List credential variable names without exposing values")
    list_cmd.set_defaults(handler=credentials_list)

    set_cmd = credentials_sub.add_parser("set", help="Store a credential in the user credentials file or OS keyring")
    set_cmd.add_argument("env_name", help="Environment variable name, for example DEEPSEEK_API_KEY")
    set_cmd.add_argument("--value", default=None, help="Credential value. Prompts interactively when omitted.")
    set_cmd.add_argument(
        "--keyring",
        action="store_true",
        help="Store in the OS credential manager instead of ~/.lora/credentials.env",
    )
    set_cmd.set_defaults(handler=credentials_set)

    delete_cmd = credentials_sub.add_parser("delete", help="Delete a credential from the user credentials file")
    delete_cmd.add_argument("env_name")
    delete_cmd.add_argument("--keyring", action="store_true", help="Delete from the OS credential manager")
    delete_cmd.set_defaults(handler=credentials_delete)

    validate_cmd = credentials_sub.add_parser("validate", help="Validate that the active agent profile can resolve an API key")
    validate_cmd.set_defaults(handler=credentials_validate)


def credentials_list(args: argparse.Namespace) -> dict[str, Any]:
    config = _base_config(args)
    user_path = user_credentials_path(config.user_lora_root)
    names = set(list_user_credential_names(config.user_lora_root))
    for env_name in (DEFAULT_API_KEY_ENV,):
        if credential_is_configured(env_name, user_lora_root=config.user_lora_root):
            names.add(env_name)
    return {
        "credentials_file": str(user_path),
        "env_names": sorted(names),
        "process_env": sorted(name for name in names if _non_empty(os.environ.get(name))),
        "keyring_available": _keyring_available(),
    }


def credentials_set(args: argparse.Namespace) -> dict[str, Any]:
    env_name = str(args.env_name).strip()
    if not env_name:
        raise ValueError("env_name must not be empty")
    value = args.value or prompt_for_secret(f"{env_name}: ")
    if args.keyring:
        set_keyring_credential(env_name, value)
        os.environ[env_name] = value
        return {"status": "stored", "env_name": env_name, "storage": f"keyring:{env_name}"}

    config = _base_config(args)
    path = set_user_credential(config.user_lora_root, env_name, value)
    return {"status": "stored", "env_name": env_name, "storage": f"file:{path}"}


def credentials_delete(args: argparse.Namespace) -> dict[str, Any]:
    env_name = str(args.env_name).strip()
    if not env_name:
        raise ValueError("env_name must not be empty")
    if args.keyring:
        deleted = delete_keyring_credential(env_name)
        os.environ.pop(env_name, None)
        return {"status": "deleted" if deleted else "missing", "env_name": env_name, "storage": f"keyring:{env_name}"}

    config = _base_config(args)
    deleted = delete_user_credential(config.user_lora_root, env_name)
    return {
        "status": "deleted" if deleted else "missing",
        "env_name": env_name,
        "storage": f"file:{user_credentials_path(config.user_lora_root)}",
    }


def credentials_validate(args: argparse.Namespace) -> dict[str, Any]:
    config = load_run_config(
        workspace_root=args.workspace_root,
        config_file=args.config,
        agent_alias=args.agent_alias,
        model=args.model,
        max_steps=args.max_steps,
    )
    resolved = config.resolved_agent
    env_name = resolved.api_key_env if resolved is not None else DEFAULT_API_KEY_ENV
    api_key = resolved.api_key if resolved is not None else None
    api_key_source = resolved.api_key_source if resolved is not None else "missing"
    if api_key:
        return {
            "status": "ok",
            "agent_alias": config.agent_alias,
            "api_key_env": env_name,
            "api_key_source": api_key_source,
        }
    _, lookup_source = lookup_credential(env_name)
    return {
        "status": "missing",
        "agent_alias": config.agent_alias,
        "api_key_env": env_name,
        "api_key_source": api_key_source,
        "hint": (
            f"Set the key with `lora credentials set {env_name}` "
            f"or export {env_name} in your shell environment."
        ),
        "lookup_source": lookup_source,
        "credentials_file": str(user_credentials_path(config.user_lora_root)),
    }


def _base_config(args: argparse.Namespace):
    return load_run_config(
        workspace_root=args.workspace_root,
        config_file=args.config,
        agent_alias=args.agent_alias,
        model=args.model,
        max_steps=args.max_steps,
    )


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
    except ImportError:
        return False
    return True


def _non_empty(value: object | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
