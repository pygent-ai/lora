from __future__ import annotations

from types import SimpleNamespace

import pytest

from lora.runtime import agent
from lora.runtime.agent import LoraAgent, PromptRenderContext, _render_available_tools_prompt
from lora.runtime.agent.core import (
    MODEL_MAX_OUTPUT_TOKENS,
    PYGENT_VERIFY_SSL_ENV,
    _route_supports_streaming,
    _verify_ssl_from_env,
)


def test_agent_package_exports_supported_entry_points() -> None:
    assert LoraAgent is agent.LoraAgent
    assert PromptRenderContext is agent.PromptRenderContext
    assert callable(_render_available_tools_prompt)


def test_agent_runtime_is_split_by_responsibility() -> None:
    assert LoraAgent.__module__ == "lora.runtime.agent.core"
    assert PromptRenderContext.__module__ == "lora.runtime.agent.prompt_models"


def test_deepseek_routes_disable_streaming_for_reliable_tool_arguments() -> None:
    assert _route_supports_streaming(SimpleNamespace(base_url="https://api.deepseek.com")) is False
    assert _route_supports_streaming(SimpleNamespace(base_url="https://api.openai.com/v1")) is True


def test_coding_agent_has_enough_output_budget_to_reach_tool_execution() -> None:
    assert MODEL_MAX_OUTPUT_TOKENS >= 4096


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("YES", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("NO", False),
        ("off", False),
    ],
)
def test_verify_ssl_is_controlled_by_environment(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv(PYGENT_VERIFY_SSL_ENV, raw_value)

    assert _verify_ssl_from_env() is expected


def test_verify_ssl_uses_pygent_default_when_environment_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PYGENT_VERIFY_SSL_ENV, raising=False)

    assert _verify_ssl_from_env() is None


def test_verify_ssl_rejects_invalid_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PYGENT_VERIFY_SSL_ENV, "sometimes")

    with pytest.raises(ValueError, match=PYGENT_VERIFY_SSL_ENV):
        _verify_ssl_from_env()
