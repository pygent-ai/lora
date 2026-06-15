from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pygent.message import AssistantMessage, AssistantMessageChunk, ToolCall
from pygent.module.tool import BaseTool, ToolCategory

from lora.agent import LoraAgent, _to_pygent_message
from lora.runtime import AgentRuntimeAdapter
from lora.schema import BashCliPreset, CaseDefinition, ResolvedAgentConfig, RunConfig
from lora.session import SessionManager


class RecordingAgent:
    def __init__(self) -> None:
        self.seen_session_id: str | None = None
        self.seen_history: list[dict[str, Any]] = []

    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
        self.seen_session_id = context.session_id
        self.seen_history = list(context.history)
        yield {"role": "assistant", "content": "thinking", "type": "conversation.assistant_message"}
        yield {
            "role": "tool",
            "content": '{"ok": true}',
            "type": "conversation.tool_message",
            "payload": {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call_1"},
        }
        yield {"role": "assistant", "content": "done", "type": "conversation.assistant_message"}


class FailingAgent:
    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
        yield {"role": "assistant", "content": "partial", "type": "conversation.assistant_message"}
        raise RuntimeError("boom")


class ToolCallContentThenFinalAgent:
    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
        yield {
            "role": "assistant",
            "content": "我先看一下工具结果",
            "type": "conversation.assistant_message",
            "payload": {
                "role": "assistant",
                "content": "我先看一下工具结果",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "README.md"}'},
                    }
                ],
            },
        }
        yield {
            "role": "tool",
            "content": '{"status": "success", "result": "ok", "error": null, "tool_call_id": "call_1"}',
            "type": "conversation.tool_message",
            "payload": {"role": "tool", "content": '{"status": "success"}', "tool_call_id": "call_1"},
        }
        yield {"role": "assistant", "content": "最终答案", "type": "conversation.assistant_message"}


class DeltaAgent:
    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
        yield {
            "role": "assistant",
            "content": "Hel",
            "type": "conversation.assistant_delta",
            "payload": {"role": "assistant", "content": "Hel", "delta": "Hel"},
        }
        yield {
            "role": "assistant",
            "content": "lo",
            "type": "conversation.assistant_delta",
            "payload": {"role": "assistant", "content": "lo", "delta": "lo"},
        }
        yield {"role": "assistant", "content": "Hello", "type": "conversation.assistant_message"}


class DirectPygentMessageAgent:
    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[AssistantMessage]:
        yield AssistantMessage(
            content="done",
            reasoning_content="Direct object reasoning.",
            usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        )


class ToolCallingLLM:
    def __init__(self) -> None:
        self.request_messages: list[list[dict[str, Any]]] = []
        self.request_system_prompts: list[str] = []

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_messages.append([message.to_dict() for message in context.history.data])
        self.request_system_prompts.append(str(context.system_prompt))
        if len(self.request_messages) == 1:
            context.add_message(
                AssistantMessage(
                    content="",
                    reasoning_content="Need to call the calculator.",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call_add",
                            tool_name="add_numbers",
                            arguments={"a": 2, "b": 3},
                        )
                    ],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            )
            return

        yield AssistantMessageChunk(content="sum is 5", reasoning_content="Now answer.", usage={"total_tokens": 22})
        context.add_message(
            AssistantMessage(
                content="sum is 5",
                reasoning_content="Now answer.",
                usage={"prompt_tokens": 15, "completion_tokens": 7, "total_tokens": 22},
            )
        )


class UnknownToolThenAnswerLLM:
    def __init__(self) -> None:
        self.request_messages: list[list[dict[str, Any]]] = []

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_messages.append([message.to_dict() for message in context.history.data])
        if len(self.request_messages) == 1:
            context.add_message(
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call_missing",
                            tool_name="summon_missing_tool",
                            arguments={"target": "anything"},
                        )
                    ],
                )
            )
            return

        yield AssistantMessageChunk(content="recovered from missing tool")
        context.add_message(AssistantMessage(content="recovered from missing tool"))


class RepeatedToolCallingLLM:
    def __init__(self) -> None:
        self.request_messages: list[list[dict[str, Any]]] = []

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_messages.append([message.to_dict() for message in context.history.data])
        call_count = len(self.request_messages)
        if call_count <= 2:
            context.add_message(
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id=f"call_add_{call_count}",
                            tool_name="add_numbers",
                            arguments={"a": call_count, "b": call_count},
                        )
                    ],
                )
            )
            return

        yield AssistantMessageChunk(content="done")
        context.add_message(AssistantMessage(content="done"))


class EmptyAfterToolThenAnswerLLM:
    def __init__(self, empty_followups: int) -> None:
        self.empty_followups = empty_followups
        self.request_messages: list[list[dict[str, Any]]] = []

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_messages.append([message.to_dict() for message in context.history.data])
        if len(self.request_messages) == 1:
            context.add_message(
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call_add",
                            tool_name="add_numbers",
                            arguments={"a": 1, "b": 2},
                        )
                    ],
                )
            )
            return

        if len(self.request_messages) <= self.empty_followups + 1:
            context.add_message(AssistantMessage(content=""))
            return

        yield AssistantMessageChunk(content="recovered")
        context.add_message(AssistantMessage(content="recovered"))


class BashThenAnswerLLM:
    def __init__(self) -> None:
        self.request_count = 0

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_count += 1
        if self.request_count == 1:
            context.add_message(
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call_bash",
                            tool_name="bash",
                            arguments={"command": "npm install -g typescript-language-server"},
                        )
                    ],
                )
            )
            return

        yield AssistantMessageChunk(content="noticed")
        context.add_message(AssistantMessage(content="noticed"))


class BashCreateSkillThenAnswerLLM:
    def __init__(self, skills_dir: Path) -> None:
        self.request_count = 0
        self.skills_dir = skills_dir

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_count += 1
        if self.request_count == 1:
            skill_path = self.skills_dir / "debugging" / "SKILL.md"
            command = (
                f"mkdir -p {skill_path.parent.as_posix()} && "
                f"cat > {skill_path.as_posix()} <<'EOF'\n"
                "---\n"
                "name: debugging\n"
                "description: Investigate failures systematically before proposing fixes.\n"
                "---\n"
                "# Debugging\n"
                "EOF"
            )
            context.add_message(
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call_bash_skill",
                            tool_name="bash",
                            arguments={"command": command},
                        )
                    ],
                )
            )
            return

        yield AssistantMessageChunk(content="noticed new skill")
        context.add_message(AssistantMessage(content="noticed new skill"))


class BashCreateNamedSkillThenAnswerLLM:
    def __init__(self, skills_dir: Path, *, name: str, description: str) -> None:
        self.request_count = 0
        self.skills_dir = skills_dir
        self.name = name
        self.description = description

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_count += 1
        if self.request_count == 1:
            skill_path = self.skills_dir / self.name / "SKILL.md"
            command = (
                f"mkdir -p {skill_path.parent.as_posix()} && "
                f"cat > {skill_path.as_posix()} <<'EOF'\n"
                "---\n"
                f"name: {self.name}\n"
                f"description: {self.description}\n"
                "---\n"
                "# Skill\n"
                "EOF"
            )
            context.add_message(
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call_bash_skill",
                            tool_name="bash",
                            arguments={"command": command},
                        )
                    ],
                )
            )
            return

        yield AssistantMessageChunk(content="noticed project skill")
        context.add_message(AssistantMessage(content="noticed project skill"))


class AddNumbersTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="add_numbers",
            description="Add two numbers and return the sum.",
            category=ToolCategory.CALCULATION,
        )
        self.parameters.data["a"]["description"] = "First number."
        self.parameters.data["b"]["description"] = "Second number."

    def forward(self, a: float, b: float) -> float:
        return a + b


class ToolEnabledLoraAgent(LoraAgent):
    def start_run(self, case_run_ref: Any, turn_id: str | None) -> None:
        super().start_run(case_run_ref, turn_id)
        self._register_tool(AddNumbersTool())


class FakeBashTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="bash",
            description="Run a fake shell command.",
            category=ToolCategory.SYSTEM,
        )
        self.parameters.data["command"]["description"] = "Command to run."

    def forward(self, command: str) -> dict[str, Any]:
        marker = "cat > "
        if marker in command and "<<'EOF'" in command:
            target = command.split(marker, 1)[1].split(" <<'EOF'", 1)[0].strip()
            content = command.split("<<'EOF'\n", 1)[1].rsplit("\nEOF", 1)[0]
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return {"command": command, "stdout": "added 1 package", "exit_code": 0}


class FakeBashLoraAgent(LoraAgent):
    def start_run(self, case_run_ref: Any, turn_id: str | None) -> None:
        super().start_run(case_run_ref, turn_id)
        self._tools["bash"] = FakeBashTool()


class ToolSchemaTests(unittest.TestCase):
    def test_default_bash_tool_schema_rejects_extra_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            agent = LoraAgent(
                config,
                resolved_agent=ResolvedAgentConfig(alias="default", model_name="test-model", api_key=None),
            )

            agent.start_run(run, "turn-0001")
            bash_tool = next(tool["function"] for tool in agent.tools_param() if tool["function"]["name"] == "bash")

            self.assertEqual(bash_tool["parameters"].get("additionalProperties"), False)


class AgentRuntimeAdapterTests(unittest.TestCase):
    def test_default_lora_agent_receives_resolved_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = ResolvedAgentConfig(
                alias="dev",
                model_name="profile-model",
                api_key=None,
                api_key_source="missing",
                base_url="https://profile.example/v1",
            )
            config = RunConfig(
                workspace_root=tmp,
                lora_root=Path(tmp) / ".lora",
                agent_alias="dev",
                model_name="profile-model",
                api_key_source="missing",
                base_url="https://profile.example/v1",
                resolved_agent=profile,
            )
            manager = SessionManager(config)

            adapter = AgentRuntimeAdapter(config=config, session_manager=manager)

        self.assertEqual(adapter.agent.resolved_agent.alias, "dev")
        self.assertEqual(adapter.agent.model_name, "profile-model")
        self.assertEqual(adapter.agent.base_url, "https://profile.example/v1")

    def test_lora_agent_registers_selected_pygent_tools_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            agent = LoraAgent(config)

            agent.start_run(run, "turn-0001")

            self.assertEqual(list(agent._tools), ["bash", "read", "write", "edit", "glob", "grep", "diff"])
            self.assertEqual(
                [tool["function"]["name"] for tool in agent.tools_param()],
                ["bash", "read", "write", "edit", "glob", "grep", "diff"],
            )
            self.assertNotIn("delete_file", agent._tools)

    def test_run_case_records_messages_events_and_saves_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("case-a")
            run = manager.start_case_run(ref.session_id, "case-a")
            session = manager.load(ref.session_id)
            case = CaseDefinition.from_dict(
                {
                    "id": "case-a",
                    "input": {"messages": [{"role": "user", "content": "hello"}]},
                }
            )
            agent = RecordingAgent()

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_case(session, case, run)
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            messages = _read_jsonl(Path(run.run_dir) / "messages.jsonl")

            self.assertEqual(result.status, "passed")
            self.assertEqual(result.final_answer, "done")
            self.assertEqual(agent.seen_session_id, ref.session_id)
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant", "tool", "assistant"])
            self.assertIn("conversation.user_message", [event["type"] for event in events])
            self.assertIn("conversation.tool_message", [event["type"] for event in events])
            self.assertEqual([message["role"] for message in messages], ["user", "assistant", "tool", "assistant"])

    def test_run_case_appends_initial_skill_reminder_to_first_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            skill_file = workspace / ".lora" / "skills" / "case-helper" / "SKILL.md"
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(
                "---\nname: case-helper\ndescription: Help with case runs.\n---\n",
                encoding="utf-8",
            )
            config = RunConfig(workspace_root=workspace, lora_root=workspace / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("case-a")
            run = manager.start_case_run(ref.session_id, "case-a")
            session = manager.load(ref.session_id)
            case = CaseDefinition.from_dict({"id": "case-a", "input": {"content": "hello"}})
            agent = LoraAgent(
                config,
                resolved_agent=ResolvedAgentConfig(
                    alias=config.agent_alias,
                    model_name="test-model",
                    api_key=None,
                    api_key_source="none",
                    base_url=config.base_url,
                ),
            )

            asyncio.run(AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_case(session, case, run))

            loaded = manager.load(ref.session_id)
            self.assertIn("hello", loaded.history[0]["content"])
            self.assertIn("<system-reminder>", loaded.history[0]["content"])
            self.assertIn("<skills-context>", loaded.history[0]["content"])
            self.assertIn("<name>case-helper</name>", loaded.history[0]["content"])

    def test_carry_context_false_hides_prior_history_but_preserves_it_after_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("case-a")
            session = manager.load(ref.session_id)
            session.history = [{"role": "user", "content": "old context"}]
            manager.save(session)
            run = manager.start_case_run(ref.session_id, "case-a")
            session = manager.load(ref.session_id)
            case = CaseDefinition.from_dict(
                {
                    "id": "case-a",
                    "session": {"carry_context": False},
                    "input": {"content": "fresh input"},
                }
            )
            agent = RecordingAgent()

            asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_case(session, case, run)
            )

            loaded = manager.load(ref.session_id)
            self.assertEqual([item["content"] for item in agent.seen_history], ["fresh input"])
            self.assertEqual(loaded.history[0]["content"], "old context")
            self.assertEqual(loaded.history[1]["content"], "fresh input")

    def test_agent_failure_preserves_partial_trace_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("case-a")
            run = manager.start_case_run(ref.session_id, "case-a")
            session = manager.load(ref.session_id)
            case = CaseDefinition.from_dict({"id": "case-a", "input": {"content": "explode"}})

            result = asyncio.run(
                AgentRuntimeAdapter(agent=FailingAgent(), config=config, session_manager=manager).run_case(
                    session,
                    case,
                    run,
                )
            )

            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            loaded = manager.load(ref.session_id)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.final_answer, "partial")
            self.assertIn("boom", result.error or "")
            self.assertIn("runtime.error", [event["type"] for event in events])
            self.assertEqual(loaded.history[-1]["content"], "partial")

    def test_run_turn_streams_assistant_deltas_without_fragmenting_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            chunks: list[str] = []

            result = asyncio.run(
                AgentRuntimeAdapter(agent=DeltaAgent(), config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="stream me",
                    case_run_ref=run,
                    turn_id="turn-0001",
                    on_assistant_delta=chunks.append,
                )
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            messages = _read_jsonl(Path(run.run_dir) / "messages.jsonl")

            self.assertEqual(chunks, ["Hel", "lo"])
            self.assertEqual(result["final_answer"], "Hello")
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant"])
            self.assertEqual(loaded.history[-1]["content"], "Hello")
            self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
            self.assertNotIn("conversation.assistant_delta", [event["type"] for event in events])

    def test_run_turn_reports_runtime_messages_after_streaming_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            messages: list[tuple[str, str, str | None]] = []

            result = asyncio.run(
                AgentRuntimeAdapter(agent=RecordingAgent(), config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="use a tool",
                    case_run_ref=run,
                    turn_id="turn-0001",
                    on_runtime_message=lambda message: messages.append(
                        (message.role, message.content, message.payload.get("tool_call_id") if message.payload else None)
                    ),
                )
            )

            self.assertEqual(result["final_answer"], "done")
            self.assertEqual(
                messages,
                [
                    ("assistant", "thinking", None),
                    ("tool", '{"ok": true}', "call_1"),
                    ("assistant", "done", None),
                ],
            )

    def test_run_turn_final_answer_uses_last_assistant_message_without_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            result = asyncio.run(
                AgentRuntimeAdapter(
                    agent=ToolCallContentThenFinalAgent(),
                    config=config,
                    session_manager=manager,
                ).run_turn(
                    session=session,
                    user_input="use a tool",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            self.assertEqual(result["final_answer"], "最终答案")

    def test_lora_agent_persists_assistant_tool_calls_in_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = ToolCallingLLM()
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="add 2 and 3",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            assistant = loaded.history[1]
            tool_message = loaded.history[2]
            event_assistant = next(event for event in events if event["type"] == "conversation.assistant_message")

            self.assertEqual(result["status"], "passed")
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant", "tool", "assistant"])
            self.assertEqual(assistant["tool_calls"][0]["id"], "call_add")
            self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "add_numbers")
            self.assertEqual(json.loads(assistant["tool_calls"][0]["function"]["arguments"]), {"a": 2, "b": 3})
            self.assertEqual(assistant["reasoning_content"], "Need to call the calculator.")
            self.assertEqual(assistant["usage"]["total_tokens"], 15)
            self.assertEqual(tool_message["tool_call_id"], "call_add")
            self.assertEqual(event_assistant["payload"]["tool_calls"][0]["id"], "call_add")
            self.assertEqual(event_assistant["payload"]["reasoning_content"], "Need to call the calculator.")
            self.assertEqual(event_assistant["payload"]["usage"]["total_tokens"], 15)
            self.assertEqual(loaded.history[-1]["reasoning_content"], "Now answer.")
            self.assertEqual(loaded.history[-1]["usage"]["total_tokens"], 22)
            self.assertEqual(llm.request_messages[1][-2]["tool_calls"][0]["id"], "call_add")
            self.assertEqual(llm.request_messages[1][-2]["reasoning_content"], "Need to call the calculator.")
            self.assertEqual(llm.request_messages[1][-2]["usage"]["total_tokens"], 15)
            self.assertEqual(llm.request_messages[1][-1]["tool_call_id"], "call_add")
            self.assertEqual(loaded.system_prompt, llm.request_system_prompts[-1])
            self.assertEqual(
                [message["content"] for message in loaded.history[:-1]],
                [message["content"] for message in llm.request_messages[-1][1:]],
            )

    def test_lora_agent_records_unknown_tool_as_tool_error_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = UnknownToolThenAnswerLLM()
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="try any missing tool",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            tool_calls = _read_jsonl(Path(run.run_dir) / "tool_calls.jsonl")
            tool_results = _read_jsonl(Path(run.run_dir) / "tool_results.jsonl")
            unknown_event = next(event for event in events if event["type"] == "tool.unknown")
            tool_message = loaded.history[2]
            tool_payload = json.loads(tool_message["content"])

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["final_answer"], "recovered from missing tool")
            self.assertEqual(len(llm.request_messages), 2)
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant", "tool", "assistant"])
            self.assertEqual(tool_calls[0]["tool_name"], "summon_missing_tool")
            self.assertEqual(tool_calls[0]["args"], {"target": "anything"})
            self.assertEqual(tool_results[0]["status"], "error")
            self.assertEqual(tool_results[0]["error_type"], "UnknownToolError")
            self.assertIn("summon_missing_tool", tool_results[0]["error"])
            self.assertEqual(tool_results[0]["details"]["requested_tool"], "summon_missing_tool")
            self.assertIn("add_numbers", tool_results[0]["details"]["available_tools"])
            self.assertEqual(unknown_event["payload"]["requested_tool"], "summon_missing_tool")
            self.assertEqual(unknown_event["payload"]["arguments"], {"target": "anything"})
            self.assertEqual(tool_payload["status"], "error")
            self.assertEqual(tool_payload["error_type"], "UnknownToolError")
            self.assertEqual(tool_payload["details"]["requested_tool"], "summon_missing_tool")
            self.assertIn("bash", tool_payload["details"]["available_tools"])
            self.assertEqual(llm.request_messages[1][-1]["tool_call_id"], "call_missing")

    def test_model_request_prefix_is_append_only_across_tool_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = ToolCallingLLM()
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="add 2 and 3",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            self.assertEqual(len(llm.request_messages), 2)
            self.assertEqual(llm.request_system_prompts[0], llm.request_system_prompts[1])
            self.assertEqual(loaded.system_prompt, llm.request_system_prompts[0])
            self.assertNotIn("__SYSTEM_PROMPT_REQUEST_BOUNDARY__", llm.request_system_prompts[0])
            self.assertEqual(llm.request_messages[0], llm.request_messages[1][: len(llm.request_messages[0])])

    def test_lora_agent_allows_unlimited_steps_until_no_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=-1)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = RepeatedToolCallingLLM()
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="keep using tools",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["final_answer"], "done")
            self.assertEqual(len(llm.request_messages), 3)
            self.assertEqual(
                [item["role"] for item in loaded.history],
                ["user", "assistant", "tool", "assistant", "tool", "assistant"],
            )

    def test_lora_agent_retries_empty_followup_after_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=-1)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = EmptyAfterToolThenAnswerLLM(empty_followups=5)
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="use a tool",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["final_answer"], "recovered")
            self.assertEqual(len(llm.request_messages), 7)
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant", "tool", "assistant"])

    def test_lora_agent_errors_after_empty_followup_retry_budget_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=-1)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = EmptyAfterToolThenAnswerLLM(empty_followups=6)
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="use a tool",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            self.assertEqual(result["status"], "error")
            self.assertIn("empty assistant response after tool result", result["error"] or "")
            self.assertEqual(len(llm.request_messages), 7)
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant", "tool"])

    def test_runtime_preserves_direct_pygent_message_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            result = asyncio.run(
                AgentRuntimeAdapter(agent=DirectPygentMessageAgent(), config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="hello",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            assistant = loaded.history[-1]

            self.assertEqual(result["final_answer"], "done")
            self.assertEqual(assistant["content"], "done")
            self.assertEqual(assistant["reasoning_content"], "Direct object reasoning.")
            self.assertEqual(assistant["usage"]["total_tokens"], 6)

    def test_to_pygent_message_restores_assistant_metadata(self) -> None:
        message = _to_pygent_message(
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Need to call the calculator.",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "tool_calls": [
                    {
                        "id": "call_add",
                        "type": "function",
                        "function": {"name": "add_numbers", "arguments": "{\"a\": 2, \"b\": 3}"},
                    }
                ],
            }
        )

        self.assertIsNotNone(message)
        restored = message.to_dict()  # type: ignore[union-attr]
        self.assertEqual(restored["tool_calls"][0]["id"], "call_add")
        self.assertEqual(restored["tool_calls"][0]["function"]["name"], "add_numbers")
        self.assertEqual(json.loads(restored["tool_calls"][0]["function"]["arguments"]), {"a": 2, "b": 3})
        self.assertEqual(restored["reasoning_content"], "Need to call the calculator.")
        self.assertEqual(restored["usage"]["total_tokens"], 15)

    def test_run_turn_records_chat_messages_and_saves_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            agent = LoraAgent(
                config,
                resolved_agent=ResolvedAgentConfig(
                    alias=config.agent_alias,
                    model_name="test-model",
                    api_key=None,
                    api_key_source="none",
                    base_url=config.base_url,
                ),
            )

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="hello chat",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            prompt_event = next(event for event in events if event["type"] == "prompt.rendered")
            prompt_text_path = Path(prompt_event["payload"]["prompt_text_path"])
            session_prompt_text_path = (
                Path(ref.session_dir)
                / "context"
                / "rendered_prompts"
                / run.case_run_id
                / "turn-0001.txt"
            )

            self.assertEqual(result["status"], "passed")
            self.assertIn("Lora agent is wired into chat", result["final_answer"])
            self.assertIn("You are Lora", loaded.system_prompt)
            self.assertIn("# Available Tools", loaded.system_prompt)
            self.assertIn("# Tool Result Handling", loaded.system_prompt)
            self.assertIn("# Context Budget", loaded.system_prompt)
            self.assertNotIn("# Runtime Context", loaded.system_prompt)
            self.assertNotIn("# File State", loaded.system_prompt)
            self.assertNotIn("# Recent Context", loaded.system_prompt)
            self.assertTrue(prompt_text_path.exists())
            self.assertTrue(session_prompt_text_path.exists())
            rendered_prompt = prompt_text_path.read_text(encoding="utf-8")
            self.assertNotIn("__SYSTEM_PROMPT_REQUEST_BOUNDARY__", rendered_prompt)
            self.assertIn("# Available Tools", rendered_prompt)
            self.assertIn(
                "Tools currently available for this request: bash, read, write, edit, glob, grep, diff.",
                rendered_prompt,
            )
            self.assertIn("Use diff to inspect persisted Lora file changes.", rendered_prompt)
            self.assertIn("Workspace root:", rendered_prompt)
            self.assertIn("Default excludes:", rendered_prompt)
            self.assertIn(".venv", rendered_prompt)
            self.assertIn(".lora", rendered_prompt)
            self.assertIn("Use glob or grep before bash find/cat", rendered_prompt)
            self.assertIn("For large files, do not read the whole file first.", rendered_prompt)
            self.assertIn("call read with offset and limit around those matches", rendered_prompt)
            self.assertIn(
                "File and bash path arguments resolve from workspace_root. Prefer workspace-relative paths when possible; absolute paths are also supported when they stay inside the workspace.",
                rendered_prompt,
            )
            self.assertNotIn(
                "Use Windows absolute paths for read/write/edit file_path values; use bash working_directory for shell commands rooted in the workspace.",
                rendered_prompt,
            )
            self.assertIn("Use bash as a fallback for verification or composed shell commands", rendered_prompt)
            self.assertIn("tool.available", prompt_event["payload"]["request_system_module_ids"])
            self.assertIn("system.tool_result_reminders", prompt_event["payload"]["request_system_module_ids"])
            self.assertIn("system.token_budget", prompt_event["payload"]["request_system_module_ids"])
            self.assertNotIn("runtime.system_reminder", prompt_event["payload"].get("request_system_module_ids", []))
            self.assertNotIn("<system-reminder>", rendered_prompt)
            self.assertTrue(prompt_event["payload"]["static_prompt_created"])
            self.assertEqual(prompt_event["payload"]["injection_decision"]["reason"], "before_model_request")
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant"])
            self.assertIn(
                "<user-context>\n"
                "  <user-identity>default</user-identity>\n"
                "  <user-message>hello chat</user-message>\n"
                "</user-context>",
                loaded.history[0]["content"],
            )
            self.assertIn("<system-reminder>", loaded.history[0]["content"])
            self.assertIn("<available-bash-cli>", loaded.history[0]["content"])
            self.assertIn("<lora-chat>", loaded.history[0]["content"])
            self.assertIn("uv run lora chat --new -m", loaded.history[0]["content"])
            self.assertIn("uv run lora chat --session &lt;session_id&gt; -m", loaded.history[0]["content"])
            self.assertIn("Available via uv run in this workspace.", loaded.history[0]["content"])
            self.assertIn("当前系统时间为：", loaded.history[0]["content"])
            user_event = next(event for event in events if event["type"] == "conversation.user_message")
            self.assertEqual(user_event["payload"]["raw_content"], "hello chat")
            self.assertEqual(user_event["payload"]["user_identity"], "default")
            self.assertTrue(user_event["payload"]["wrapped"])
            self.assertEqual(
                [event["type"] for event in events],
                [
                    "conversation.user_message",
                    "model.request",
                    "context.projection_created",
                    "prompt.rendered",
                    "conversation.assistant_message",
                    "model.response",
                    "context.checkpoint",
                ],
            )

    def test_run_turn_user_context_wrapper_escapes_user_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", user_identity="dev<ops>")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            asyncio.run(
                AgentRuntimeAdapter(agent=RecordingAgent(), config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="search <src> & report",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            self.assertIn("<user-identity>dev&lt;ops&gt;</user-identity>", loaded.history[0]["content"])
            self.assertIn("<user-message>search &lt;src&gt; &amp; report</user-message>", loaded.history[0]["content"])

    def test_static_prompt_is_cached_per_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")

            first_run = manager.start_case_run(ref.session_id, "chat")
            first_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=first_session,
                    user_input="first",
                    case_run_ref=first_run,
                    turn_id="turn-0001",
                )
            )

            metadata_path = Path(ref.session_dir) / "context" / "prompts" / "static_prompt.json"
            text_path = Path(ref.session_dir) / "context" / "prompts" / "static_prompt.txt"
            first_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            first_text = text_path.read_text(encoding="utf-8")

            second_run = manager.start_case_run(ref.session_id, "chat")
            second_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=second_session,
                    user_input="second",
                    case_run_ref=second_run,
                    turn_id="turn-0002",
                )
            )

            second_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            second_text = text_path.read_text(encoding="utf-8")
            first_prompt_event = next(
                event for event in _read_jsonl(Path(first_run.run_dir) / "events.jsonl") if event["type"] == "prompt.rendered"
            )
            second_prompt_event = next(
                event
                for event in _read_jsonl(Path(second_run.run_dir) / "events.jsonl")
                if event["type"] == "prompt.rendered"
            )

            self.assertEqual(first_text, second_text)
            self.assertEqual(first_metadata["created_at"], second_metadata["created_at"])
            self.assertEqual(first_metadata["prompt_hash"], second_metadata["prompt_hash"])
            self.assertTrue(first_prompt_event["payload"]["static_prompt_created"])
            self.assertFalse(second_prompt_event["payload"]["static_prompt_created"])
            self.assertIn("tool.available", second_prompt_event["payload"]["request_system_module_ids"])
            self.assertIn("system.tool_result_reminders", second_prompt_event["payload"]["request_system_module_ids"])
            self.assertIn("system.token_budget", second_prompt_event["payload"]["request_system_module_ids"])
            self.assertNotIn("runtime.system_reminder", first_prompt_event["payload"].get("request_system_module_ids", []))
            self.assertNotIn("runtime.system_reminder", second_prompt_event["payload"].get("request_system_module_ids", []))
            first_rendered = Path(first_prompt_event["payload"]["prompt_text_path"]).read_text(encoding="utf-8")
            self.assertNotIn("<available-bash-cli>", first_rendered)

    def test_static_prompt_describes_multilevel_lora_paths_without_skill_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as user_tmp:
            workspace = Path(tmp)
            user_lora_root = Path(user_tmp) / ".lora"
            project_skill = workspace / ".lora" / "skills" / "code-review" / "SKILL.md"
            project_skill.parent.mkdir(parents=True, exist_ok=True)
            project_skill.write_text(
                "---\nname: code-review\ndescription: Review code changes.\n---\n",
                encoding="utf-8",
            )
            config = RunConfig(
                workspace_root=workspace,
                lora_root=workspace / ".lora",
                user_lora_root=user_lora_root,
            )
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="paths",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            static_prompt = (Path(ref.session_dir) / "context" / "prompts" / "static_prompt.txt").read_text(
                encoding="utf-8"
            )

            self.assertIn("# Lora Paths", static_prompt)
            self.assertIn(f"- Workspace root: {workspace.resolve()}", static_prompt)
            self.assertIn(f"- Project Lora root: {(workspace / '.lora').resolve()}", static_prompt)
            self.assertIn(f"- User Lora root: {user_lora_root.resolve()}", static_prompt)
            self.assertIn("Bash commands and file tools resolve relative paths from the workspace root.", static_prompt)
            self.assertNotIn("<available-skills>", static_prompt)
            self.assertNotIn("code-review", static_prompt)

    def test_first_request_renders_skill_directory_and_available_skills_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            skills_dir = workspace / ".lora" / "skills"
            skill_file = skills_dir / "code-review" / "SKILL.md"
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(
                "\n".join(
                    [
                        "---",
                        "name: code-review",
                        "description: Review code changes and prioritize bugs.",
                        "---",
                        "# Code Review",
                    ]
                ),
                encoding="utf-8",
            )
            config = RunConfig(workspace_root=workspace, lora_root=workspace / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")

            first_run = manager.start_case_run(ref.session_id, "chat")
            first_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=first_session,
                    user_input="first",
                    case_run_ref=first_run,
                    turn_id="turn-0001",
                )
            )
            second_run = manager.start_case_run(ref.session_id, "chat")
            second_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=second_session,
                    user_input="second",
                    case_run_ref=second_run,
                    turn_id="turn-0002",
                )
            )

            loaded = manager.load(ref.session_id)
            first_user_message = loaded.history[0]["content"]
            second_user_message = loaded.history[2]["content"]
            static_prompt = (Path(ref.session_dir) / "context" / "prompts" / "static_prompt.txt").read_text(
                encoding="utf-8"
            )

            self.assertIn("<skills-context>", first_user_message)
            self.assertIn(f"<skills-directory>{workspace / '.lora' / 'skills'}</skills-directory>", first_user_message)
            self.assertIn("<available-skills>", first_user_message)
            self.assertIn("<name>code-review</name>", first_user_message)
            self.assertIn("<description>Review code changes and prioritize bugs.</description>", first_user_message)
            self.assertNotIn("<skills-context>", second_user_message)
            self.assertNotIn("<skills-context>", static_prompt)

    def test_available_skills_include_user_and_project_sources_with_project_shadowing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as user_tmp:
            workspace = Path(tmp)
            user_lora_root = Path(user_tmp) / ".lora"
            user_skill = user_lora_root / "skills" / "code-review" / "SKILL.md"
            project_skill = workspace / ".lora" / "skills" / "code-review" / "SKILL.md"
            user_only_skill = user_lora_root / "skills" / "python-style" / "SKILL.md"
            for path, description in [
                (user_skill, "General user-level review guidance."),
                (project_skill, "Project-specific review guidance."),
                (user_only_skill, "Shared Python style guidance."),
            ]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "\n".join(
                        [
                            "---",
                            f"name: {path.parent.name}",
                            f"description: {description}",
                            "---",
                            "# Skill",
                        ]
                    ),
                    encoding="utf-8",
                )
            config = RunConfig(
                workspace_root=workspace,
                lora_root=workspace / ".lora",
                user_lora_root=user_lora_root,
            )
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="skills",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            rendered = loaded.history[0]["content"]
            state = json.loads((Path(ref.session_dir) / "state" / "skill_context.json").read_text(encoding="utf-8"))

            self.assertIn("<user-skills-directory>", rendered)
            self.assertIn(str((user_lora_root / "skills").resolve()), rendered)
            self.assertIn("<project-skills-directory>", rendered)
            self.assertIn(str((workspace / ".lora" / "skills").resolve()), rendered)
            self.assertIn("<available-skills>", rendered)
            self.assertIn("<name>code-review</name>", rendered)
            self.assertIn("<description>Project-specific review guidance.</description>", rendered)
            self.assertIn("<scope>project</scope>", rendered)
            self.assertIn("<uri>project://skills/code-review/SKILL.md</uri>", rendered)
            self.assertIn("<shadowed>", rendered)
            self.assertIn("<uri>user://skills/code-review/SKILL.md</uri>", rendered)
            self.assertIn("<name>python-style</name>", rendered)
            self.assertIn("<scope>user</scope>", rendered)
            self.assertIn("<uri>user://skills/python-style/SKILL.md</uri>", rendered)
            self.assertEqual(state["user_skills_dir"], str((user_lora_root / "skills").resolve()))
            self.assertEqual(state["project_skills_dir"], str((workspace / ".lora" / "skills").resolve()))
            self.assertIn("skills_fingerprint", state)

    def test_second_request_does_not_scan_skills_when_no_tool_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            skills_dir = workspace / ".lora" / "skills"
            skill_file = skills_dir / "code-review" / "SKILL.md"
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(
                "---\nname: code-review\ndescription: Review code changes.\n---\n",
                encoding="utf-8",
            )
            config = RunConfig(workspace_root=workspace, lora_root=workspace / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")

            first_run = manager.start_case_run(ref.session_id, "chat")
            first_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=first_session,
                    user_input="first",
                    case_run_ref=first_run,
                    turn_id="turn-0001",
                )
            )
            skill_file.unlink()
            second_run = manager.start_case_run(ref.session_id, "chat")
            second_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=second_session,
                    user_input="second",
                    case_run_ref=second_run,
                    turn_id="turn-0002",
                )
            )

            loaded = manager.load(ref.session_id)
            second_user_message = loaded.history[2]["content"]

            self.assertNotIn("runtime.skill_reminder", second_user_message)
            self.assertNotIn("<skills-context>", second_user_message)

    def test_bash_tool_detects_new_skill_and_reminds_before_next_model_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = RunConfig(workspace_root=workspace, lora_root=workspace / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            state_dir = Path(ref.session_dir) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "skill_context.json").write_text(
                json.dumps(
                    {
                        "initial_skill_context_injected": True,
                        "skills_dir": str(workspace / ".lora" / "skills"),
                        "skills_dir_fingerprint": "",
                        "known_skills": {},
                        "pending_new_skills": [],
                        "pending_system_reminders": [],
                    }
                ),
                encoding="utf-8",
            )
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            agent = FakeBashLoraAgent(config)
            agent.llm = BashCreateSkillThenAnswerLLM(workspace / ".lora" / "skills")

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="install a skill",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            prompt_events = [
                event for event in _read_jsonl(Path(run.run_dir) / "events.jsonl") if event["type"] == "prompt.rendered"
            ]
            loaded = manager.load(ref.session_id)
            tool_messages = [message for message in loaded.history if message["role"] == "tool"]
            state = json.loads((state_dir / "skill_context.json").read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "passed")
            self.assertEqual(len(prompt_events), 2)
            self.assertNotIn("runtime.skill_reminder", prompt_events[1]["payload"].get("request_system_module_ids", []))
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("<system-reminder>", tool_messages[0]["content"])
            self.assertIn("<new-skills>", tool_messages[0]["content"])
            self.assertIn("<name>debugging</name>", tool_messages[0]["content"])
            self.assertIn("<description>Investigate failures systematically before proposing fixes.</description>", tool_messages[0]["content"])
            self.assertEqual(state["pending_new_skills"], [])
            self.assertEqual(state["pending_system_reminders"], [])

    def test_project_skill_shadowing_known_user_skill_triggers_new_skill_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as user_tmp:
            workspace = Path(tmp)
            user_lora_root = Path(user_tmp) / ".lora"
            user_skill = user_lora_root / "skills" / "code-review" / "SKILL.md"
            user_skill.parent.mkdir(parents=True, exist_ok=True)
            user_skill.write_text(
                "---\nname: code-review\ndescription: User-level review guidance.\n---\n",
                encoding="utf-8",
            )
            config = RunConfig(
                workspace_root=workspace,
                lora_root=workspace / ".lora",
                user_lora_root=user_lora_root,
                max_steps=3,
            )
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            state_dir = Path(ref.session_dir) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            user_skill_entry = {
                "name": "code-review",
                "description": "User-level review guidance.",
                "path": str(user_skill.resolve()),
                "content_hash": "user-hash",
                "scope": "user",
                "uri": "user://skills/code-review/SKILL.md",
                "shadowed": [],
            }
            (state_dir / "skill_context.json").write_text(
                json.dumps(
                    {
                        "initial_skill_context_injected": True,
                        "skills_dir": str(workspace / ".lora" / "skills"),
                        "skills_dir_fingerprint": "",
                        "user_skills_dir": str((user_lora_root / "skills").resolve()),
                        "project_skills_dir": str((workspace / ".lora" / "skills").resolve()),
                        "skills_fingerprint": "old",
                        "known_skills": {"code-review": user_skill_entry},
                        "pending_new_skills": [],
                        "pending_system_reminders": [],
                    }
                ),
                encoding="utf-8",
            )
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            agent = FakeBashLoraAgent(config)
            agent.llm = BashCreateNamedSkillThenAnswerLLM(
                workspace / ".lora" / "skills",
                name="code-review",
                description="Project-level review guidance.",
            )

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="add project skill",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            prompt_events = [
                event for event in _read_jsonl(Path(run.run_dir) / "events.jsonl") if event["type"] == "prompt.rendered"
            ]
            loaded = manager.load(ref.session_id)
            tool_messages = [message for message in loaded.history if message["role"] == "tool"]

            self.assertEqual(result["status"], "passed")
            self.assertNotIn("runtime.skill_reminder", prompt_events[1]["payload"].get("request_system_module_ids", []))
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("<new-skills>", tool_messages[0]["content"])
            self.assertIn("<description>Project-level review guidance.</description>", tool_messages[0]["content"])
            self.assertIn("<scope>project</scope>", tool_messages[0]["content"])
            self.assertIn("<uri>project://skills/code-review/SKILL.md</uri>", tool_messages[0]["content"])
            self.assertIn("<shadowed>", tool_messages[0]["content"])
            self.assertIn("<uri>user://skills/code-review/SKILL.md</uri>", tool_messages[0]["content"])

    def test_pending_new_cli_is_rendered_once_with_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            state_dir = Path(ref.session_dir) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "cli_context.json").write_text(
                json.dumps(
                    {
                        "initial_available_cli_injected": True,
                        "known_bash_cli": {},
                        "pending_new_bash_cli": [
                            {
                                "name": "pyright",
                                "description": "Python type checker.",
                                "installed": True,
                                "detected_at": "2026-05-29T06:05:00Z",
                                "source": "tool_result",
                                "include_time": True,
                            }
                        ],
                        "pending_system_reminders": [
                            {
                                "kind": "cli_context",
                                "include_time": True,
                                "created_at": "2026-05-29T06:05:00Z",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            first_run = manager.start_case_run(ref.session_id, "chat")
            first_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=first_session,
                    user_input="notice new cli",
                    case_run_ref=first_run,
                    turn_id="turn-0001",
                )
            )
            second_run = manager.start_case_run(ref.session_id, "chat")
            second_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=second_session,
                    user_input="no repeat",
                    case_run_ref=second_run,
                    turn_id="turn-0002",
                )
            )

            loaded = manager.load(ref.session_id)
            first_user_message = loaded.history[0]["content"]
            second_user_message = loaded.history[2]["content"]
            state = json.loads((state_dir / "cli_context.json").read_text(encoding="utf-8"))

            self.assertIn("<new-bash-cli>", first_user_message)
            self.assertIn("<pyright>", first_user_message)
            self.assertIn("当前系统时间为：", first_user_message)
            self.assertNotIn("<new-bash-cli>", second_user_message)
            self.assertEqual(state["pending_new_bash_cli"], [])
            self.assertEqual(state["pending_system_reminders"], [])

    def test_bash_tool_detects_new_cli_before_next_model_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(
                workspace_root=tmp,
                lora_root=Path(tmp) / ".lora",
                max_steps=3,
                cli_bash_presets=[
                    BashCliPreset(
                        name="madeupcli",
                        command="madeupcli --help",
                        description="Newly installed CLI.",
                    )
                ],
            )
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            state_dir = Path(ref.session_dir) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "cli_context.json").write_text(
                json.dumps(
                    {
                        "initial_available_cli_injected": True,
                        "known_bash_cli": {
                            "madeupcli": {
                                "name": "madeupcli",
                                "description": "Newly installed CLI.",
                                "installed": False,
                                "detected_at": "2026-05-29T06:00:00Z",
                            }
                        },
                        "pending_new_bash_cli": [],
                        "pending_system_reminders": [],
                    }
                ),
                encoding="utf-8",
            )
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            agent = FakeBashLoraAgent(config)
            agent.llm = BashThenAnswerLLM()

            with patch("lora.agent.shutil.which", return_value="C:/tools/madeupcli.exe"):
                result = asyncio.run(
                    AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                        session=session,
                        user_input="install the cli",
                        case_run_ref=run,
                        turn_id="turn-0001",
                    )
                )

            prompt_events = [
                event for event in _read_jsonl(Path(run.run_dir) / "events.jsonl") if event["type"] == "prompt.rendered"
            ]
            loaded = manager.load(ref.session_id)
            tool_messages = [message for message in loaded.history if message["role"] == "tool"]

            self.assertEqual(result["status"], "passed")
            self.assertEqual(len(prompt_events), 2)
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("<new-bash-cli>", tool_messages[0]["content"])
            self.assertIn("<madeupcli>", tool_messages[0]["content"])
            self.assertIn("当前系统时间为：", tool_messages[0]["content"])

    def test_bash_install_appends_new_cli_reminder_to_tool_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            agent = FakeBashLoraAgent(config)
            agent.llm = BashThenAnswerLLM()

            with patch("lora.agent.shutil.which", return_value="C:/tools/typescript-language-server.cmd"):
                asyncio.run(
                    AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                        session=session,
                        user_input="install typescript-language-server",
                        case_run_ref=run,
                        turn_id="turn-0001",
                    )
                )

            loaded = manager.load(ref.session_id)
            tool_messages = [message for message in loaded.history if message["role"] == "tool"]

            self.assertEqual(len(tool_messages), 1)
            self.assertIn("<system-reminder>", tool_messages[0]["content"])
            self.assertIn("<new-bash-cli>", tool_messages[0]["content"])
            self.assertIn("<typescript-language-server>", tool_messages[0]["content"])

    def test_model_events_include_safe_agent_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = ResolvedAgentConfig(
                alias="dev",
                model_name="profile-model",
                api_key=None,
                api_key_source="missing",
                base_url="https://profile.example/v1",
            )
            config = RunConfig(
                workspace_root=tmp,
                lora_root=Path(tmp) / ".lora",
                agent_alias="dev",
                model_name="profile-model",
                api_key_source="missing",
                base_url="https://profile.example/v1",
                resolved_agent=profile,
            )
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            result = asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="hello",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            request = next(event for event in events if event["type"] == "model.request")
            response = next(event for event in events if event["type"] == "model.response")

            self.assertEqual(result["status"], "passed")
            self.assertIn("agent alias 'dev'", result["final_answer"])
            self.assertEqual(request["payload"]["agent_alias"], "dev")
            self.assertEqual(request["payload"]["model_name"], "profile-model")
            self.assertEqual(request["payload"]["api_key_source"], "missing")
            self.assertEqual(response["payload"]["agent_alias"], "dev")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
