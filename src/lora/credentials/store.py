from __future__ import annotations

import getpass
import os
import sys
import warnings
from pathlib import Path

from lora.core.io import load_env_file

try:
    import keyring
except ImportError:  # pragma: no cover - optional dependency
    keyring = None  # type: ignore[assignment]

KEYRING_SERVICE = "lora"
USER_CREDENTIALS_FILENAME = "credentials.env"
DEFAULT_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEPRECATED_WORKSPACE_ENV = ".env"


def user_credentials_path(user_lora_root: str | Path) -> Path:
    return Path(user_lora_root).expanduser().resolve() / USER_CREDENTIALS_FILENAME


def ensure_user_lora_root(user_lora_root: str | Path) -> Path:
    root = Path(user_lora_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_credentials(*, user_lora_root: str | Path, workspace_root: str | Path) -> list[str]:
    """Load credential files into ``os.environ`` without overriding existing variables."""
    loaded: list[str] = []
    user_root = ensure_user_lora_root(user_lora_root)
    workspace = Path(workspace_root).expanduser().resolve()

    user_path = user_root / USER_CREDENTIALS_FILENAME
    if _load_env_file_quiet(user_path):
        loaded.append(f"file:{user_path}")

    local_path = workspace / ".env.local"
    if _load_env_file_quiet(local_path):
        loaded.append(f"file:{local_path}")

    legacy_path = workspace / DEPRECATED_WORKSPACE_ENV
    if _load_env_file_quiet(legacy_path):
        loaded.append(f"file:{legacy_path}")
        warnings.warn(
            (
                f"Loading workspace {DEPRECATED_WORKSPACE_ENV} is deprecated. "
                f"Move secrets to {user_path} or {local_path.name}."
            ),
            DeprecationWarning,
            stacklevel=2,
        )
    return loaded


def resolve_api_key_env_name(model_request: dict[str, object] | None) -> str:
    if isinstance(model_request, dict):
        value = model_request.get("api_key_env")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_API_KEY_ENV


def lookup_credential(env_name: str) -> tuple[str | None, str]:
    value = _non_empty(os.environ.get(env_name))
    if value:
        return value, f"env:{env_name}"
    keyring_value = get_keyring_credential(env_name)
    if keyring_value:
        return keyring_value, f"keyring:{env_name}"
    return None, "missing"


def get_keyring_credential(env_name: str) -> str | None:
    if keyring is None:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, env_name)
    except Exception:  # noqa: BLE001 - keyring backends vary by platform
        return None
    return _non_empty(value)


def set_keyring_credential(env_name: str, value: str) -> None:
    if keyring is None:
        raise RuntimeError("keyring is not installed; run `uv add keyring` to use OS credential storage")
    keyring.set_password(KEYRING_SERVICE, env_name, value)


def delete_keyring_credential(env_name: str) -> bool:
    if keyring is None:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, env_name)
    except Exception:  # noqa: BLE001 - missing entries are not an error
        return False
    return True


def read_env_entries(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        entries[key] = value.strip().strip('"').strip("'")
    return entries


def write_env_entries(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={entries[key]}" for key in sorted(entries)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    _restrict_file_permissions(path)


def set_user_credential(user_lora_root: str | Path, env_name: str, value: str) -> Path:
    root = ensure_user_lora_root(user_lora_root)
    path = root / USER_CREDENTIALS_FILENAME
    entries = read_env_entries(path)
    entries[env_name] = value
    write_env_entries(path, entries)
    os.environ[env_name] = value
    return path


def delete_user_credential(user_lora_root: str | Path, env_name: str) -> bool:
    path = user_credentials_path(user_lora_root)
    entries = read_env_entries(path)
    if env_name not in entries:
        return False
    del entries[env_name]
    write_env_entries(path, entries)
    os.environ.pop(env_name, None)
    return True


def list_user_credential_names(user_lora_root: str | Path) -> list[str]:
    return sorted(read_env_entries(user_credentials_path(user_lora_root)))


def credential_is_configured(env_name: str, *, user_lora_root: str | Path) -> bool:
    if _non_empty(os.environ.get(env_name)):
        return True
    if env_name in read_env_entries(user_credentials_path(user_lora_root)):
        return True
    return get_keyring_credential(env_name) is not None


def prompt_for_secret(prompt: str) -> str:
    if sys.stdin.isatty():
        value = getpass.getpass(prompt)
    else:
        value = sys.stdin.read().strip()
    if not value.strip():
        raise ValueError("credential value must not be empty")
    return value.strip()


def warn_plaintext_api_key() -> None:
    warnings.warn(
        "model_request.api_key in lora.yaml is deprecated. "
        f"Use api_key_env and store the secret in {USER_CREDENTIALS_FILENAME} or an environment variable.",
        DeprecationWarning,
        stacklevel=3,
    )


def _load_env_file_quiet(path: Path) -> bool:
    if not path.exists():
        return False
    load_env_file(path)
    return True


def _restrict_file_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(0o600)
    except OSError:
        return


def _non_empty(value: object | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
