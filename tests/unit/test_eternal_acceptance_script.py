from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "acceptance" / "eternal_conversation_200.py"
SPEC = importlib.util.spec_from_file_location("eternal_conversation_200", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_acceptance_foreground_step_budget_defaults_to_500() -> None:
    assert MODULE.DEFAULT_REACT_MAX_STEPS == 500


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
    edge_tasks = [task for task in tasks if task["phase"] == "edge cases"]
    assert all("无需为了产生 diff 重写正确代码" in task["prompt"] for task in edge_tasks)
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
    retry = MODULE.build_retry_message(task["prompt"])

    assert task["prompt"] in retry
    assert task["probe_marker"] not in retry
    assert "完成声明不代表当前工作区状态" in retry
    assert "补充最小兼容实现属于本任务范围" in retry
    assert "不要仅因为符号缺失而反问" in retry
    assert "不要用设计说明代替执行" in retry
    assert "不能以当前实现已经很快或足够好为由无改动结束" in retry
    assert "不要在没有具体剩余检查时继续调用工具" in retry


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
    assert "context/conversation-checkpoints.sqlite3" in MODULE.SESSION_ROLLBACK_FILES


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


def test_failed_background_cleanup_does_not_prevent_transactional_restore() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "A failed background task was already captured" in source
    assert source.index("except Exception:\n                    # A failed background") < source.index(
        "restore_project_baseline(project_root, task_baseline)"
    )


def test_load_jsonl_ignores_only_an_incomplete_trailing_record(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"number": 1}\n{"number": "part', encoding="utf-8")

    assert MODULE.load_jsonl(path) == [{"number": 1}]

    path.write_text('{"number": 1}\n{"number": "part\n', encoding="utf-8")
    assert MODULE.load_jsonl(path) == [{"number": 1}]

    path.write_text('{"number": "broken\n{"number": 2}\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        MODULE.load_jsonl(path)


def test_verified_noop_requires_explicit_task_permission_read_and_passing_tests() -> None:
    evidence = {
        "successful_tool_call_count": 3,
        "has_successful_verification": True,
        "tool_names": ["bash", "read"],
    }

    assert MODULE.verified_noop_allowed(
        {"phase": "edge cases", "allow_verified_noop": True},
        changed_paths=[],
        tool_evidence=evidence,
    )
    assert MODULE.verified_noop_allowed(
        {"phase": "performance", "allow_verified_noop": True},
        changed_paths=[],
        tool_evidence=evidence,
    )
    assert not MODULE.verified_noop_allowed(
        {"phase": "performance", "allow_verified_noop": False},
        changed_paths=[],
        tool_evidence=evidence,
    )
    assert not MODULE.verified_noop_allowed(
        {"phase": "edge cases", "allow_verified_noop": True},
        changed_paths=["quarry/a.py"],
        tool_evidence=evidence,
    )
    assert not MODULE.verified_noop_allowed(
        {"phase": "edge cases", "allow_verified_noop": True},
        changed_paths=[],
        tool_evidence={**evidence, "tool_names": ["bash"]},
    )


def test_performance_tasks_explicitly_allow_evidence_backed_noop(tmp_path: Path) -> None:
    task = next(
        task
        for task in MODULE.build_tasks(tmp_path)
        if task["phase"] == "performance" and task["component"] == "retry budget"
    )

    assert task["allow_verified_noop"] is True
    assert "verified no-op" in task["prompt"]
    assert "基准和测试证据" in task["prompt"]
    assert "不要继续调用工具" in task["prompt"]


def test_active_task_baseline_restores_project_and_session_after_crash(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    project_root = tmp_path / "project"
    session_dir = tmp_path / "session"
    MODULE.initialize_project(project_root)
    session_file = session_dir / "session.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_bytes(b'{"history": []}\n')
    project_baseline = MODULE.capture_project_baseline(project_root)
    session_baseline = MODULE.capture_session_baseline(session_dir)
    MODULE.persist_task_baseline(
        run_root,
        task_number=30,
        project_baseline=project_baseline,
        session_baseline=session_baseline,
    )
    (project_root / "README.md").write_text("poisoned\n", encoding="utf-8")
    (project_root / "quarry" / "new.py").write_text("bad = True\n", encoding="utf-8")
    session_file.write_bytes(b"poisoned")

    restored = MODULE.restore_active_task_baseline(
        run_root,
        expected_task_number=30,
        project_root=project_root,
        session_dir=session_dir,
    )

    assert restored is True
    assert MODULE.capture_project_baseline(project_root) == project_baseline
    assert MODULE.capture_session_baseline(session_dir) == session_baseline
    assert not (run_root / MODULE.ACTIVE_TASK_BASELINE).exists()


def test_committed_task_baseline_is_discarded_on_resume(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    project_root = tmp_path / "project"
    session_dir = tmp_path / "session"
    MODULE.initialize_project(project_root)
    MODULE.persist_task_baseline(
        run_root,
        task_number=29,
        project_baseline=MODULE.capture_project_baseline(project_root),
        session_baseline=MODULE.capture_session_baseline(session_dir),
    )

    assert not MODULE.restore_active_task_baseline(
        run_root,
        expected_task_number=30,
        project_root=project_root,
        session_dir=session_dir,
    )
    assert not (run_root / MODULE.ACTIVE_TASK_BASELINE).exists()


def test_unexpected_exception_marks_acceptance_run_failed(tmp_path: Path) -> None:
    MODULE.write_json(tmp_path / "run.json", {"status": "running"})

    MODULE.mark_run_crashed(tmp_path, RuntimeError("boom"))

    metadata = MODULE.read_json(tmp_path / "run.json")
    assert metadata["status"] == "failed"
    assert metadata["failure"] == "RuntimeError: boom"
    assert metadata["finished_at"]


@pytest.mark.asyncio
async def test_memory_backlog_recovery_restarts_background_job_without_foreground_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Harness:
        def __init__(self) -> None:
            self.covered = 2
            self.retries = 0

        async def wait_idle(self) -> None:
            return None

        async def retry_pending(self, _session) -> bool:
            self.retries += 1
            self.covered = 5
            return True

    harness = Harness()
    runtime = SimpleNamespace(memory_harness=harness)
    manager = SimpleNamespace(load=lambda _session_id: SimpleNamespace(history=[{}] * 5))
    session_ref = SimpleNamespace(session_id="s1", session_dir=str(tmp_path))
    monkeypatch.setattr(
        MODULE,
        "load_projection",
        lambda _session_dir: {"covered_through": harness.covered},
    )

    projection = await MODULE.recover_memory_backlog(
        runtime,
        manager,
        session_ref,
        max_uncovered_messages=0,
        recovery_attempts=2,
    )

    assert projection["covered_through"] == 5
    assert harness.retries == 1


@pytest.mark.asyncio
async def test_memory_backlog_recovery_fails_after_bounded_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = SimpleNamespace()
    harness.wait_idle = lambda: asyncio.sleep(0)
    harness.retry_pending = lambda _session: asyncio.sleep(0, result=True)
    runtime = SimpleNamespace(memory_harness=harness)
    manager = SimpleNamespace(load=lambda _session_id: SimpleNamespace(history=[{}] * 5))
    session_ref = SimpleNamespace(session_id="s1", session_dir=str(tmp_path))
    monkeypatch.setattr(MODULE, "load_projection", lambda _session_dir: {"covered_through": 0})

    with pytest.raises(RuntimeError, match="remains behind"):
        await MODULE.recover_memory_backlog(
            runtime,
            manager,
            session_ref,
            max_uncovered_messages=0,
            recovery_attempts=2,
        )


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
