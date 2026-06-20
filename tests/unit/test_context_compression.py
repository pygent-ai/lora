from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pygent.message import AssistantMessage, AssistantMessageChunk

from lora.config import load_run_config
from lora.runtime import (
    ContextCompressionModelResult,
    ContextCompressionRunner,
    collect_recent_file_reads,
    parse_summary,
    render_file_read_block,
)
from lora.core.io import append_jsonl
from lora.runtime import LoraAgent
from lora.runtime import AgentRuntimeAdapter
from lora.schema import AgentSession, RunConfig
from lora.sessions import SessionManager


SUMMARY_WITH_TRANSCRIPT = (
    "If you need specific details from before compaction (like exact code snippets, error messages, or content you "
    "generated), read the full transcript at:"
)


class ContextCompressionPureTests(unittest.TestCase):
    def test_parse_summary_requires_non_empty_summary_tags(self) -> None:
        self.assertEqual(parse_summary("<analysis>x</analysis><summary>\n这是压缩摘要\n</summary>"), "这是压缩摘要")
        self.assertIsNone(parse_summary("这是普通文本"))
        self.assertIsNone(parse_summary("<summary>   </summary>"))
        self.assertIsNone(parse_summary("</summary><summary>bad"))
        self.assertIsNone(parse_summary("<summary>valid</summary>", has_tool_call=True))

    def test_file_read_block_restores_recent_five_and_hides_only_over_limit_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            logs_dir = session_dir / "logs"
            for index in range(1, 8):
                append_jsonl(
                    logs_dir / "file_events.jsonl",
                    {
                        "type": "file.read",
                        "path": f"src/file_{index}.py",
                        "created_at": f"2026-06-15T00:00:0{index}+00:00",
                        "payload": {
                            "path": f"src/file_{index}.py",
                            "returned_range": {"unit": "line", "start": index, "end": index + 10},
                            "returned_content": f"content-{index}",
                        },
                    },
                )
            append_jsonl(
                logs_dir / "file_events.jsonl",
                {
                    "type": "file.read",
                    "path": "src/large.py",
                    "created_at": "2026-06-15T00:00:08+00:00",
                    "payload": {
                        "path": "src/large.py",
                        "returned_range": {"unit": "line", "start": 120, "end": 220},
                        "returned_content": "x" * 5001,
                    },
                },
            )

            records = collect_recent_file_reads(session_dir, count=5)
            xml, metadata = render_file_read_block(records, max_chars=5000)

            self.assertNotIn("src/file_1.py", xml)
            self.assertNotIn("src/file_2.py", xml)
            self.assertIn("src/file_4.py", xml)
            self.assertLess(xml.index("src/file_4.py"), xml.index("src/file_7.py"))
            self.assertIn('path="src/large.py"', xml)
            self.assertIn('range="120-220"', xml)
            self.assertIn('truncated="true"', xml)
            self.assertIn('char_count="5001"', xml)
            self.assertIn(
                "The previous file read result is too large to include in this compacted context",
                xml,
            )
            self.assertNotIn("x" * 100, xml)
            self.assertEqual(metadata[-1]["included"], False)
            self.assertEqual(metadata[-1]["char_count"], 5001)
            self.assertEqual(metadata[-1]["truncated"], True)

    def test_file_read_length_threshold_includes_4999_and_5000_but_not_5001(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            for name, size in (("lt", 4999), ("eq", 5000), ("gt", 5001)):
                append_jsonl(
                    session_dir / "logs" / "file_events.jsonl",
                    {
                        "type": "file.read",
                        "path": f"src/{name}.txt",
                        "created_at": f"2026-06-15T00:00:0{len(name)}+00:00",
                        "payload": {
                            "path": f"src/{name}.txt",
                            "returned_range": {"unit": "full", "start": 1, "end": "EOF"},
                            "returned_content": name * (size // len(name)) + name[: size % len(name)],
                        },
                    },
                )

            xml, metadata = render_file_read_block(collect_recent_file_reads(session_dir), max_chars=5000)

            self.assertIn('path="src/lt.txt" mode="full" truncated="false" char_count="4999"', xml)
            self.assertIn('path="src/eq.txt" mode="full" truncated="false" char_count="5000"', xml)
            self.assertIn('path="src/gt.txt" mode="full" truncated="true" char_count="5001"', xml)
            self.assertEqual([item["included"] for item in metadata], [True, True, False])

    def test_context_compression_config_resolves_from_model_and_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "agent:",
                        "  default_alias: dev",
                        "agents:",
                        "  - alias: dev",
                        "    model_request:",
                        "      model_name: profile-model",
                        "      context_window: 32000",
                        "context_compression:",
                        "  enabled: true",
                        "  trigger_ratio: 0.75",
                        "  file_read_count: 4",
                        "  file_read_max_chars: 1234",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_run_config(workspace_root=root)

            self.assertEqual(config.context_window, 32000)
            self.assertTrue(config.context_compression_enabled)
            self.assertEqual(config.context_compression_trigger_ratio, 0.75)
            self.assertEqual(config.context_compression_file_read_count, 4)
            self.assertEqual(config.context_compression_file_read_max_chars, 1234)


class ContextCompressionRunnerTests(unittest.TestCase):
    def test_runner_skips_below_trigger_and_does_not_write_model_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = _session(tmp, token_usage={"latest_input_tokens": 800, "latest_output_tokens": 99})
            config = RunConfig(
                workspace_root=tmp,
                lora_root=Path(tmp) / ".lora",
                context_window=1000,
            )
            calls: list[bool] = []

            decision = asyncio.run(
                ContextCompressionRunner(config=config, session_dir=Path(tmp)).maybe_compact(
                    session=session,
                    system_prompt="system prompt",
                    model_messages=[{"role": "user", "content": "hello"}],
                    history_cutoff=1,
                    call_model=_recording_model(calls, "<summary>unused</summary>"),
                )
            )

            self.assertEqual(decision.status, "skipped")
            self.assertEqual(session.status, "normal")
            self.assertFalse((Path(tmp) / "model_context.json").exists())
            self.assertEqual(calls, [])

    def test_runner_compacts_at_threshold_preserving_system_and_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = _session(tmp, token_usage={"latest_input_tokens": 800, "latest_output_tokens": 100})
            session.history = [
                {"role": "user", "content": "older user"},
                {"role": "assistant", "content": "older assistant"},
                {"role": "user", "content": "latest\n\n<system-reminder>\n原始 reminder 内容\n</system-reminder>"},
            ]
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", context_window=1000)
            append_jsonl(
                Path(tmp) / "logs" / "file_events.jsonl",
                {
                    "type": "file.read",
                    "path": "src/a.ts",
                    "created_at": "2026-06-15T00:00:00+00:00",
                    "payload": {
                        "path": "src/a.ts",
                        "returned_range": {"unit": "line", "start": 120, "end": 220},
                        "returned_content": "lines 120-220",
                    },
                },
            )

            decision = asyncio.run(
                ContextCompressionRunner(config=config, session_dir=Path(tmp)).maybe_compact(
                    session=session,
                    system_prompt="system prompt",
                    model_messages=session.history,
                    history_cutoff=len(session.history),
                    call_model=_recording_model([], "<analysis>x</analysis><summary>压缩摘要</summary>"),
                )
            )

            self.assertEqual(decision.status, "compacted")
            self.assertEqual(session.status, "compacted")
            self.assertEqual(decision.messages[0], {"role": "system", "content": "system prompt"})
            self.assertEqual(decision.messages[1]["role"], "user")
            content = decision.messages[1]["content"]
            self.assertLess(content.index("<system-reminder>"), content.index("<session-context>"))
            self.assertIn("<system-reminder>\n原始 reminder 内容\n</system-reminder>", content)
            self.assertIn("<file-read>", content)
            self.assertIn('path="src/a.ts" mode="partial" range="120-220"', content)
            self.assertIn("<summary>", content)
            self.assertIn("压缩摘要", content)
            self.assertIn(SUMMARY_WITH_TRANSCRIPT, content)

            model_context = json.loads((Path(tmp) / "model_context.json").read_text(encoding="utf-8"))
            self.assertTrue(model_context["is_compacted"])
            self.assertEqual(model_context["history_cutoff"], len(session.history))
            self.assertEqual(model_context["messages"], decision.messages)
            compactions = _read_jsonl(Path(tmp) / "compactions.jsonl")
            self.assertEqual(len(compactions), 1)
            self.assertEqual(compactions[0]["attempts_with_tools"], 1)
            self.assertEqual(compactions[0]["attempts_without_tools"], 0)
            self.assertFalse(compactions[0]["fallback_without_tools"])
            self.assertEqual(len(session.history), 3)
            self.assertFalse((Path(tmp) / "sessions").exists())

    def test_runner_retries_tool_calls_then_falls_back_without_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = _session(tmp, token_usage={"latest_input_tokens": 900, "latest_output_tokens": 0})
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", context_window=1000)
            tool_modes: list[bool] = []

            async def call_model(
                messages: list[dict[str, Any]], *, system_prompt: str, tools_enabled: bool
            ) -> ContextCompressionModelResult:
                tool_modes.append(tools_enabled)
                if tools_enabled:
                    return ContextCompressionModelResult(text="<summary>ignored</summary>", has_tool_call=True)
                return ContextCompressionModelResult(text="<summary>fallback summary</summary>", has_tool_call=False)

            decision = asyncio.run(
                ContextCompressionRunner(config=config, session_dir=Path(tmp)).maybe_compact(
                    session=session,
                    system_prompt="system",
                    model_messages=[{"role": "user", "content": "<system-reminder>\nR\n</system-reminder>"}],
                    history_cutoff=1,
                    call_model=call_model,
                )
            )

            self.assertEqual(decision.status, "compacted")
            self.assertEqual(tool_modes, [True, True, True, True, True, False])
            record = _read_jsonl(Path(tmp) / "compactions.jsonl")[0]
            self.assertEqual(record["attempts_with_tools"], 5)
            self.assertEqual(record["attempts_without_tools"], 1)
            self.assertTrue(record["fallback_without_tools"])

    def test_runner_marks_session_failed_after_ten_failed_attempts_without_replacing_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = _session(tmp, token_usage={"latest_input_tokens": 900, "latest_output_tokens": 0})
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", context_window=1000)
            calls: list[bool] = []

            decision = asyncio.run(
                ContextCompressionRunner(config=config, session_dir=Path(tmp)).maybe_compact(
                    session=session,
                    system_prompt="system",
                    model_messages=[{"role": "user", "content": "hello"}],
                    history_cutoff=1,
                    call_model=_recording_model(calls, "no summary"),
                )
            )

            self.assertEqual(decision.status, "failed")
            self.assertEqual(session.status, "compression_failed")
            self.assertEqual(calls, [True, True, True, True, True, False, False, False, False, False])
            self.assertFalse((Path(tmp) / "model_context.json").exists())

    def test_concurrent_compaction_for_same_session_runs_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_a = _session(tmp, token_usage={"latest_input_tokens": 900, "latest_output_tokens": 0})
            session_b = _session(tmp, token_usage={"latest_input_tokens": 900, "latest_output_tokens": 0})
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", context_window=1000)
            call_count = 0

            async def slow_model(
                messages: list[dict[str, Any]], *, system_prompt: str, tools_enabled: bool
            ) -> ContextCompressionModelResult:
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.01)
                return ContextCompressionModelResult(text="<summary>once</summary>", has_tool_call=False)

            async def run_both() -> list[str]:
                runner = ContextCompressionRunner(config=config, session_dir=Path(tmp))
                decisions = await asyncio.gather(
                    runner.maybe_compact(
                        session=session_a,
                        system_prompt="system",
                        model_messages=[{"role": "user", "content": "<system-reminder>\nR\n</system-reminder>"}],
                        history_cutoff=1,
                        call_model=slow_model,
                    ),
                    runner.maybe_compact(
                        session=session_b,
                        system_prompt="system",
                        model_messages=[{"role": "user", "content": "<system-reminder>\nR\n</system-reminder>"}],
                        history_cutoff=1,
                        call_model=slow_model,
                    ),
                )
                return [decision.status for decision in decisions]

            statuses = asyncio.run(run_both())

            self.assertEqual(statuses, ["compacted", "compacted"])
            self.assertEqual(call_count, 1)
            self.assertEqual(len(_read_jsonl(Path(tmp) / "compactions.jsonl")), 1)


class ContextCompressionStreamIntegrationTests(unittest.TestCase):
    def test_lora_agent_compacts_before_next_model_request_and_keeps_gui_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(
                workspace_root=tmp,
                lora_root=Path(tmp) / ".lora",
                max_steps=1,
                context_window=1000,
            )
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            session = manager.load(ref.session_id)
            session.history = [
                {"role": "user", "content": "<system-reminder>\nR\n</system-reminder>\nold user"},
                {
                    "role": "assistant",
                    "content": "old assistant that must not remain model-visible",
                    "usage": {"prompt_tokens": 800, "completion_tokens": 100, "total_tokens": 900},
                },
            ]
            session.token_usage = {
                "latest_input_tokens": 800,
                "latest_output_tokens": 100,
                "latest_context_tokens": 900,
            }
            manager.save(session)
            run = manager.start_case_run(ref.session_id, "chat")
            agent = LoraAgent(config)
            llm = CompressionThenAnswerLLM()
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=manager.load(ref.session_id),
                    user_input="continue",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            model_context = json.loads((Path(ref.session_dir) / "model_context.json").read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "passed")
            self.assertEqual(loaded.status, "compacted")
            self.assertEqual([message["role"] for message in model_context["messages"]], ["system", "user"])
            self.assertIn("<session-context>", model_context["messages"][1]["content"])
            latest_reminder = _extract_system_reminder(loaded.history[2]["content"])
            self.assertIn(latest_reminder, model_context["messages"][1]["content"])
            self.assertIn("compressed summary", model_context["messages"][1]["content"])
            self.assertEqual(len(llm.requests), 2)
            self.assertTrue(llm.requests[0]["tools_enabled"])
            self.assertIn("CRITICAL: Respond with TEXT ONLY", llm.requests[0]["messages"][-1]["content"])
            self.assertEqual([message["role"] for message in llm.requests[1]["messages"]], ["system", "user"])
            self.assertIn("<session-context>", llm.requests[1]["messages"][1]["content"])
            self.assertNotIn("old assistant that must not remain model-visible", llm.requests[1]["messages"][1]["content"])
            self.assertGreaterEqual(len(loaded.history), 4)
            self.assertIn("old assistant that must not remain model-visible", loaded.history[1]["content"])


class CompressionThenAnswerLLM:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def stream_forward(self, context: Any, **kwargs: Any):
        messages = [message.to_dict() for message in context.history.data]
        self.requests.append(
            {
                "messages": messages,
                "system_prompt": str(context.system_prompt),
                "tools_enabled": bool(kwargs.get("tools")),
            }
        )
        if len(self.requests) == 1:
            text = "<analysis>x</analysis><summary>compressed summary</summary>"
            yield AssistantMessageChunk(content=text)
            context.add_message(AssistantMessage(content=text))
            return
        yield AssistantMessageChunk(content="continued")
        context.add_message(
            AssistantMessage(
                content="continued",
                usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            )
        )


def _session(tmp: str, *, token_usage: dict[str, int]) -> AgentSession:
    return AgentSession(
        session_id="chat-test",
        workspace_root=tmp,
        session_dir=tmp,
        created_at="2026-06-15T00:00:00+00:00",
        updated_at="2026-06-15T00:00:00+00:00",
        status="normal",
        token_usage=token_usage,
        history=[],
    )


def _recording_model(
    calls: list[bool],
    text: str,
) -> Callable[[list[dict[str, Any]]], Awaitable[ContextCompressionModelResult]]:
    async def call_model(
        messages: list[dict[str, Any]], *, system_prompt: str, tools_enabled: bool
    ) -> ContextCompressionModelResult:
        calls.append(tools_enabled)
        return ContextCompressionModelResult(text=text, has_tool_call=False)

    return call_model


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _extract_system_reminder(content: str) -> str:
    start = content.index("<system-reminder>")
    end = content.index("</system-reminder>", start) + len("</system-reminder>")
    return content[start:end]


if __name__ == "__main__":
    unittest.main()
