import json
from pathlib import Path

import pytest

from lora.runtime.eternal_conversation import (
    DynamicMemoryCli,
    EXTRACTOR_SYSTEM_PROMPT,
    EternalConversationHarness,
    _bound_extractor_payload,
    _validate_extractor_payload,
    load_projection,
    render_memory_context,
)
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


def test_extractor_payload_enforces_compact_snapshot_and_ut_limits() -> None:
    snapshot = {
        "resident_memory": ["x"] * 5,
        "recent_context": [],
        "current_state": [],
        "completed": [],
        "next_actions": [],
        "constraints": [],
    }
    with pytest.raises(ValueError, match="compact output limits"):
        _validate_extractor_payload({"snapshot": snapshot, "changed_uts": []})

    snapshot["resident_memory"] = []
    with pytest.raises(ValueError, match="at most 4 items"):
        _validate_extractor_payload({"snapshot": snapshot, "changed_uts": [{}] * 5})


def test_extractor_payload_is_not_silently_truncated_before_validation() -> None:
    payload = {
        "snapshot": {
            "resident_memory": ["x" * 400] * 8,
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
    with pytest.raises(ValueError, match="compact output limits"):
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

    async def call_agent(alias: str, system: str, request: str) -> str:
        calls.append((alias, system, request))
        if "memory extraction Agent" in system:
            return json.dumps({
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
        return json.dumps({"approved": True, "diagnostics": []})

    harness = EternalConversationHarness(
        EternalConversationConfig(
            enabled=True,
            extractor_agent_alias="extractor",
            builder_agent_alias="builder",
            dynamic_memory_cli_path=str(SCRIPT),
        ),
        call_agent=call_agent,
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

    async def call_agent(alias: str, system: str, request: str) -> str:
        nonlocal invalid_once
        payload = json.loads(request.split("\n\nYour previous response", 1)[0])
        calls.append((alias, payload))
        if "memory extraction Agent" in system:
            if invalid_once:
                invalid_once = False
                return "not-json"
            return json.dumps({
                "snapshot": {
                    "resident_memory": [], "recent_context": [], "current_state": [],
                    "completed": [], "next_actions": [], "constraints": [],
                },
                "changed_uts": [],
                "semantic_statement": "The new range is represented by the snapshot.",
            })
        return json.dumps({"approved": True, "diagnostics": []})

    harness = EternalConversationHarness(
        EternalConversationConfig(enabled=True, dynamic_memory_cli_path=str(SCRIPT)),
        call_agent=call_agent,
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
    assert len(extractor_payloads) == 3  # first invalid attempt, retry, then the next frozen range
    assert [item["content"] for item in extractor_payloads[-1]["frozen_working_memory"]] == ["second", "two"]
    assert load_projection(ref.session_dir)["covered_through"] == 4
    extractor_history = (Path(ref.session_dir) / "agent-history" / "extractor" / "conversation.jsonl").read_text(encoding="utf-8")
    assert '"status": "rejected"' in extractor_history
    assert '"status": "accepted"' in extractor_history
    foreground_history = (Path(ref.session_dir) / "agent-history" / "foreground" / "conversation.jsonl").read_text(encoding="utf-8")
    assert "first" in foreground_history and "second" in foreground_history
    raw_history = (Path(ref.session_dir) / "raw-history" / "events.jsonl").read_text(encoding="utf-8")
    assert "model-visible-envelope" in raw_history and "system_prompt" in raw_history
