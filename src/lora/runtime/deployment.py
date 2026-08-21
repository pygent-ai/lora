from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pygent.tool import LocalToolExecutor, SandboxExecutorSupport


def _model_deployment_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _add_default_provider_options(value: object) -> bool:
    changed = False
    if isinstance(value, dict):
        if {"route_id", "provider", "model"}.issubset(
            value
        ) and "provider_options" not in value:
            value["provider_options"] = {}
            changed = True
        for item in value.values():
            changed = _add_default_provider_options(item) or changed
    elif isinstance(value, list):
        for item in value:
            changed = _add_default_provider_options(item) or changed
    return changed


def _refresh_snapshot_digest(snapshot: dict[str, object]) -> None:
    snapshot["digest"] = _model_deployment_digest(
        {
            "scope_id": snapshot.get("deployment_scope_id"),
            "group": snapshot.get("model_group"),
            "profile": snapshot.get("profile"),
            "resources": snapshot.get("resources"),
        }
    )


def _migrate_model_deployment_document(value: object) -> bool:
    if not _add_default_provider_options(value):
        return False
    if not isinstance(value, dict):
        return True

    if isinstance(value.get("model_group"), dict):
        _refresh_snapshot_digest(value)

    snapshots = value.get("snapshots")
    if isinstance(snapshots, list):
        digest_items: list[dict[str, object]] = []
        for item in snapshots:
            if not isinstance(item, dict) or not isinstance(item.get("snapshot"), dict):
                continue
            snapshot = item["snapshot"]
            _refresh_snapshot_digest(snapshot)
            digest_items.append(
                {
                    "group_name": item.get("group_name"),
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "digest": snapshot.get("digest"),
                }
            )
        value["digest"] = _model_deployment_digest(digest_items)
    return True


def migrate_legacy_model_deployments(path: Path) -> int:
    """Make Pygent <=0.2.18 route snapshots readable by Pygent 0.2.19."""

    if not path.exists():
        return 0

    migrated = 0
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = (
            ("pygent_model_profiles", "rowid", "snapshot_json"),
            ("pygent_model_admissions", "rowid", "admission_json"),
        )
        for table, key_column, json_column in columns:
            if table not in tables:
                continue
            rows = connection.execute(
                f'SELECT "{key_column}", "{json_column}" FROM "{table}"'
            ).fetchall()
            for key, payload in rows:
                value = json.loads(payload)
                if not _migrate_model_deployment_document(value):
                    continue
                connection.execute(
                    f'UPDATE "{table}" SET "{json_column}" = ? WHERE "{key_column}" = ?',
                    (json.dumps(value, ensure_ascii=False, separators=(",", ":")), key),
                )
                migrated += 1
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return migrated


class LoraModelResourceResolver:
    """Reconstructable deployment identity with process-local live invokers."""

    resolver_id = "lora-model"

    def __init__(self) -> None:
        self._invokers: dict[str, Any] = {}

    def register(self, revision: str, invoker: Any) -> None:
        self._invokers[revision] = invoker

    async def validate(self, model_group: Any, resources: Any) -> None:
        del model_group
        for _, resource in resources.route_resources:
            if resource.revision not in self._invokers:
                raise ValueError(f"model resource revision {resource.revision!r} is unavailable")

    @asynccontextmanager
    async def acquire(self, model_group: Any, resources: Any) -> Any:
        del model_group
        revision = resources.route_resources[0][1].revision
        invoker = self._invokers.get(revision)
        if invoker is None:
            raise RuntimeError(f"model resource revision {revision!r} is unavailable")
        yield invoker


class WorkspaceToolExecutor(LocalToolExecutor):
    """Deployment adapter for tools confined by Lora's workspace policy."""

    sandbox_support = SandboxExecutorSupport(profiles=("workspace",))


__all__ = [
    "LoraModelResourceResolver",
    "WorkspaceToolExecutor",
    "migrate_legacy_model_deployments",
]
