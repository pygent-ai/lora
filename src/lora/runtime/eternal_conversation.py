from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from lora.core.io import append_jsonl, read_json, utc_now, write_json
from lora.schema import AgentSession, EternalConversationConfig


AgentCaller = Callable[[str, str, str], Awaitable[str]]

EXTRACTOR_SYSTEM_PROMPT = """You are the memory extraction Agent in an eternal-conversation Harness.
Return one JSON object only. Use the old Snapshot plus the frozen Working Memory as the continuous
history input. Compare proposed memories with the published UTs and resolve conflicts by updating
stable UT IDs. Preserve exact answer-bearing facts, decisions, constraints, commitments, active work,
and likely future retrieval phrasings. Version support windows, deprecation deadlines, compatibility
promises, and constraints the user asks not to publish in project documentation are still durable
memory and MUST be retained with their exact version/date boundary. A durable user decision or
constraint MUST have its own stable UT and MUST NOT share a UT with an evolving implementation
summary; this separation prevents later code updates from overwriting it. Treat user-defined proper
nouns, internal codenames, aliases, environment names, and their referents as durable retrieval keys
whenever they may affect future behavior. Preserve the exact user-authored name in the UT content,
at least one query, and must_include; never leave it only in the Snapshot. A request to keep a name
out of source code, repository files, or project documentation means memory-only visibility, not
permission to omit it. Before returning, self-audit every future-relevant named entity and alias in
the continuous history and ensure an existing or changed stable UT carries both the exact name and
its meaning. When updating an existing UT, carry forward every still-effective decision,
constraint, commitment, and exact boundary from the published UT; absence from the frozen Working
Memory is not evidence that an older constraint became stale. Do not decide cursor or
publication legality.

Output: {"snapshot": {"resident_memory":[],"recent_context":[],"current_state":[],
"completed":[],"next_actions":[],"constraints":[]}, "changed_uts": [UT changes],
"semantic_statement":"..."}. Each upsert UT needs action,id,memory_id,priority,content,queries,
must_include,evidence_refs,source,tags. priority MUST be an integer from 0 through 100. Every
must_include item MUST be an exact substring of content. Use action=retire with id only when evidence makes a UT stale.
An empty changed_uts list is valid. The Snapshot must carry everything the foreground must know
without retrieval and must treat later Working Memory as newer than the Snapshot. Keep the result
compact: at most 4 changed UTs; merge updates into stable component-level UTs; each UT content at
most 700 characters, at most 4 queries, and at most 3 must_include phrases. Snapshot limits are:
resident_memory 4 items, recent_context 4, current_state 6, completed 4, next_actions 4, constraints
6; each item at most 280 characters. Prefer exact dense facts over narration. Never copy old
Snapshot items unchanged when a shorter merged item preserves them."""

BUILDER_SYSTEM_PROMPT = """You are the memory build Agent. Review the frozen Pending UT batch for
internal consistency and build readiness without changing memory semantics. Return JSON only:
{"approved":true,"diagnostics":[]} or {"approved":false,"diagnostics":["..."]}.
The Harness performs deterministic construction, Built-only tests, exact-content comparison, and
atomic migration after your approval."""


class DynamicMemoryCli:
    def __init__(self, script: Path, root: Path) -> None:
        self.script = script
        self.root = root

    async def call(self, *args: str) -> dict[str, Any]:
        env = dict(os.environ)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self.script),
            *args,
            cwd=str(self.root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        text = stdout.decode("utf-8", errors="replace")
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"dynamic memory CLI returned invalid JSON: {text or stderr.decode(errors='replace')}") from exc
        if process.returncode != 0:
            raise RuntimeError(f"dynamic memory CLI failed: {result}")
        return result

    async def init(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        return await self.call("init", "--path", str(self.root))

    async def file_command(self, command: str, payload: dict[str, Any], name: str) -> dict[str, Any]:
        path = self.root / "jobs" / name
        write_json(path, payload)
        return await self.call(command, "--file", str(path))


def load_projection(session_dir: str | Path) -> dict[str, Any]:
    return read_json(Path(session_dir) / "state" / "eternal-conversation.json", default={})


def render_memory_context(session_dir: str | Path, projection: dict[str, Any]) -> str:
    raw = Path(session_dir) / "raw-history" / "events.jsonl"
    snapshot = projection.get("snapshot") or {}
    command = str(projection.get("memory_cli_command") or "dynamic-memory-cli")
    revision = int(projection.get("snapshot_revision") or 0)
    covered = int(projection.get("covered_through") or 0)
    return "\n".join(
        (
            "<memory-access-instruction>",
            "Use the mounted dynamic-memory-cli search interface when the Snapshot is insufficient or the task depends on prior decisions, constraints, commitments, preferences, or detailed history.",
            "Published memory records prior decisions, not immutable authority.",
            "Conflict protocol (apply before acting or changing files): an override is acknowledged only when the current user message refers to the earlier constraint or decision and communicates an intent to replace it. A message that states only the new, contradictory behavior is always unacknowledged; never infer acknowledgment merely because the requested behavior is clearly opposite.",
            "For an unacknowledged conflict, pause before acting. Stop further exploration immediately. Do not accept the new request, reject it, or choose a workaround on the user's behalf. In at most 120 words, state the conflict briefly, then end the response with exactly one direct clarification question that names the prior option and the new option; wait for the user's answer.",
            f"Search command: {command} search <keyword-or-key-phrase>",
            f"Complete observable Raw History: {raw}",
            "Use read/grep tools directly on Raw History for evidence. Before changing existing behavior, search both dynamic memory and Raw History for prior user constraints, commitments, preferences, and environment assumptions. For compatibility removal, alias removal, deprecation, or version migration, explicitly search Raw History for the affected feature name together with support-window, version, release, compatibility, and deprecation terms before editing. Preserve the earliest relevant direct-user matches (for example, use a bounded first-match search); a tail-only view can hide the original constraint and is not sufficient evidence. If memory search returns only implementation facts rather than direct user intent, Raw History is the required fallback, not an optional extra. Never invent missing history.",
            "</memory-access-instruction>",
            f'<memory-snapshot revision="{revision}" covered-through="{covered}">',
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            "</memory-snapshot>",
        )
    )


class EternalConversationHarness:
    def __init__(self, config: EternalConversationConfig, *, call_agent: AgentCaller) -> None:
        self.config = config
        self.call_agent = call_agent
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._builders: dict[str, asyncio.Task[None]] = {}
        self._closed = False
        self._accepting = True

    def _script(self, session_dir: Path) -> Path:
        if self.config.dynamic_memory_cli_path:
            path = Path(self.config.dynamic_memory_cli_path)
            if path.is_file():
                return path
        for root in (session_dir.parents[1] / "skills", Path.home() / ".lora" / "skills"):
            path = root / "dynamic-memory-cli" / "scripts" / "dynamic_memory_cli.py"
            if path.is_file():
                return path
        env = os.environ.get("LORA_DYNAMIC_MEMORY_CLI")
        if env and Path(env).is_file():
            return Path(env).resolve()
        raise FileNotFoundError("dynamic-memory-cli script is not configured or installed")

    async def record_and_trigger(
        self, session: AgentSession, *, model_envelope: dict[str, Any] | None = None
    ) -> None:
        if not self.config.enabled or self._closed or not self._accepting:
            return
        session_dir = Path(session.session_dir)
        state_path = session_dir / "state" / "eternal-harness.json"
        state = read_json(state_path, default={})
        recorded = int(state.get("recorded_messages") or 0)
        raw_path = session_dir / "raw-history" / "events.jsonl"
        if model_envelope is not None:
            append_jsonl(raw_path, {
                "cursor": f"envelope-{len(session.history)}",
                "type": "model-visible-envelope",
                "session_id": session.session_id,
                "created_at": utc_now(),
                "payload": model_envelope,
            })
        for index, message in enumerate(session.history[recorded:], start=recorded + 1):
            event = {
                "cursor": index,
                "type": "message",
                "session_id": session.session_id,
                "created_at": utc_now(),
                "payload": message,
            }
            append_jsonl(raw_path, event)
            append_jsonl(
                session_dir / "agent-history" / "foreground" / "conversation.jsonl",
                event,
            )
        state.update({"recorded_messages": len(session.history), "requested_cursor": len(session.history)})
        write_json(state_path, state)
        task = self._workers.get(session.session_id)
        if task is None or task.done():
            self._workers[session.session_id] = asyncio.create_task(
                self._guarded_extract_loop(session.session_id, session_dir),
                name=f"eternal-extractor:{session.session_id}",
            )

    async def _guarded_extract_loop(self, session_id: str, session_dir: Path) -> None:
        try:
            await self._extract_loop(session_id, session_dir)
        except Exception as exc:
            append_jsonl(session_dir / "agent-history" / "extractor" / "errors.jsonl", {
                "created_at": utc_now(), "error_type": type(exc).__name__, "error": str(exc)
            })
            raise

    async def _extract_loop(self, session_id: str, session_dir: Path) -> None:
        cli = DynamicMemoryCli(self._script(session_dir), session_dir / "memory")
        await cli.init()
        while not self._closed:
            harness_state = read_json(session_dir / "state" / "eternal-harness.json", default={})
            formal = await cli.call("get-state")
            target = int(harness_state.get("requested_cursor") or 0)
            covered = int(formal.get("covered_through") or 0)
            if target <= covered:
                return
            session = read_json(session_dir / "session.json")
            frozen = list(session.get("history") or [])[covered:target]
            published = await cli.call("list", "--full")
            published_memories = list(published.get("memories") or [])
            query = _memory_query(frozen)
            related = await cli.call("search", query) if query and published_memories else {"matches": []}
            related_ids = [str(item.get("id")) for item in related.get("matches") or []][:32]
            by_id = {str(item.get("id")): item for item in published_memories}
            high_priority = sorted(
                published_memories,
                key=lambda item: int(item.get("priority") or 0),
                reverse=True,
            )[:12]
            conflict_reference = []
            seen: set[str] = set()
            for item in [*(by_id[ut_id] for ut_id in related_ids if ut_id in by_id), *high_priority]:
                ut_id = str(item.get("id"))
                if ut_id not in seen:
                    seen.add(ut_id)
                    conflict_reference.append(item)
            evidence_ref = f"raw-history:range-{covered + 1}-{target}"
            request = {
                "old_snapshot": formal.get("snapshot") or {},
                "frozen_working_memory": frozen,
                "published_uts": conflict_reference,
                "source": session_id,
                "evidence_ref": evidence_ref,
            }
            parsed = await self._call_json_agent(
                session_dir=session_dir,
                role="extractor",
                alias=self.config.extractor_agent_alias or "default",
                system_prompt=EXTRACTOR_SYSTEM_PROMPT,
                request=request,
                normalize=_bound_extractor_payload,
                validate=_validate_extractor_payload,
            )
            proposal = {
                "base_memory_revision": formal["memory_revision"],
                "base_snapshot_revision": formal["snapshot_revision"],
                "from_cursor": covered + 1,
                "to_cursor": target,
                "snapshot": parsed["snapshot"],
                "changed_uts": _normalize_changes(parsed.get("changed_uts") or [], session_id, evidence_ref),
                "evidence_refs": [evidence_ref],
                "semantic_statement": parsed.get("semantic_statement") or "All future-relevant effects are carried.",
            }
            result = await cli.file_command("publish-pending", proposal, f"proposal-{covered + 1}-{target}.json")
            result["memory_cli_command"] = (
                f'"{sys.executable}" "{cli.script}" --root "{cli.root}"'
            )
            write_json(session_dir / "state" / "eternal-conversation.json", result)
            self._schedule_builder(session_id, session_dir, cli)

    def _schedule_builder(self, session_id: str, session_dir: Path, cli: DynamicMemoryCli) -> None:
        task = self._builders.get(session_id)
        if task is None or task.done():
            self._builders[session_id] = asyncio.create_task(
                self._guarded_build_loop(session_id, session_dir, cli),
                name=f"eternal-builder:{session_id}",
            )

    async def _guarded_build_loop(self, session_id: str, session_dir: Path, cli: DynamicMemoryCli) -> None:
        try:
            await self._build_loop(session_id, session_dir, cli)
        except Exception as exc:
            append_jsonl(session_dir / "agent-history" / "builder" / "errors.jsonl", {
                "created_at": utc_now(), "error_type": type(exc).__name__, "error": str(exc)
            })
            raise

    async def _build_loop(self, session_id: str, session_dir: Path, cli: DynamicMemoryCli) -> None:
        while not self._closed:
            batch_path = session_dir / "memory" / "jobs" / f"build-{utc_now().replace(':', '-')}.json"
            frozen = await cli.call("freeze-pending", "--output", str(batch_path))
            if int(frozen.get("count") or 0) == 0:
                return
            batch = read_json(batch_path)
            review = await self._call_json_agent(
                session_dir=session_dir,
                role="builder",
                alias=self.config.builder_agent_alias or "default",
                system_prompt=BUILDER_SYSTEM_PROMPT,
                request=batch,
            )
            if not review.get("approved"):
                return
            await cli.call("build-pending", "--file", str(batch_path))

    async def _call_json_agent(
        self,
        *,
        session_dir: Path,
        role: str,
        alias: str,
        system_prompt: str,
        request: dict[str, Any],
        normalize: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        user_prompt = json.dumps(request, ensure_ascii=False)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            response: str | None = None
            try:
                response = await self.call_agent(alias, system_prompt, user_prompt)
                parsed = _json_object(response)
                if normalize is not None:
                    parsed = normalize(parsed)
                if validate is not None:
                    validate(parsed)
                self._append_agent_history(
                    session_dir,
                    role,
                    {
                        "attempt": attempt,
                        "agent_alias": alias,
                        "system_prompt": system_prompt,
                        "request": request,
                        "response": response,
                        "status": "accepted",
                    },
                )
                return parsed
            except Exception as exc:
                last_error = exc
                self._append_agent_history(
                    session_dir,
                    role,
                    {
                        "attempt": attempt,
                        "agent_alias": alias,
                        "system_prompt": system_prompt,
                        "request": request,
                        "response": response,
                        "status": "rejected",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                user_prompt = (
                    json.dumps(request, ensure_ascii=False)
                    + "\n\nYour previous response was invalid: "
                    + str(exc)
                    + "\nReturn exactly one valid JSON object matching the system contract."
                )
        assert last_error is not None
        raise last_error

    @staticmethod
    def _append_agent_history(session_dir: Path, role: str, value: dict[str, Any]) -> None:
        append_jsonl(session_dir / "agent-history" / role / "conversation.jsonl", {
            "created_at": utc_now(), **value
        })

    async def close(self) -> None:
        self._accepting = False
        await self.wait_idle()
        self._closed = True

    async def wait_idle(self) -> None:
        while True:
            active = [
                task for task in (*self._workers.values(), *self._builders.values())
                if not task.done()
            ]
            if active:
                await asyncio.gather(*active, return_exceptions=True)
                continue
            failures = [
                task.exception()
                for task in (*self._workers.values(), *self._builders.values())
                if not task.cancelled() and task.done() and task.exception() is not None
            ]
            if failures:
                raise RuntimeError(
                    "eternal-conversation background work failed: "
                    + "; ".join(f"{type(exc).__name__}: {exc}" for exc in failures)
                ) from failures[0]
            return


def _json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("background Agent did not return JSON")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("background Agent JSON must be an object")
    return value


def _normalize_changes(changes: list[dict[str, Any]], session_id: str, evidence_ref: str) -> list[dict[str, Any]]:
    normalized = []
    for item in changes:
        value = dict(item)
        if value.get("action", "upsert") == "upsert":
            priority = value.get("priority", 50)
            if isinstance(priority, str):
                priority = {"low": 25, "medium": 50, "high": 80, "critical": 100}.get(
                    priority.strip().lower(), 50
                )
            value["priority"] = max(0, min(100, priority if isinstance(priority, int) else 50))
            value["source"] = session_id
            value["evidence_refs"] = [evidence_ref]
            value.setdefault("tags", [])
            value.setdefault("memory_id", f"memory-{value.get('id')}")
            content = str(value.get("content") or "").strip()
            exact_assertions = [
                str(phrase).strip()
                for phrase in value.get("must_include") or []
                if str(phrase).strip().casefold() in content.casefold()
            ]
            # This fallback is a structural assertion copied verbatim from the
            # Agent-authored memory. It adds no memory semantics of its own.
            value["must_include"] = exact_assertions or [content[:160]]
        normalized.append(value)
    return normalized


def _memory_query(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in reversed(messages):
        if message.get("role") not in {"user", "assistant"}:
            continue
        data = message.get("data")
        raw = data.get("raw_content") if isinstance(data, dict) else None
        content = str(raw or message.get("content") or "").strip()
        if content:
            parts.append(content[:800])
        if len(parts) >= 4:
            break
    return " ".join(reversed(parts))[:2400] or "recent conversation"


def _bound_extractor_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Copy an Agent proposal before legality validation without changing semantics."""
    # Silent truncation can remove the exact fact at the tail of a UT while the
    # proposal still appears valid. Oversized proposals must instead fail
    # validation and make the extractor retry with a deliberately compact form.
    bounded = json.loads(json.dumps(value, ensure_ascii=False))
    return bounded


def _validate_extractor_payload(value: dict[str, Any]) -> None:
    snapshot = value.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("extractor snapshot must be an object")
    field_limits = {
        "resident_memory": 4,
        "recent_context": 4,
        "current_state": 6,
        "completed": 4,
        "next_actions": 4,
        "constraints": 6,
    }
    for field, limit in field_limits.items():
        items = snapshot.get(field)
        if not isinstance(items, list):
            raise ValueError(f"snapshot.{field} must be a list")
        if not all(isinstance(item, str) for item in items):
            raise ValueError(f"snapshot.{field} items must be strings")
        if len(items) > limit or any(len(item) > 280 for item in items):
            raise ValueError(f"snapshot.{field} exceeds compact output limits")
    changes = value.get("changed_uts")
    if not isinstance(changes, list) or len(changes) > 4:
        raise ValueError("changed_uts must be a list with at most 4 items")
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("each UT change must be an object")
        if change.get("action") == "retire":
            continue
        content = change.get("content")
        queries = change.get("queries")
        must_include = change.get("must_include")
        if not isinstance(content, str) or len(content) > 700:
            raise ValueError("UT content must be a string of at most 700 characters")
        if not isinstance(queries, list) or len(queries) > 4:
            raise ValueError("UT queries must be a list with at most 4 items")
        if not isinstance(must_include, list) or len(must_include) > 3:
            raise ValueError("UT must_include must be a list with at most 3 items")


__all__ = ["EternalConversationHarness", "load_projection", "render_memory_context"]
