from __future__ import annotations

from lora.runtime import agent
from lora.runtime.agent import LoraAgent, PromptRenderContext, _render_available_tools_prompt


def test_agent_package_exports_supported_entry_points() -> None:
    assert LoraAgent is agent.LoraAgent
    assert PromptRenderContext is agent.PromptRenderContext
    assert callable(_render_available_tools_prompt)


def test_agent_runtime_is_split_by_responsibility() -> None:
    assert LoraAgent.__module__ == "lora.runtime.agent.core"
    assert PromptRenderContext.__module__ == "lora.runtime.agent.prompt_models"
