from lora.runtime.agent.prompt_models import PromptRenderContext
from lora.runtime.agent.prompt_sources import (
    _render_system_coding_rules_prompt,
    _render_system_path_policy_prompt,
)


def test_path_policy_keeps_reproduction_files_inside_workspace(tmp_path) -> None:
    context = PromptRenderContext(
        session_id="chat-test",
        workspace_root=tmp_path,
        session_dir=tmp_path / ".lora" / "sessions" / "chat-test",
        turn_id="turn-1",
        projection={},
        tool_names=[],
    )

    prompt = _render_system_path_policy_prompt(context)

    assert "temporary reproduction scripts and scratch files inside the workspace" in prompt
    assert "Never place /tmp" in prompt
    assert "shell redirects, pipeline output files" in prompt


def test_coding_rules_make_open_ended_audits_converge(tmp_path) -> None:
    context = PromptRenderContext(
        session_id="chat-test",
        workspace_root=tmp_path,
        session_dir=tmp_path / ".lora" / "sessions" / "chat-test",
        turn_id="turn-1",
        projection={},
        tool_names=[],
    )

    prompt = _render_system_coding_rules_prompt(context)

    assert prompt is not None
    assert "time-box exploration" in prompt
    assert "write the failing regression test" in prompt
