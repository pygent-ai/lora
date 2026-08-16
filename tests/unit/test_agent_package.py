from __future__ import annotations

from types import SimpleNamespace

from lora.runtime import agent
from lora.runtime.agent import LoraAgent, PromptRenderContext, _render_available_tools_prompt
from lora.runtime.agent.core import MODEL_MAX_OUTPUT_TOKENS, _route_supports_streaming


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
