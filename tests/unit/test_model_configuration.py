from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pygent.llm import ModelConfig, ModelConnection, ModelInfo

from lora.schema import ResolvedAgentConfig, RunConfig
from lora.runtime.model_configuration import (
    CLIENT_FACTORIES,
    CredentialEnvironment,
    build_model_invoker,
    builtin_model_catalogs,
    discover_models,
    preferred_models,
    preferred_profile_name,
)


def native_mapping(
    *, protocol: str = "openai_chat_completions", group: tuple[str, ...] = ("a",)
) -> dict[str, Any]:
    capabilities = {
        "modalities": {"input": ["text"], "output": ["text"]},
        "streaming": {"output": ["text"]},
        "tools": {"call": True, "choice": ["auto"], "parallel": False},
        "structured_output": {"json_object": True, "json_schema": False},
        "reasoning": {"supported": False, "controllable": False},
        "limits": {"context_tokens": 1000, "max_output_tokens": 100},
    }
    return {
        "models": {
            key: {
                "provider": "test",
                "model_id": f"model-{key}",
                "protocol": protocol,
                "connection": {
                    "base_url": "https://example.test/v1",
                    "credential": {"env": "TEST_KEY"},
                    "verify_ssl": True,
                },
                "provider_options": {},
                "capabilities": capabilities,
            }
            for key in group
        },
        "model_groups": {"coding": {"models": list(group)}},
    }


def native_runtime_config(root, *, group: tuple[str, ...] = ("main", "backup")):
    mapping = native_mapping(group=group)
    for model in mapping["models"].values():
        model["connection"]["credential"] = {"none": True}
    config = RunConfig(
        workspace_root=str(root),
        lora_root=str(root / ".lora"),
        model_config_mapping=mapping,
        resolved_agent=ResolvedAgentConfig(
            alias="default", default_model_group="coding"
        ),
    )
    config.runtime_approvals.enabled = False
    config.eternal_conversation.enabled = False
    return config


def test_preferred_models_moves_only_selected_child_to_front() -> None:
    config = ModelConfig.from_mapping(native_mapping(group=("a", "b", "c")))
    assert tuple(item.name for item in preferred_models(config, "coding", "b")) == (
        "b",
        "a",
        "c",
    )
    assert preferred_profile_name("b") == "preferred:b"


@pytest.mark.parametrize(
    "protocol",
    [
        "openai_chat_completions",
        "openai_responses",
        "anthropic_messages",
        "gemini_generate_content",
    ],
)
def test_build_model_invoker_uses_each_native_protocol(protocol: str) -> None:
    invoker = build_model_invoker(
        ModelConfig.from_mapping(native_mapping(protocol=protocol)),
        credential_environ={"TEST_KEY": "secret"},
    )
    assert protocol in invoker._adapters
    assert "a" in invoker._clients
    asyncio.run(invoker.aclose())


def test_credential_environment_checks_transient_before_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "lora.runtime.model_configuration.lookup_credential",
        lambda name, user_lora_root: ("stored", f"keyring:{name}"),
    )
    environ = CredentialEnvironment(tmp_path, transient={"TEST_KEY": "draft"})
    assert environ["TEST_KEY"] == "draft"
    assert environ["OTHER_KEY"] == "stored"


@pytest.mark.asyncio
async def test_discovery_uses_native_catalog_and_closes_client(monkeypatch) -> None:
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(return_value=(ModelInfo("m1"),))
        ),
        aclose=AsyncMock(),
    )
    monkeypatch.setitem(
        CLIENT_FACTORIES, "openai_chat_completions", lambda **kwargs: client
    )
    connection = ModelConnection.from_mapping(
        {
            "base_url": "https://example.test/v1",
            "credential": {"env": "TEST_KEY"},
        }
    )
    result = await discover_models(
        protocol="openai_chat_completions",
        connection=connection,
        credential_environ={"TEST_KEY": "secret"},
        timeout=3,
    )
    assert result == (ModelInfo("m1"),)
    client.models.list.assert_awaited_once_with(timeout=3)
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_discovery_closes_client_when_catalog_fails(monkeypatch) -> None:
    client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(side_effect=TimeoutError("slow"))),
        aclose=AsyncMock(),
    )
    monkeypatch.setitem(
        CLIENT_FACTORIES, "openai_chat_completions", lambda **kwargs: client
    )
    connection = ModelConnection.from_mapping(
        {"base_url": "https://example.test/v1", "credential": {"none": True}}
    )
    with pytest.raises(TimeoutError, match="slow"):
        await discover_models(
            protocol="openai_chat_completions",
            connection=connection,
            credential_environ={},
        )
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_discovery_closes_client_when_cancelled(monkeypatch) -> None:
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(side_effect=asyncio.CancelledError())
        ),
        aclose=AsyncMock(),
    )
    monkeypatch.setitem(
        CLIENT_FACTORIES, "openai_chat_completions", lambda **kwargs: client
    )
    connection = ModelConnection.from_mapping(
        {"base_url": "https://example.test/v1", "credential": {"none": True}}
    )
    with pytest.raises(asyncio.CancelledError):
        await discover_models(
            protocol="openai_chat_completions",
            connection=connection,
            credential_environ={},
        )
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_discovery_rejects_protocol_without_catalog_and_closes(monkeypatch) -> None:
    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setitem(CLIENT_FACTORIES, "openai_responses", lambda **kwargs: client)
    connection = ModelConnection.from_mapping(
        {"base_url": "https://example.test/v1", "credential": {"none": True}}
    )
    with pytest.raises(ValueError, match="does not provide native model discovery"):
        await discover_models(
            protocol="openai_responses",
            connection=connection,
            credential_environ={},
        )
    client.aclose.assert_awaited_once()


def test_missing_native_credential_is_rejected() -> None:
    with pytest.raises(LookupError, match="TEST_KEY"):
        build_model_invoker(
            ModelConfig.from_mapping(native_mapping()), credential_environ={}
        )


def test_proxy_http_client_is_owned_by_invoker(monkeypatch) -> None:
    http_client = SimpleNamespace(aclose=AsyncMock())
    native_client = SimpleNamespace(aclose=AsyncMock())
    factory = Mock(return_value=http_client)
    monkeypatch.setattr(
        "lora.runtime.model_configuration.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs),
    )
    monkeypatch.setitem(
        CLIENT_FACTORIES,
        "openai_chat_completions",
        lambda **kwargs: native_client,
    )
    mapping = native_mapping()
    mapping["models"]["a"]["connection"]["proxy"] = "https://proxy.test"
    invoker = build_model_invoker(
        ModelConfig.from_mapping(mapping),
        credential_environ={"TEST_KEY": "secret"},
    )
    asyncio.run(invoker.aclose())
    native_client.aclose.assert_awaited_once()
    http_client.aclose.assert_awaited_once()


def test_builtin_catalogs_are_json_safe_native_values() -> None:
    catalogs = builtin_model_catalogs()
    assert "openai" in catalogs["providers"]
    assert "text_tools_structured_reasoning" in catalogs["capability_presets"]
    assert catalogs["providers"]["openai"]["default_protocol"] == "openai_responses"
