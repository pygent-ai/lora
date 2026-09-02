import asyncio
import json
from contextvars import ContextVar
from pathlib import Path

import pytest
from pygent import AIMessage, PygentAgent, ToolCall, ToolMessage, UserMessage
from pygent.llm import ModelExecution, ModelProviderResponse

from lora.config import load_run_config
from lora.runtime.eternal_conversation import (
    DynamicMemoryCli,
    EXTRACTOR_SYSTEM_PROMPT,
    EternalConversationHarness,
    MAX_MEMORY_JOB_ATTEMPTS,
    _bound_extractor_payload,
    _compact_working_memory,
    _validate_extractor_payload,
    load_projection,
    render_memory_context,
)
from lora.runtime.service import (
    LoraRuntimeService,
    MAX_IDENTICAL_MEMORY_TOOL_REJECTIONS,
)
from lora.runtime.agent import LoraAgent
from lora.runtime.agent.common import DEFAULT_REACT_MAX_STEPS
from lora.schema import EternalConversationConfig, RunConfig
from lora.sessions import SessionManager


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "memory-cli"
    / "skills"
    / "dynamic-memory-cli"
    / "scripts"
    / "dynamic_memory_cli.py"
)


def test_identical_memory_tool_rejection_guard_is_bounded_below_agent_budget() -> None:
    assert MAX_IDENTICAL_MEMORY_TOOL_REJECTIONS == 8
    assert MAX_IDENTICAL_MEMORY_TOOL_REJECTIONS < DEFAULT_REACT_MAX_STEPS


class _MemoryReActInvoker:
    def __init__(self) -> None:
        self.requests = []
        self.generations = []
        self.failed_results_seen = 0

    def validate_route(self, _route) -> None:
        return None

    def execute(self, *, message, context, generation, **_kwargs) -> ModelExecution:
        self.requests.append((message, context))
        self.generations.append(generation)

        async def invoke(_emit) -> ModelProviderResponse:
            if isinstance(message, UserMessage):
                answer = AIMessage(
                    content="publishing",
                    tool_calls=(ToolCall(
                        call_id="publish-1",
                        name="publish_pending",
                        arguments={"payload": {"value": "x" * 701}},
                    ),),
                )
            elif isinstance(message, ToolMessage):
                if message.results[0].status == "failed":
                    self.failed_results_seen += 1
                    error_text = str(message.results[0].error)
                    assert "at most 700 characters" in error_text
                    assert "split" in error_text
                    assert "rejected the previous call before any write" in message.content
                    assert "NEVER resend identical arguments" in message.content
                    assert "multiple focused UTs" in message.content
                    assert "within 8 UT" in message.content
                    if self.failed_results_seen == 1:
                        assert "IDENTICAL INVALID PAYLOAD" not in message.content
                        assert "IDENTICAL INVALID PAYLOAD" not in error_text
                        answer = AIMessage(
                            content="accidentally repeating",
                            tool_calls=(ToolCall(
                                call_id="publish-repeat",
                                name="publish_pending",
                                arguments={"payload": {"value": "x" * 701}},
                            ),),
                        )
                    else:
                        assert "IDENTICAL INVALID PAYLOAD REJECTION #2" in message.content
                        assert "IDENTICAL INVALID PAYLOAD REJECTION #2" in error_text
                        answer = AIMessage(
                            content="shortening",
                            tool_calls=(ToolCall(
                                call_id="publish-2",
                                name="publish_pending",
                                arguments={"payload": {"value": "compact"}},
                            ),),
                        )
                else:
                    assert message.results[0].status == "succeeded", message.results[0]
                    answer = AIMessage(content="published")
            else:  # pragma: no cover - Pygent ReAct has only these model boundaries here.
                raise AssertionError(type(message).__name__)
            return ModelProviderResponse(message=answer, usage={})

        return ModelExecution(invoke)

    async def aclose(self) -> None:
        return None


def test_working_memory_compacts_tool_protocol_but_preserves_conversation() -> None:
    history = [
        {"role": "user", "content": "keep this exact constraint"},
        {
            "role": "assistant",
            "content": "final answer stays complete",
            "usage": {"input_tokens": 999_999},
            "tool_calls": [{"name": "write", "arguments": {"content": "x" * 5_000}}],
        },
        {
            "role": "tool",
            "results": [{"name": "write", "status": "succeeded", "output": "y" * 5_000}],
        },
    ]

    compacted = _compact_working_memory(history)

    assert compacted[0] == {"role": "user", "content": "keep this exact constraint"}
    assert compacted[1]["content"] == "final answer stays complete"
    assert "usage" not in compacted[1]
    assert "truncated_json" in compacted[1]["tool_calls"][0]["arguments"]
    assert "full evidence remains in Raw History" in compacted[2]["results"][0]["output"]


@pytest.mark.asyncio
async def test_background_memory_runner_uses_native_pygent_react(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_run_config(workspace_root=tmp_path)
    invoker = _MemoryReActInvoker()
    real_agent = LoraAgent
    created_react_agents: list[dict] = []

    def make_pygent_agent(**kwargs):
        created_react_agents.append(kwargs)
        return PygentAgent(**kwargs)

    def make_agent(child_config, *, managed_model):
        agent = real_agent(child_config, managed_model=managed_model)
        agent.llm = invoker
        return agent

    monkeypatch.setattr("lora.runtime.service.LoraAgent", make_agent)
    monkeypatch.setattr("lora.runtime.service.PygentAgent", make_pygent_agent)
    service = object.__new__(LoraRuntimeService)
    service.config = config
    received = []

    from lora.runtime.eternal_conversation import MemoryAgentTool

    async def publish(payload: dict) -> dict:
        if len(payload["value"]) > 700:
            raise ValueError(
                "changed_uts[0] (ut-test) content has 701 characters; split its details "
                "across multiple focused UTs whose content is at most 700 characters each, or, "
                "if companion UTs already hold the split details, remove at least 1 character "
                "from this over-limit UT. NEVER resend the identical payload; "
                "then call publish_pending again"
            )
        received.append(payload)
        return {"status": "published", "build_state": "pending"}

    answer = await service._run_memory_agent(
        config.agent_alias,
        "Call publish_pending exactly once, then finish.",
        {"job": "extract"},
        MemoryAgentTool(
            name="publish_pending",
            description="Publish a Pending memory proposal.",
            handler=publish,
        ),
    )

    assert answer == "published"
    assert received == [{"value": "compact"}]
    assert len(invoker.requests) == 4
    assert all(generation.max_output_tokens is None for generation in invoker.generations)
    assert created_react_agents[0]["max_steps"] == DEFAULT_REACT_MAX_STEPS == 500
    assert created_react_agents[0]["max_model_calls"] == DEFAULT_REACT_MAX_STEPS
    assert created_react_agents[0]["max_tool_calls"] == DEFAULT_REACT_MAX_STEPS
    assert isinstance(invoker.requests[0][0], UserMessage)
    assert isinstance(invoker.requests[1][0], ToolMessage)
    assert isinstance(invoker.requests[2][0], ToolMessage)


@pytest.mark.asyncio
async def test_wait_idle_isolates_failed_background_job_from_foreground() -> None:
    async def fail() -> None:
        raise ValueError("bad extractor output")

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True),
        run_agent=lambda *_args: asyncio.sleep(0, result="done"),
    )
    harness._workers["session-1"] = asyncio.create_task(fail())

    await harness.wait_idle()
    assert harness._workers == {}


@pytest.mark.asyncio
async def test_retry_pending_restarts_only_the_failed_memory_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    manager = SessionManager(config)
    ref = manager.create("retry-memory", mode="chat")
    session = manager.load(ref.session_id)
    session_dir = Path(session.session_dir)
    (session_dir / "state").mkdir(parents=True, exist_ok=True)
    (session_dir / "state" / "eternal-harness.json").write_text(
        json.dumps({"requested_cursor": 5}), encoding="utf-8"
    )
    (session_dir / "state" / "eternal-conversation.json").write_text(
        json.dumps({"covered_through": 2}), encoding="utf-8"
    )
    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True),
        run_agent=lambda *_args: asyncio.sleep(0, result="done"),
    )
    resumed: list[str] = []

    async def resumed_job(session_id: str, _session_dir: Path) -> None:
        resumed.append(session_id)

    monkeypatch.setattr(harness, "_guarded_extract_loop", resumed_job)

    assert await harness.retry_pending(session) is True
    assert await harness.retry_pending(session) is False
    await harness.wait_idle()

    assert resumed == [session.session_id]


@pytest.mark.asyncio
async def test_memory_workers_do_not_inherit_foreground_execution_context(
    tmp_path: Path,
) -> None:
    foreground_scope = ContextVar("test_foreground_scope", default=None)
    observed = []
    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [{"role": "user", "content": "remember this"}]
    manager.save(session)

    async def run_agent(_alias: str, system: str, _request: dict, memory_tool) -> str:
        observed.append(foreground_scope.get())
        if "memory extraction Agent" in system:
            await memory_tool.handler({
                "snapshot": {
                    "resident_memory": [], "recent_context": [], "current_state": [],
                    "completed": [], "next_actions": [], "constraints": [],
                },
                "changed_uts": [{
                    "action": "upsert", "id": "ut-context", "memory_id": "memory-context",
                    "priority": 50, "content": "Remember this context boundary.",
                    "queries": ["context boundary"], "must_include": ["context boundary"],
                    "tags": ["test"],
                }],
                "semantic_statement": "The range is represented.",
            })
        else:
            await memory_tool.handler({"diagnostics": []})
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_agent=run_agent,
    )
    token = foreground_scope.set("active-foreground-execution")
    try:
        await harness.record_and_trigger(manager.load(ref.session_id))
        await harness.wait_idle()
    finally:
        foreground_scope.reset(token)

    assert observed == [None, None]


def test_extractor_payload_enforces_compact_snapshot_and_ut_limits() -> None:
    snapshot = {
        "resident_memory": ["x"] * 5,
        "recent_context": [],
        "current_state": [],
        "completed": [],
        "next_actions": [],
        "constraints": [],
    }
    with pytest.raises(ValueError, match="has 5 items; compact output limit is 4"):
        _validate_extractor_payload({"snapshot": snapshot, "changed_uts": []})

    snapshot["resident_memory"] = []
    with pytest.raises(ValueError, match="at most 8 items"):
        _validate_extractor_payload({"snapshot": snapshot, "changed_uts": [{}] * 9})


def test_extractor_payload_rejects_update_action_with_upsert_guidance() -> None:
    snapshot = {
        "resident_memory": [],
        "recent_context": [],
        "current_state": [],
        "completed": [],
        "next_actions": [],
        "constraints": [],
    }

    with pytest.raises(
        ValueError,
        match=r"unsupported action 'update'.*use action='upsert' to create or update a UT",
    ):
        _validate_extractor_payload({
            "snapshot": snapshot,
            "changed_uts": [{"action": "update", "id": "existing-ut"}],
        })


def test_extractor_payload_is_not_silently_truncated_before_validation() -> None:
    accepted_snapshot = {
        "resident_memory": ["x" * 500],
        "recent_context": [],
        "current_state": [],
        "completed": [],
        "next_actions": [],
        "constraints": [],
    }
    _validate_extractor_payload({"snapshot": accepted_snapshot, "changed_uts": []})

    payload = {
        "snapshot": {
            "resident_memory": ["x" * 501] * 4,
            "recent_context": [],
            "current_state": [],
            "completed": [],
            "next_actions": [],
            "constraints": [],
        },
        "changed_uts": [
            {"action": "upsert", "content": "c" * 900, "queries": ["q"] * 6, "must_include": ["c"] * 5}
            for _ in range(6)
        ],
    }

    bounded = _bound_extractor_payload(payload)

    assert bounded == payload
    with pytest.raises(
        ValueError,
        match=r"snapshot\.resident_memory\[0\] has 501 characters.*split detailed facts",
    ):
        _validate_extractor_payload(bounded)


def test_memory_context_requires_clarification_for_unacknowledged_conflicts(tmp_path: Path) -> None:
    prompt = render_memory_context(tmp_path, {"snapshot": {}, "covered_through": 4})
    assert "an override is acknowledged only when" in prompt
    assert "states only the new, contradictory behavior is always unacknowledged" in prompt
    assert "search both dynamic memory and Raw History" in prompt
    assert "earliest relevant direct-user matches" in prompt
    assert "tail-only view" in prompt
    assert "Raw History is the required fallback" in prompt
    assert "support-window, version, release, compatibility, and deprecation" in prompt
    assert "Do not accept the new request, reject it" in prompt
    assert "Stop further exploration immediately" in prompt
    assert "at most 120 words" in prompt
    assert "end the response with exactly one direct clarification question" in prompt


def test_extractor_retains_private_compatibility_boundaries() -> None:
    assert "Version support windows" in EXTRACTOR_SYSTEM_PROMPT
    assert "exact version/date boundary" in EXTRACTOR_SYSTEM_PROMPT
    assert "own stable UT" in EXTRACTOR_SYSTEM_PROMPT
    assert "MUST NOT share a UT" in EXTRACTOR_SYSTEM_PROMPT
    assert "carry forward every still-effective decision" in EXTRACTOR_SYSTEM_PROMPT
    assert "absence from the frozen Working" in EXTRACTOR_SYSTEM_PROMPT
    assert "Memory is not evidence" in EXTRACTOR_SYSTEM_PROMPT


def test_extractor_retains_named_entities_as_retrievable_memory() -> None:
    assert "user-defined proper" in EXTRACTOR_SYSTEM_PROMPT
    assert "internal codenames" in EXTRACTOR_SYSTEM_PROMPT
    assert "environment names" in EXTRACTOR_SYSTEM_PROMPT
    assert "exact user-authored name" in EXTRACTOR_SYSTEM_PROMPT
    assert "at least one query, and must_include" in EXTRACTOR_SYSTEM_PROMPT
    assert "never leave it only in the Snapshot" in EXTRACTOR_SYSTEM_PROMPT
    assert "memory-only visibility, not" in EXTRACTOR_SYSTEM_PROMPT
    assert "self-audit every future-relevant named entity" in EXTRACTOR_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_harness_records_raw_history_publishes_snapshot_and_builds_memory(tmp_path: Path) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "user", "content": "Use blue deployments for this service."},
        {"role": "assistant", "content": "I will preserve blue deployments as a constraint."},
    ]
    manager.save(session)

    calls = []

    async def run_agent(alias: str, system: str, request: dict, memory_tool) -> str:
        calls.append((alias, system, request))
        if "memory extraction Agent" in system:
            await memory_tool.handler({
                "snapshot": {
                    "resident_memory": ["Use blue deployments"],
                    "recent_context": [], "current_state": [], "completed": [],
                    "next_actions": [], "constraints": ["Use blue deployments"],
                },
                "changed_uts": [{
                    "action": "upsert", "id": "ut-blue", "memory_id": "memory-blue",
                    "priority": 80, "content": "The service must use blue ↔ green deployments.",
                    "queries": ["service deployment", "deployment constraint"],
                    "must_include": ["blue deployments"], "tags": ["constraint"],
                }],
                "semantic_statement": "The deployment constraint is carried.",
            })
        else:
            await memory_tool.handler({"diagnostics": []})
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(
            enabled=True,
            extractor_agent_alias="extractor",
            builder_agent_alias="builder",
            dynamic_memory_cli_path=str(SCRIPT),
        ),
        run_agent=run_agent,
    )
    await harness.record_and_trigger(manager.load(ref.session_id))
    await harness.wait_idle()

    projection = load_projection(ref.session_dir)
    assert projection["covered_through"] == 2
    assert projection["snapshot"]["constraints"] == ["Use blue deployments"]
    assert "--root" in projection["memory_cli_command"]
    raw = (Path(ref.session_dir) / "raw-history" / "events.jsonl").read_text(encoding="utf-8")
    assert "Use blue deployments" in raw
    assert (Path(ref.session_dir) / "agent-history" / "extractor" / "conversation.jsonl").exists()
    assert (Path(ref.session_dir) / "agent-history" / "builder" / "conversation.jsonl").exists()
    assert [item[0] for item in calls] == ["extractor", "builder"]

    import sqlite3
    with sqlite3.connect(Path(ref.session_dir) / "memory" / "memory.sqlite3") as connection:
        assert connection.execute("SELECT build_state FROM uts WHERE id='ut-blue'").fetchone()[0] == "built"
    listed = await DynamicMemoryCli(SCRIPT, Path(ref.session_dir) / "memory").call("list", "--full")
    assert "blue ↔ green" in listed["memories"][0]["content"]


@pytest.mark.asyncio
async def test_harness_retries_invalid_json_and_only_freezes_uncovered_history(tmp_path: Path) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    calls: list[tuple[str, dict]] = []
    invalid_once = True

    async def run_agent(alias: str, system: str, request: dict, memory_tool) -> str:
        nonlocal invalid_once
        calls.append((alias, request))
        if "memory extraction Agent" in system:
            if invalid_once:
                invalid_once = False
                with pytest.raises(
                    ValueError,
                    match="split its details.*at most 700 characters.*remove at least 1 character.*NEVER resend",
                ):
                    await memory_tool.handler({
                        "snapshot": {
                            "resident_memory": [], "recent_context": [], "current_state": [],
                            "completed": [], "next_actions": [], "constraints": [],
                        },
                        "changed_uts": [{
                            "action": "upsert", "id": "too-long",
                            "content": "x" * 701, "queries": [], "must_include": [],
                        }],
                    })
            await memory_tool.handler({
                "snapshot": {
                    "resident_memory": [], "recent_context": [], "current_state": [],
                    "completed": [], "next_actions": [], "constraints": [],
                },
                "changed_uts": [],
                "semantic_statement": "The new range is represented by the snapshot.",
            })
        else:
            await memory_tool.handler({"diagnostics": []})
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_agent=run_agent,
    )
    session = manager.load(ref.session_id)
    session.history = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "one"}]
    manager.save(session)
    await harness.record_and_trigger(session, model_envelope={"system_prompt": "host", "tools": []})
    await harness.wait_idle()

    session = manager.load(ref.session_id)
    session.history.extend([{"role": "user", "content": "second"}, {"role": "assistant", "content": "two"}])
    manager.save(session)
    await harness.record_and_trigger(session)
    await harness.wait_idle()

    extractor_payloads = [payload for alias, payload in calls if alias == "default" and "frozen_working_memory" in payload]
    assert len(extractor_payloads) == 2  # one native ReAct execution per frozen range
    assert [item["content"] for item in extractor_payloads[-1]["frozen_working_memory"]] == ["second", "two"]
    assert load_projection(ref.session_dir)["covered_through"] == 4
    extractor_history = (Path(ref.session_dir) / "agent-history" / "extractor" / "conversation.jsonl").read_text(encoding="utf-8")
    assert '"status": "rejected"' in extractor_history
    assert '"status": "accepted"' in extractor_history
    foreground_history = (Path(ref.session_dir) / "agent-history" / "foreground" / "conversation.jsonl").read_text(encoding="utf-8")
    assert "first" in foreground_history and "second" in foreground_history
    raw_history = (Path(ref.session_dir) / "raw-history" / "events.jsonl").read_text(encoding="utf-8")
    assert "model-visible-envelope" in raw_history and "system_prompt" in raw_history


@pytest.mark.asyncio
async def test_failed_builder_keeps_pending_ut_and_recovers_without_rolling_back_turn(
    tmp_path: Path,
) -> None:
    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    manager = SessionManager(config)
    ref = manager.create("chat", mode="chat")
    session = manager.load(ref.session_id)
    session.history = [
        {"role": "user", "content": "Remember the canary release decision."},
        {"role": "assistant", "content": "The canary decision is recorded."},
    ]
    manager.save(session)
    builder_failures = MAX_MEMORY_JOB_ATTEMPTS

    async def run_agent(alias: str, system: str, request: dict, memory_tool) -> str:
        nonlocal builder_failures
        if "memory extraction Agent" in system:
            await memory_tool.handler({
                "snapshot": {
                    "resident_memory": ["Use canary releases"],
                    "recent_context": [], "current_state": [], "completed": [],
                    "next_actions": [], "constraints": ["Use canary releases"],
                },
                "changed_uts": [{
                    "action": "upsert", "id": "ut-canary", "memory_id": "memory-canary",
                    "priority": 80, "content": "The service must use canary releases.",
                    "queries": ["release strategy"], "must_include": ["canary releases"],
                    "tags": ["decision"],
                }],
                "semantic_statement": "The release decision is carried.",
            })
        elif builder_failures:
            builder_failures -= 1
            raise RuntimeError("simulated background builder outage")
        else:
            await memory_tool.handler({"diagnostics": []})
        return "done"

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        run_agent=run_agent,
    )
    await harness.record_and_trigger(manager.load(ref.session_id))
    await harness.wait_idle()  # Background failure must not fail the foreground turn.

    import sqlite3
    database = Path(ref.session_dir) / "memory" / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        pending = connection.execute(
            "SELECT content, build_state FROM uts WHERE id='ut-canary'"
        ).fetchone()
    assert pending == ("The service must use canary releases.", "pending")
    state = json.loads(
        (Path(ref.session_dir) / "state" / "eternal-harness.json").read_text(encoding="utf-8")
    )
    assert state["memory_jobs"]["builder"]["status"] == "failed"
    assert state["requested_cursor"] == 2
    assert load_projection(ref.session_dir)["covered_through"] == 2

    # A later foreground trigger restarts only the recoverable memory job. The
    # already-persisted foreground history and the Pending UT remain unchanged.
    await harness.record_and_trigger(manager.load(ref.session_id))
    await harness.wait_idle()

    with sqlite3.connect(database) as connection:
        built = connection.execute(
            "SELECT content, build_state FROM uts WHERE id='ut-canary'"
        ).fetchone()
    assert built == ("The service must use canary releases.", "built")
    state = json.loads(
        (Path(ref.session_dir) / "state" / "eternal-harness.json").read_text(encoding="utf-8")
    )
    assert state["memory_jobs"]["builder"]["status"] == "idle"
    assert manager.load(ref.session_id).history == session.history
