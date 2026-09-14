from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from pygent.runtime import SQLiteModelDeploymentStore

from lora.config import load_run_config
from lora.runtime.service import LoraRuntimeService


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["profile", "admission"])
async def test_old_model_json_does_not_block_new_runtime(tmp_path: Path, location: str) -> None:
    config = load_run_config(workspace_root=tmp_path)
    config.runtime_durability.history_path = str(tmp_path / "executions.sqlite3")
    legacy = tmp_path / "model-deployments-v1.sqlite3"
    store = SQLiteModelDeploymentStore(legacy)
    await store.open()
    await store.close()
    snapshot = {
        "model_group": {
            "name": "lora:dev", "routes": [], "fallback": [],
            "capacity_key": None, "max_concurrency": None, "resolution": "concrete",
        },
    }
    with sqlite3.connect(legacy) as db:
        if location == "profile":
            db.execute("INSERT INTO pygent_model_profiles VALUES(?,?,?,?,0)",
                       ("scope", "lora:dev", "default", json.dumps(snapshot)))
        else:
            admission = {"snapshots": [{"group_name": "lora:dev", "snapshot": snapshot}]}
            db.execute("INSERT INTO pygent_model_admissions VALUES(?,?,?,1)",
                       ("old-execution", "scope", json.dumps(admission)))

    # Reproduce the reported failure using the official loader and legacy data.
    broken = SQLiteModelDeploymentStore(legacy)
    try:
        with pytest.raises(ValueError, match="stored model group fields"):
            await broken.open()
    finally:
        await broken.close()
    original = legacy.read_bytes()

    service = LoraRuntimeService(config)
    try:
        agent = service.new_agent(interactive_approvals=False)
        await service.bind(agent, agent)
        assert Path(service.model_store.path).name == "model-deployments-v2.sqlite3"
    finally:
        await service.close()
    assert legacy.read_bytes() == original


@pytest.mark.asyncio
async def test_current_store_keeps_its_namespace_on_restart(tmp_path: Path) -> None:
    config = load_run_config(workspace_root=tmp_path)
    config.runtime_durability.history_path = str(tmp_path / "executions.sqlite3")
    legacy = tmp_path / "model-deployments-v1.sqlite3"
    store = SQLiteModelDeploymentStore(legacy)
    await store.open()
    await store.close()
    namespaces = []
    for _ in range(2):
        service = LoraRuntimeService(config)
        try:
            agent = service.new_agent(interactive_approvals=False)
            await service.bind(agent, agent)
            assert Path(service.model_store.path) == tmp_path / "model-deployments-v2.sqlite3"
            namespaces.append(service.model_store.namespace_id)
        finally:
            await service.close()
    assert namespaces[0] == namespaces[1]
    assert (tmp_path / "model-deployments-v2.sqlite3").exists()
