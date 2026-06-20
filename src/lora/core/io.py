from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    json_path = Path(path)
    if default is not None and not json_path.exists():
        return dict(default)
    return json.loads(json_path.read_text(encoding="utf-8"))


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: str | Path, data: dict[str, Any]) -> None:
    jsonl_path = Path(path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def validate_path_id(value: str, field_name: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "/" in value or "\\" in value:
        raise ValueError(f"{field_name} must not contain path traversal")


def file_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "content_hash": None, "size": None}
    data = path.read_bytes()
    return {"exists": True, "content_hash": hashlib.sha256(data).hexdigest(), "size": len(data)}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def plain_data(value: Any) -> Any:
    if hasattr(value, "data") and not isinstance(value, type):
        return plain_data(value.data)
    if isinstance(value, dict):
        return {key: plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain_data(item) for item in value]
    return value
