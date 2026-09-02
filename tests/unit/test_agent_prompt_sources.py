from lora.runtime.agent.prompt_models import PromptRenderContext
from lora.runtime.agent.prompts import PromptRegistry
from lora.runtime.agent.prompt_sources import (
    _render_available_tools_prompt,
    _render_system_action_safety_prompt,
    _render_system_coding_rules_prompt,
    _render_system_output_style_prompt,
    _render_system_path_policy_prompt,
    _render_system_tool_policy_prompt,
    _render_token_budget_prompt,
)


def _context(tmp_path, *, tool_names=None) -> PromptRenderContext:
    return PromptRenderContext(
        session_id="chat-test",
        workspace_root=tmp_path,
        session_dir=tmp_path / ".lora" / "sessions" / "chat-test",
        turn_id="turn-1",
        projection={},
        tool_names=list(tool_names or []),
        user_lora_root=tmp_path / "user-lora",
        project_lora_root=tmp_path / ".lora",
        user_skills_dir=tmp_path / "user-lora" / "skills",
        project_skills_dir=tmp_path / ".lora" / "skills",
    )


def test_path_policy_separates_host_and_sandbox_paths(tmp_path) -> None:
    prompt = _render_system_path_policy_prompt(_context(tmp_path))
    assert "host-side file-tool writes" in prompt
    assert "user explicitly supplies another authorized path" in prompt
    assert "inside an explicitly scoped container or remote sandbox" in prompt
    assert "including its temporary directory" in prompt


def test_coding_rules_converge_and_verify_by_risk(tmp_path) -> None:
    prompt = _render_system_coding_rules_prompt(_context(tmp_path))
    assert prompt is not None
    assert "what must change, where and why it must change" in prompt
    assert "narrowest relevant verification first" in prompt
    assert (
        "adding the smallest compatible implementation is part of the request" in prompt
    )
    assert "do not substitute a design essay for execution" in prompt
    assert "Preserve existing comments" in prompt


def test_tool_and_action_policies_cover_denials_parallelism_and_irreversible_actions(
    tmp_path,
) -> None:
    context = _context(tmp_path)
    tool_policy = _render_system_tool_policy_prompt(context)
    action_safety = _render_system_action_safety_prompt(context)
    assert "denied by permission" in tool_policy
    assert "run independent calls together" in tool_policy
    assert (
        "destructive, hard-to-reverse, or externally visible actions" in action_safety
    )
    assert "Do not commit, amend, push, force" in action_safety
    static_module_ids = [
        module.id for module in PromptRegistry().resolve(phase="static")
    ]
    assert static_module_ids.index("system.injection_guard") < static_module_ids.index(
        "system.action_safety"
    )


def test_dynamic_tools_and_context_rules_are_capability_aware(tmp_path) -> None:
    context = _context(tmp_path, tool_names=["read", "write", "edit", "bash"])
    tools_prompt = _render_available_tools_prompt(context)
    budget_prompt = _render_token_budget_prompt(context)
    output_prompt = _render_system_output_style_prompt(context)
    assert "prefer them over shell cat/head/tail/sed" in tools_prompt
    assert "A context summary continues the same task" in budget_prompt
    assert "without narrating private deliberation" in output_prompt
