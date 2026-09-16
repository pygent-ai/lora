from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
from pygent.core import FrozenJsonObject
from pygent.llm import (
    AnthropicMessagesClient,
    CapabilityPresetCatalog,
    GeminiGenerateContentClient,
    ModelCapabilities,
    ModelCapabilityCatalog,
    ModelConfig,
    ConnectionConfig,
    ModelEntry,
    ModelInfo,
    ModelLimits,
    ModelProviderClient,
    ModelSpec,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
    ProviderCatalog,
    ResolvedModelConnection,
    anthropic_messages_adapters,
    gemini_generate_content_adapters,
    openai_compatible_adapters,
    openai_responses_adapters,
)

from lora.credentials import list_user_credential_names, lookup_credential

from .agent.model_invoker import LoraModelInvoker


CLIENT_FACTORIES: dict[str, Any] = {
    "openai_chat_completions": OpenAICompatibleClient,
    "openai_responses": OpenAIResponsesClient,
    "anthropic_messages": AnthropicMessagesClient,
    "gemini_generate_content": GeminiGenerateContentClient,
}


class CredentialEnvironment(Mapping[str, str]):
    """Resolve native environment references across Lora credential sources."""

    def __init__(
        self,
        user_lora_root: str | Path,
        *,
        transient: Mapping[str, str] | None = None,
    ) -> None:
        self.user_lora_root = Path(user_lora_root).expanduser().resolve()
        self.transient = {
            key: value for key, value in dict(transient or {}).items() if value
        }

    def __getitem__(self, key: str) -> str:
        if key in self.transient:
            return self.transient[key]
        value, _ = lookup_credential(key, user_lora_root=self.user_lora_root)
        if value is None:
            raise KeyError(key)
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(
            set(self.transient)
            | set(os.environ)
            | set(list_user_credential_names(self.user_lora_root))
        )

    def __len__(self) -> int:
        return len(set(iter(self)))


class _OwnedProviderClient:
    """Close an injected HTTP client that Pygent intentionally treats as borrowed."""

    def __init__(self, inner: ModelProviderClient, http_client: httpx.AsyncClient) -> None:
        self.inner = inner
        self.http_client = http_client

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def invoke(
        self, model: ModelSpec, payload: FrozenJsonObject
    ) -> FrozenJsonObject:
        return await self.inner.invoke(model, payload)

    def stream(
        self, model: ModelSpec, payload: FrozenJsonObject
    ) -> AsyncIterator[FrozenJsonObject]:
        return self.inner.stream(model, payload)

    async def aclose(self) -> None:
        try:
            await self.inner.aclose()
        finally:
            await self.http_client.aclose()


def preferred_profile_name(model_key: str) -> str:
    if not isinstance(model_key, str) or not model_key:
        raise ValueError("model_key must be a non-empty string")
    return f"preferred:{model_key}"


def preferred_models(
    config: ModelConfig, group_name: str, preferred_model_key: str
) -> tuple[ModelEntry, ...]:
    try:
        group = config.model_groups[group_name]
    except KeyError:
        raise ValueError(f"unknown model group {group_name!r}") from None
    by_name = {entry.name: entry for entry in group.models}
    if preferred_model_key not in by_name:
        raise ValueError(
            f"model {preferred_model_key!r} is not in group {group_name!r}"
        )
    return (
        by_name[preferred_model_key],
        *(entry for entry in group.models if entry.name != preferred_model_key),
    )


def build_model_invoker(
    config: ModelConfig, *, credential_environ: Mapping[str, str]
) -> LoraModelInvoker:
    protocols = {entry.spec.protocol for entry in config.models.values()}
    unsupported = protocols - set(CLIENT_FACTORIES)
    if unsupported:
        raise ValueError("unsupported model protocols: " + ", ".join(sorted(unsupported)))
    adapters: dict[str, Any] = {}
    for factory in (
        openai_compatible_adapters,
        openai_responses_adapters,
        anthropic_messages_adapters,
        gemini_generate_content_adapters,
    ):
        adapters.update(factory())
    clients = {
        key: _create_client(
            connection=config.connection_for(key),
            credential_environ=credential_environ,
        )
        for key in config.models
    }
    return LoraModelInvoker(
        adapters={protocol: adapters[protocol] for protocol in protocols},
        clients=clients,
    )


def _create_client(
    *,
    connection: ResolvedModelConnection,
    credential_environ: Mapping[str, str],
) -> ModelProviderClient:
    try:
        factory = CLIENT_FACTORIES[connection.protocol]
    except KeyError:
        raise ValueError(
            f"unsupported model protocol {connection.protocol!r}"
        ) from None
    api_key = connection.credential.resolve(credential_environ)
    if connection.proxy is None:
        return factory(
            base_url=connection.base_url,
            api_key=api_key,
            verify_ssl=connection.verify_ssl,
        )
    http_client = httpx.AsyncClient(
        proxy=connection.proxy,
        verify=connection.verify_ssl,
    )
    inner = factory(
        base_url=connection.base_url,
        api_key=api_key,
        client=http_client,
    )
    return _OwnedProviderClient(inner, http_client)


async def discover_models(
    *,
    connection_name: str,
    protocol: str,
    connection: ConnectionConfig,
    credential_environ: Mapping[str, str],
    timeout: float = 10.0,
) -> tuple[ModelInfo, ...]:
    client = _create_client(
        connection=ResolvedModelConnection(
            name=connection_name,
            provider=connection.provider,
            protocol=protocol,
            base_url=connection.protocols[protocol],
            credential=connection.credential,
            verify_ssl=connection.verify_ssl,
            proxy=connection.proxy,
        ),
        credential_environ=credential_environ,
    )
    try:
        catalog = getattr(client, "models", None)
        if catalog is None:
            raise ValueError(
                f"protocol {protocol!r} does not provide native model discovery"
            )
        return await catalog.list(timeout=timeout)
    finally:
        await client.aclose()


def builtin_model_catalogs() -> dict[str, Any]:
    providers = {}
    for key, provider in ProviderCatalog.builtin().providers.items():
        providers[key] = {
            "provider": provider.provider,
            "display_name": provider.display_name,
            "default_protocol": provider.default_protocol,
            "protocols": {
                protocol_key: {
                    "protocol": protocol.protocol,
                    "base_url": protocol.base_url,
                    "authentication": protocol.authentication,
                    "api_key_env": protocol.api_key_env,
                    "provider_options_schema": protocol.provider_options_schema.to_dict(),
                }
                for protocol_key, protocol in provider.protocols.items()
            },
        }
    capabilities = [
        {
            "provider": provider,
            "model_id": model_id,
            "protocol": protocol,
            "capabilities": value.to_mapping(),
        }
        for (provider, model_id, protocol), value in ModelCapabilityCatalog.builtin().models.items()
    ]
    presets = {}
    for key, preset in CapabilityPresetCatalog.builtin().presets.items():
        presets[key] = ModelCapabilities(
            modalities=preset.modalities,
            streaming=preset.streaming,
            tools=preset.tools,
            structured_output=preset.structured_output,
            reasoning=preset.reasoning,
            limits=ModelLimits(context_tokens=None, max_output_tokens=None),
        ).to_mapping()
    return {
        "providers": providers,
        "model_capabilities": capabilities,
        "capability_presets": presets,
    }


__all__ = [
    "CLIENT_FACTORIES",
    "CredentialEnvironment",
    "build_model_invoker",
    "builtin_model_catalogs",
    "discover_models",
    "preferred_models",
    "preferred_profile_name",
]
