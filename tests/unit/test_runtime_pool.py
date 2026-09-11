from __future__ import annotations

import asyncio

import pytest

from lora.orchestration import RuntimeScopeKey, WorkspaceRuntimePool
from lora.schema import ModelRouteConfig, ResolvedAgentConfig, RunConfig


class _Runtime:
    def __init__(self, config, **_kwargs) -> None:
        self.config = config
        self.reminders = object()
        self.initialized = 0
        self.closed: list[bool] = []

    async def initialize(self) -> None:
        self.initialized += 1

    async def close(self, *, cancel: bool) -> None:
        self.closed.append(cancel)


def _config(tmp_path, *, max_steps: int = -1) -> RunConfig:
    return RunConfig(
        workspace_root=tmp_path,
        lora_root=tmp_path / ".lora",
        max_steps=max_steps,
    )


@pytest.mark.asyncio
async def test_same_generation_reuses_one_runtime(tmp_path) -> None:
    created: list[_Runtime] = []

    def factory(config, **kwargs):
        runtime = _Runtime(config, **kwargs)
        created.append(runtime)
        return runtime

    pool = WorkspaceRuntimePool(runtime_factory=factory)
    first, second = await asyncio.gather(
        pool.acquire(config=_config(tmp_path)),
        pool.acquire(config=_config(tmp_path)),
    )

    assert first.runtime is second.runtime
    assert len(created) == 1
    assert created[0].initialized == 1

    await first.release()
    await first.release()
    await second.release()
    assert created[0].closed == []
    await pool.close(cancel=True)
    assert created[0].closed == [True]


@pytest.mark.asyncio
async def test_pool_injects_host_collaboration_into_new_runtime(tmp_path) -> None:
    created: list[_Runtime] = []
    collaboration = object()

    def factory(config, **kwargs):
        assert kwargs["collaboration"] is collaboration
        runtime = _Runtime(config, **kwargs)
        created.append(runtime)
        return runtime

    pool = WorkspaceRuntimePool(runtime_factory=factory)
    pool.attach_collaboration(collaboration)
    lease = await pool.acquire(config=_config(tmp_path))

    assert len(created) == 1
    await lease.release()
    await pool.close(cancel=True)


@pytest.mark.asyncio
async def test_new_generation_retires_old_after_last_lease(tmp_path) -> None:
    created: list[_Runtime] = []

    def factory(config, **kwargs):
        runtime = _Runtime(config, **kwargs)
        created.append(runtime)
        return runtime

    pool = WorkspaceRuntimePool(runtime_factory=factory)
    old = await pool.acquire(config=_config(tmp_path))
    new = await pool.acquire(config=_config(tmp_path, max_steps=4))

    assert old.runtime is not new.runtime
    assert created[0].closed == []

    await old.release()
    assert created[0].closed == [False]
    await new.release()
    assert created[1].closed == []

    await pool.retire_scope(RuntimeScopeKey.from_config(new.runtime.config))
    assert created[1].closed == [False]


@pytest.mark.asyncio
async def test_pool_rejects_new_leases_after_stop(tmp_path) -> None:
    pool = WorkspaceRuntimePool(runtime_factory=_Runtime)
    await pool.stop_accepting()

    with pytest.raises(RuntimeError, match="closing"):
        await pool.acquire(config=_config(tmp_path))


@pytest.mark.asyncio
async def test_credential_change_creates_a_new_generation_without_exposing_key(
    tmp_path,
) -> None:
    created: list[_Runtime] = []

    def configured(secret: str) -> RunConfig:
        route = ModelRouteConfig(
            id="primary",
            provider="openai",
            model_name="model",
            base_url="https://example.invalid",
            api_key_env="MODEL_API_KEY",
            api_key=secret,
            api_key_source="config",
        )
        config = _config(tmp_path)
        config.resolved_agent = ResolvedAgentConfig(alias="default", routes=(route,))
        return config

    def factory(config, **kwargs):
        runtime = _Runtime(config, **kwargs)
        created.append(runtime)
        return runtime

    pool = WorkspaceRuntimePool(runtime_factory=factory)
    first = await pool.acquire(config=configured("first-secret"))
    await first.release()
    second = await pool.acquire(config=configured("second-secret"))

    assert first.runtime is not second.runtime
    assert len(created) == 2
    assert "first-secret" not in first.runtime.generation_key.config_fingerprint
    assert "second-secret" not in second.runtime.generation_key.config_fingerprint
    await second.release()
    await pool.close(cancel=True)


@pytest.mark.asyncio
async def test_concurrent_initialization_failure_is_shared_and_cleaned_up(
    tmp_path,
) -> None:
    release_initialization = asyncio.Event()
    created: list[_Runtime] = []

    class _FailingRuntime(_Runtime):
        async def initialize(self) -> None:
            self.initialized += 1
            await release_initialization.wait()
            raise RuntimeError("initialization failed")

    def factory(config, **kwargs):
        runtime = _FailingRuntime(config, **kwargs)
        created.append(runtime)
        return runtime

    pool = WorkspaceRuntimePool(runtime_factory=factory)
    first = asyncio.create_task(pool.acquire(config=_config(tmp_path)))
    await asyncio.sleep(0)
    second = asyncio.create_task(pool.acquire(config=_config(tmp_path)))
    await asyncio.sleep(0)
    release_initialization.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert len(created) == 1
    assert all(isinstance(result, RuntimeError) for result in results)
    assert created[0].closed == [True]
    assert pool._entries == {}
