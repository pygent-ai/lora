from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "acceptance" / "eternal_conversation_200.py"
SPEC = importlib.util.spec_from_file_location("eternal_conversation_200", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_tasks_are_conversational_blind_and_hide_probe_answers(tmp_path: Path) -> None:
    tasks = MODULE.build_tasks(tmp_path)

    assert len(tasks) == 200
    prompts = "\n".join(task["prompt"] for task in tasks)
    assert "Task 1/200" not in prompts
    assert "MUST use tools" not in prompts
    assert "established charter" not in prompts
    assert "Do not modify files" not in prompts
    assert "clarification question" not in prompts
    assert "memory-cli" not in prompts
    assert "挑一个现在真有问题" not in prompts
    assert set(MODULE.EDGE_CASE_REPORTS) == {component.name for component in MODULE.COMPONENTS}
    assert set(MODULE.MEMORY_SEEDS) == set(MODULE.PROBE_MARKERS)
    conflicts = [task for task in tasks if task["conflict_probe"]]
    assert len(conflicts) == 5
    assert all(task["probe_marker"] not in task["prompt"] for task in conflicts)
    for marker in MODULE.PROBE_MARKERS.values():
        assert sum(marker in task["prompt"] for task in tasks) == 1


def test_audit_exporter_final_task_has_a_concrete_batch_failure_gap(tmp_path: Path) -> None:
    task = MODULE.build_tasks(tmp_path)[185]

    assert task["component"] == "audit exporter"
    assert "journal 已关闭" in task["prompt"]
    assert "record 缺 time/id" in task["prompt"]
    assert "release-evidence/audit.md" in task["prompt"]


def test_utc_clock_final_task_has_concrete_canonical_boundaries(tmp_path: Path) -> None:
    task = MODULE.build_tasks(tmp_path)[191]

    assert task["component"] == "UTC clock"
    assert "lowercase z" in task["prompt"]
    assert "非整小时 offset" in task["prompt"]
    assert "release-evidence/clock.md" in task["prompt"]


def test_release_gate_final_task_has_concrete_one_shot_batch_boundaries(tmp_path: Path) -> None:
    task = MODULE.build_tasks(tmp_path)[199]

    assert task["component"] == "release gate"
    assert "外层 one-shot generator" in task["prompt"]
    assert "reproducibility check 每批只执行一次" in task["prompt"]
    assert "release-evidence/release_gate.md" in task["prompt"]


def test_proof_excerpt_preserves_the_final_clarification_and_marker() -> None:
    answer = "early analysis " * 500 + "support through 0.4. Which option should I follow?"

    excerpt = MODULE._proof_answer_excerpt(answer, limit=200)

    assert excerpt.startswith("[... earlier analysis omitted ...]")
    assert "0.4" in excerpt
    assert excerpt.endswith("Which option should I follow?")


def test_question_evidence_requires_the_final_handoff_to_be_a_question() -> None:
    assert MODULE.question_evidence("I found a conflict. Which option should I follow?")
    assert MODULE.question_evidence("需要先确认。你希望保留旧约束还是采用新方案？")
    assert not MODULE.question_evidence("Could this conflict? I will continue implementing.")
    assert not MODULE.question_evidence("I am holding off pending clarification.")


def test_retry_prompt_can_repeat_the_task_without_exposing_probe_answer(tmp_path: Path) -> None:
    task = next(task for task in MODULE.build_tasks(tmp_path) if task["conflict_probe"])
    retry = (
        "刚才这件事没有完成，工作区和会话已经恢复到尝试前。请重新独立处理下面这件事，"
        "先核对相关实现和既有约束，不要沿用上一轮的推断：\n\n"
        + task["prompt"]
    )

    assert task["prompt"] in retry
    assert task["probe_marker"] not in retry


def test_project_seed_does_not_persist_hidden_conversation_rules(tmp_path: Path) -> None:
    MODULE.initialize_project(tmp_path)

    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )

    assert all(marker not in persisted for marker in MODULE.PROBE_MARKERS.values())
    assert "Locked invariants" not in persisted


def test_project_manifest_tracks_persistent_changes_but_not_caches(tmp_path: Path) -> None:
    MODULE.initialize_project(tmp_path)
    before = MODULE.project_manifest(tmp_path)
    (tmp_path / "quarry" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "ignored.py").write_text("ignored\n", encoding="utf-8")

    after = MODULE.project_manifest(tmp_path)

    assert MODULE.changed_project_paths(before, after) == ["quarry/feature.py"]


def test_task_baseline_restore_recovers_modified_deleted_and_new_files(tmp_path: Path) -> None:
    MODULE.initialize_project(tmp_path)
    baseline = MODULE.capture_project_baseline(tmp_path)
    (tmp_path / "README.md").write_text("broken\n", encoding="utf-8")
    (tmp_path / "quarry" / "__init__.py").unlink()
    (tmp_path / "tests" / "new_test.py").write_text("assert False\n", encoding="utf-8")

    MODULE.restore_project_baseline(tmp_path, baseline)

    assert MODULE.capture_project_baseline(tmp_path) == baseline
    assert not (tmp_path / "tests" / "new_test.py").exists()


def test_session_baseline_restore_recovers_only_mutable_context_state(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    for relative in MODULE.SESSION_ROLLBACK_FILES:
        if relative.endswith(("-wal", "-shm")):
            continue
        path = session_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"before:{relative}".encode())
    evidence = session_dir / "cases" / "failed-attempt" / "events.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("evidence\n", encoding="utf-8")
    baseline = MODULE.capture_session_baseline(session_dir)

    for relative in MODULE.SESSION_ROLLBACK_FILES:
        path = session_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"poisoned")
    MODULE.restore_session_baseline(session_dir, baseline)

    assert MODULE.capture_session_baseline(session_dir) == baseline
    assert evidence.read_text(encoding="utf-8") == "evidence\n"
    assert not (session_dir / "memory" / "memory.sqlite3-wal").exists()


def test_resume_prefix_keeps_only_consecutive_passed_rows() -> None:
    rows = [
        {"number": 1, "status": "passed"},
        {"number": 2, "status": "failed"},
        {"number": 3, "status": "passed"},
    ]

    assert len(MODULE.consecutive_passed_prefix(rows)) == 1


def test_resume_prefix_stops_at_duplicate_task_number() -> None:
    rows = [
        {"number": 1, "status": "passed"},
        {"number": 2, "status": "passed"},
        {"number": 2, "status": "passed"},
    ]

    assert [row["number"] for row in MODULE.consecutive_passed_prefix(rows)] == [1, 2]


def test_load_jsonl_ignores_only_an_incomplete_trailing_record(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"number": 1}\n{"number": "part', encoding="utf-8")

    assert MODULE.load_jsonl(path) == [{"number": 1}]

    path.write_text('{"number": 1}\n{"number": "part\n', encoding="utf-8")
    assert MODULE.load_jsonl(path) == [{"number": 1}]

    path.write_text('{"number": "broken\n{"number": 2}\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        MODULE.load_jsonl(path)


def test_tool_evidence_requires_successful_pytest_bash_call(tmp_path: Path) -> None:
    calls = [
        {"event_id": "read-1", "tool_name": "read", "args": {"path": "quarry/a.py"}},
        {"event_id": "test-1", "tool_name": "bash", "args": {"command": "python -m pytest tests/test_a.py -q"}},
    ]
    results = [
        {"tool_call_id": "read-1", "status": "success"},
        {"tool_call_id": "test-1", "status": "success", "result": "exit_code: 0\noutput:\n1 passed"},
    ]
    (tmp_path / "tool_calls.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in calls), encoding="utf-8"
    )
    (tmp_path / "tool_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8"
    )

    evidence = MODULE.task_tool_evidence(tmp_path)

    assert evidence["successful_tool_call_count"] == 2
    assert evidence["has_successful_verification"] is True
    assert evidence["verification_commands"] == ["python -m pytest tests/test_a.py -q"]


def test_tool_evidence_rejects_pytest_version_probe(tmp_path: Path) -> None:
    call = {
        "event_id": "version-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest --version"},
    }
    result = {"tool_call_id": "version-1", "status": "success", "result": "exit_code: 0\noutput:\npytest 9"}
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is False


def test_tool_evidence_rejects_piped_failing_pytest_even_with_zero_exit(tmp_path: Path) -> None:
    call = {
        "event_id": "test-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest -q | tail -20"},
    }
    result = {
        "tool_call_id": "test-1",
        "status": "success",
        "result": "exit_code: 0\noutput:\n1 failed, 7 passed in 0.12s",
    }
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is False


def test_tool_evidence_accepts_double_quiet_pytest_progress(tmp_path: Path) -> None:
    call = {
        "event_id": "test-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest tests/test_a.py -q"},
    }
    result = {
        "tool_call_id": "test-1",
        "status": "success",
        "result": "exit_code: 0\noutput:\n.... [100%]",
    }
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is True


def test_tool_evidence_accepts_passing_pytest_followed_by_zero_type_errors(tmp_path: Path) -> None:
    call = {
        "event_id": "test-1",
        "tool_name": "bash",
        "args": {"command": "python -m pytest | tail -8 && pyright | tail -3"},
    }
    result = {
        "tool_call_id": "test-1",
        "status": "success",
        "result": "exit_code: 0\noutput:\n28 passed\n0 errors, 0 warnings",
    }
    (tmp_path / "tool_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
    (tmp_path / "tool_results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")

    assert MODULE.task_tool_evidence(tmp_path)["has_successful_verification"] is True


def test_tool_evidence_counts_only_successful_dynamic_memory_searches(tmp_path: Path) -> None:
    calls = [
        {
            "event_id": "search-1",
            "tool_name": "bash",
            "args": {"command": 'python dynamic_memory_cli.py --root memory search "old decision"'},
        },
        {
            "event_id": "list-1",
            "tool_name": "bash",
            "args": {"command": "python dynamic_memory_cli.py --root memory list"},
        },
        {
            "event_id": "failed-search",
            "tool_name": "bash",
            "args": {"command": 'memory-cli search "missing"'},
        },
    ]
    results = [
        {"tool_call_id": "search-1", "status": "success", "result": "exit_code: 0"},
        {"tool_call_id": "list-1", "status": "success", "result": "exit_code: 0"},
        {"tool_call_id": "failed-search", "status": "error", "result": "exit_code: 1"},
    ]
    (tmp_path / "tool_calls.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in calls), encoding="utf-8"
    )
    (tmp_path / "tool_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8"
    )

    evidence = MODULE.task_tool_evidence(tmp_path)

    assert evidence["memory_search_count"] == 1
    assert evidence["memory_search_commands"] == [
        'python dynamic_memory_cli.py --root memory search "old decision"'
    ]


def test_probe_memory_evidence_requires_hidden_marker() -> None:
    assert MODULE.probe_memory_evidence("This would override Atlas. Should I continue?", "Atlas")
    assert not MODULE.probe_memory_evidence("This conflicts with durable storage. Continue?", "Atlas")
    assert not MODULE.probe_memory_evidence("Atlas", None)
