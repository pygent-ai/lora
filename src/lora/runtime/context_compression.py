from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Literal

from lora.core.io import append_jsonl, read_json, utc_now, write_json
from lora.core.redaction import redact_secrets
from lora.schema import AgentSession, RunConfig
from lora.tracing import EventStore


COMPRESSION_REQUEST_PROMPT = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed by a <summary> block. Tool calls will be rejected and you will fail the task."""

TRANSCRIPT_POINTER = (
    "If you need specific details from before compaction (like exact code snippets, error messages, or content you "
    "generated), read the full transcript at:"
)
TOO_LARGE_FILE_READ_TEMPLATE = (
    "The previous file read result is too large to include in this compacted context, so its details are not shown "
    "here. Path: {path}. Read scope: {scope}. Returned content length: {char_count} characters. Re-read this exact "
    "file or range if you need the content."
)


@dataclass(frozen=True, slots=True)
class ContextCompressionModelResult:
    text: str
    has_tool_call: bool = False


@dataclass(frozen=True, slots=True)
class FileReadRecord:
    path: str
    mode: Literal["full", "partial"]
    content: str
    range: str | None = None
    created_at: str | None = None


@dataclass(slots=True)
class ContextCompressionDecision:
    status: Literal["skipped", "compacted", "failed"]
    messages: list[dict[str, str]] = field(default_factory=list)
    reason: str | None = None
    compaction_id: str | None = None


CompressionModelCaller = Callable[
    [list[dict[str, Any]]],
    Awaitable[ContextCompressionModelResult],
]

_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()


def parse_summary(text: str, *, has_tool_call: bool = False) -> str | None:
    if has_tool_call:
        return None
    start = text.find("<summary>")
    if start < 0:
        return None
    start += len("<summary>")
    end = text.find("</summary>", start)
    if end < 0:
        return None
    summary = text[start:end].strip()
    return summary or None


def collect_recent_file_reads(session_dir: str | Path, *, count: int = 5) -> list[FileReadRecord]:
    root = Path(session_dir)
    records = _collect_file_event_reads(root)
    if not records:
        records = _collect_tool_result_reads(root)
    return records[-count:]


def render_file_read_block(
    records: list[FileReadRecord],
    *,
    max_chars: int = 5000,
) -> tuple[str, list[dict[str, Any]]]:
    lines = ["<file-read>"]
    metadata: list[dict[str, Any]] = []
    for record in records:
        char_count = len(record.content)
        truncated = char_count > max_chars
        attrs = [
            f'path="{escape(record.path, quote=True)}"',
            f'mode="{record.mode}"',
        ]
        if record.range is not None:
            attrs.append(f'range="{escape(record.range, quote=True)}"')
        attrs.extend([f'truncated="{str(truncated).lower()}"', f'char_count="{char_count}"'])
        if truncated:
            scope = "full file" if record.mode == "full" else f"lines {record.range}"
            body = TOO_LARGE_FILE_READ_TEMPLATE.format(
                path=record.path,
                scope=scope,
                char_count=char_count,
            )
        else:
            body = record.content
        lines.extend([f"<file {' '.join(attrs)}>", body, "</file>"])
        row: dict[str, Any] = {
            "path": record.path,
            "mode": record.mode,
            "included": not truncated,
            "char_count": char_count,
            "truncated": truncated,
        }
        if record.range is not None:
            row["range"] = record.range
        metadata.append(row)
    lines.append("</file-read>")
    return "\n".join(lines), metadata


def load_model_context(session_dir: str | Path) -> dict[str, Any] | None:
    path = Path(session_dir) / "model_context.json"
    if not path.exists():
        return None
    return read_json(path)


class ContextCompressionRunner:
    def __init__(self, *, config: RunConfig, session_dir: str | Path):
        self.config = config
        self.session_dir = Path(session_dir)

    async def maybe_compact(
        self,
        *,
        session: AgentSession,
        system_prompt: str,
        model_messages: list[dict[str, Any]],
        history_cutoff: int,
        call_model: Callable[..., Awaitable[ContextCompressionModelResult]],
    ) -> ContextCompressionDecision:
        lock = await _session_lock(session.session_id)
        async with lock:
            self._refresh_session_state(session)
            if session.status == "compacted":
                return self._already_compacted_decision(session)
            if session.status == "compression_failed":
                return ContextCompressionDecision(status="failed", reason="session compression already failed")
            if not self._should_compact(session):
                return ContextCompressionDecision(status="skipped", reason="below threshold")

            session.status = "compressing"
            _persist_session(session)
            compaction_id = f"compact_{uuid.uuid4().hex}"
            transcript_path = self._write_transcript(
                session=session,
                system_prompt=system_prompt,
                model_messages=model_messages,
            )
            compression_messages = [
                *model_messages,
                {"role": "user", "content": COMPRESSION_REQUEST_PROMPT},
            ]
            attempts_with_tools = 0
            attempts_without_tools = 0
            summary: str | None = None

            for _ in range(5):
                attempts_with_tools += 1
                result = await _safe_call_model(
                    call_model,
                    compression_messages,
                    system_prompt=system_prompt,
                    tools_enabled=True,
                )
                if result is not None:
                    summary = parse_summary(result.text, has_tool_call=result.has_tool_call)
                    if summary is not None:
                        break

            if summary is None:
                for _ in range(5):
                    attempts_without_tools += 1
                    result = await _safe_call_model(
                        call_model,
                        compression_messages,
                        system_prompt=system_prompt,
                        tools_enabled=False,
                    )
                    if result is not None:
                        summary = parse_summary(result.text, has_tool_call=result.has_tool_call)
                        if summary is not None:
                            break

            if summary is None:
                session.status = "compression_failed"
                session.metadata["context_compression_error"] = "summary parsing failed after 10 attempts"
                _persist_session(session)
                return ContextCompressionDecision(status="failed", reason="summary parsing failed")

            file_read_xml, file_read_metadata = render_file_read_block(
                collect_recent_file_reads(
                    self.session_dir,
                    count=self.config.context_compression_file_read_count,
                ),
                max_chars=self.config.context_compression_file_read_max_chars,
            )
            summary_with_transcript = _summary_with_transcript(summary, transcript_path)
            user_content = _render_continuation_user_message(
                system_reminder=_extract_system_reminder(model_messages),
                file_read_xml=file_read_xml,
                summary=summary_with_transcript,
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            model_context = {
                "session_id": session.session_id,
                "is_compacted": True,
                "compaction_id": compaction_id,
                "history_cutoff": history_cutoff,
                "messages": messages,
            }
            write_json(self.session_dir / "model_context.json", model_context)
            append_jsonl(
                self.session_dir / "compactions.jsonl",
                {
                    "compaction_id": compaction_id,
                    "session_id": session.session_id,
                    "created_at": utc_now(),
                    "summary": summary_with_transcript,
                    "transcript_path": str(transcript_path),
                    "file_reads": file_read_metadata,
                    "attempts_with_tools": attempts_with_tools,
                    "attempts_without_tools": attempts_without_tools,
                    "fallback_without_tools": attempts_without_tools > 0,
                },
            )
            session.status = "compacted"
            session.metadata["context_compression_status"] = "compacted"
            session.metadata["context_compression_id"] = compaction_id
            _persist_session(session)
            return ContextCompressionDecision(status="compacted", messages=messages, compaction_id=compaction_id)

    def _refresh_session_state(self, session: AgentSession) -> None:
        path = self.session_dir / "session.json"
        if not path.exists():
            return
        data = read_json(path)
        session.status = str(data.get("status") or session.status or "normal")
        session.token_usage = dict(data.get("token_usage") or session.token_usage)
        session.metadata.update(dict(data.get("metadata") or {}))

    def _already_compacted_decision(self, session: AgentSession) -> ContextCompressionDecision:
        state = load_model_context(self.session_dir)
        if not state:
            return ContextCompressionDecision(status="failed", reason="compacted session is missing model_context.json")
        messages = [
            {"role": str(message.get("role")), "content": str(message.get("content", ""))}
            for message in state.get("messages", [])
            if isinstance(message, dict)
        ]
        return ContextCompressionDecision(
            status="compacted",
            messages=messages,
            reason="already compacted",
            compaction_id=str(state.get("compaction_id") or ""),
        )

    def _should_compact(self, session: AgentSession) -> bool:
        if not self.config.context_compression_enabled:
            return False
        if self.config.context_window is None or self.config.context_window <= 0:
            return False
        usage = _latest_token_usage(session)
        context_tokens = usage.get("context_tokens", 0)
        return context_tokens >= self.config.context_window * self.config.context_compression_trigger_ratio

    def _write_transcript(
        self,
        *,
        session: AgentSession,
        system_prompt: str,
        model_messages: list[dict[str, Any]],
    ) -> Path:
        path = self.session_dir / "transcript.jsonl"
        now = utc_now()
        append_jsonl(
            path,
            {
                "type": "message",
                "message_id": f"{session.session_id}:system",
                "role": "system",
                "content": system_prompt,
                "created_at": now,
            },
        )
        for index, message in enumerate(model_messages, start=1):
            append_jsonl(
                path,
                {
                    "type": "message",
                    "message_id": str(message.get("id") or f"{session.session_id}:msg_{index}"),
                    "role": str(message.get("role") or ""),
                    "content": str(message.get("content") or ""),
                    "created_at": now,
                },
            )
        return path


def _collect_file_event_reads(session_dir: Path) -> list[FileReadRecord]:
    records: list[FileReadRecord] = []
    for row in EventStore.iter_jsonl(session_dir / "logs" / "file_events.jsonl") or []:
        if row.get("type") != "file.read":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        if not isinstance(payload, dict):
            continue
        content = payload.get("returned_content")
        if not isinstance(content, str):
            continue
        path = payload.get("path") or row.get("path")
        if not isinstance(path, str) or not path:
            continue
        mode, range_text = _mode_and_range(payload.get("returned_range") or payload.get("range"))
        records.append(
            FileReadRecord(
                path=path,
                mode=mode,
                range=range_text,
                content=content,
                created_at=str(row.get("created_at") or ""),
            )
        )
    return records


def _collect_tool_result_reads(session_dir: Path) -> list[FileReadRecord]:
    calls: dict[str, dict[str, Any]] = {}
    for row in EventStore.iter_jsonl(session_dir / "logs" / "tool_calls.jsonl") or []:
        if row.get("tool_name") == "read" and row.get("event_id"):
            calls[str(row["event_id"])] = row
    records: list[FileReadRecord] = []
    for row in EventStore.iter_jsonl(session_dir / "logs" / "tool_results.jsonl") or []:
        call_id = row.get("tool_call_id")
        call = calls.get(str(call_id))
        if call is None:
            continue
        result = row.get("result")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        path = _first_string(args, ("path", "file_path", "filename")) or _result_path(result)
        content = _result_content(result)
        if path is None or content is None:
            continue
        mode, range_text = _mode_and_range(result.get("range") if isinstance(result, dict) else None)
        records.append(
            FileReadRecord(
                path=path,
                mode=mode,
                range=range_text,
                content=content,
                created_at=str(row.get("created_at") or ""),
            )
        )
    return records


def _mode_and_range(value: Any) -> tuple[Literal["full", "partial"], str | None]:
    if not isinstance(value, dict) or value.get("unit") == "full":
        return "full", None
    start = value.get("start", 1)
    end = value.get("end", "EOF")
    return "partial", f"{start}-{end}"


def _result_path(result: Any) -> str | None:
    if isinstance(result, dict):
        return _first_string(result, ("path", "file_path", "filename"))
    return None


def _result_content(result: Any) -> str | None:
    if isinstance(result, dict):
        content = result.get("content")
        return content if isinstance(content, str) else None
    if isinstance(result, str):
        return result
    return None


def _first_string(mapping: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = mapping.get(name)
        if isinstance(value, str) and value:
            return value
    return None


async def _session_lock(session_id: str) -> asyncio.Lock:
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[session_id] = lock
        return lock


async def _safe_call_model(
    call_model: Callable[..., Awaitable[ContextCompressionModelResult]],
    messages: list[dict[str, Any]],
    *,
    system_prompt: str,
    tools_enabled: bool,
) -> ContextCompressionModelResult | None:
    try:
        return await call_model(messages, system_prompt=system_prompt, tools_enabled=tools_enabled)
    except Exception:  # noqa: BLE001 - compression attempts are retried by design.
        return None


def _latest_token_usage(session: AgentSession) -> dict[str, int]:
    raw = dict(session.token_usage or session.metadata.get("token_usage") or {})
    if not raw:
        for message in reversed(session.history):
            usage = message.get("usage")
            if isinstance(usage, dict):
                raw = dict(usage)
                break
    input_tokens = _int_value(raw, "latest_input_tokens", "input_tokens", "prompt_tokens")
    output_tokens = _int_value(raw, "latest_output_tokens", "output_tokens", "completion_tokens")
    context_tokens = _int_value(raw, "latest_context_tokens", "context_tokens", "total_tokens")
    if context_tokens == 0 and (input_tokens or output_tokens):
        context_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "context_tokens": context_tokens,
    }


def _int_value(mapping: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


def _extract_system_reminder(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        content = str(message.get("content") or "")
        end = content.rfind("</system-reminder>")
        if end < 0:
            continue
        start = content.rfind("<system-reminder>", 0, end)
        if start < 0:
            continue
        return content[start : end + len("</system-reminder>")]
    return "<system-reminder>\n</system-reminder>"


def _summary_with_transcript(summary: str, transcript_path: Path) -> str:
    if TRANSCRIPT_POINTER in summary:
        return summary
    return f"{summary.rstrip()}\n{TRANSCRIPT_POINTER} {transcript_path}"


def _render_continuation_user_message(
    *,
    system_reminder: str,
    file_read_xml: str,
    summary: str,
) -> str:
    return "\n".join(
        [
            system_reminder,
            "<session-context>",
            "This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.",
            "",
            file_read_xml,
            "",
            "<summary>",
            summary,
            "</summary>",
            "</session-context>",
        ]
    )


def _persist_session(session: AgentSession) -> None:
    session.updated_at = utc_now()
    data = redact_secrets(session.to_dict())
    session_dir = Path(session.session_dir)
    write_json(session_dir / "session.json", data)
