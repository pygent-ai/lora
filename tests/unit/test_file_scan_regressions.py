from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lora.runtime.tools import FileEffectTracker, SnapshotBudgetExceeded, _bash_command_may_write
from lora.runtime.file_effects import (FileEffectBaselineStore, process_file_effect_batch,
    DeferredFileEffectBatch, DeferredFileEffectJob)
from lora.runtime.file_effect_models import FileEffect
from lora.schema import CaseRunRef
from lora.tracing import EventStore


@pytest.mark.parametrize("command", [
    "python -c \"print('a -> b')\"",
    "python - <<'PY' 2>&1 | tail -60\nprint('a -> b')\nPY\n",
    "python - <<-\"PY\"\n\tprint('touch x > file')\n\tPY\n",
    "echo 'touch rm >' # > output",
    "echo '<<END'\necho ok",
    "echo ok 2>&1",
])
def test_read_only_shell_syntax(command):
    assert not _bash_command_may_write(command)


@pytest.mark.parametrize("command", [
    "echo hi > file", "echo hi 2>errors", "echo hi >>file",
    "python - <<'PY' >output\nprint('a -> b')\nPY\n",
    "python - <<'PY'\nprint('a -> b')\nPY\ntouch output",
    "echo '<<END'\necho hi >output", "touch x", "npm install", "bash -c 'echo hi >out'",
])
def test_real_shell_writes(command):
    assert _bash_command_may_write(command)


@pytest.fixture
def scan(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = tmp_path / ".lora" / "sessions" / "s1"
    session.mkdir(parents=True)
    (session / "session.json").write_text("{}")
    run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1",
        run_dir=session / "cases" / "chat" / "runs" / "r1")
    return workspace, FileEffectTracker(workspace, EventStore(run)), FileEffectBaselineStore(session), run


def batch(workspace, run, name="bash", declared=None):
    return DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=workspace,
        jobs=[DeferredFileEffectJob(tool_call_id="tool1", tool_name=name,
        args={}, turn_id="turn1", declared=declared or [], requires_snapshot=True)])


def test_prunes_before_descending(scan):
    workspace, tracker, _, _ = scan
    for directory in (".tmp", "node_modules", ".venv-extra", ".git"):
        hidden = workspace / directory
        hidden.mkdir()
        (hidden / "file.txt").write_text("ignored")
    (workspace / "source.txt").write_text("source")
    import os
    original = os.scandir
    def guarded(path):
        assert Path(path).name not in tracker.IGNORED_DIRS
        assert not Path(path).name.startswith(".venv-")
        return original(path)
    with patch("os.scandir", side_effect=guarded):
        assert len(tracker.snapshot_workspace()) == 1


@pytest.mark.parametrize("attribute,value", [
    ("SNAPSHOT_MAX_FILES", 0), ("SNAPSHOT_MAX_BYTES", 1), ("SNAPSHOT_MAX_SECONDS", 0),
])
def test_budget_stops_scan(scan, attribute, value):
    workspace, tracker, _, _ = scan
    (workspace / "file.txt").write_text("content")
    with patch.object(tracker, attribute, value):
        with pytest.raises(SnapshotBudgetExceeded):
            tracker.snapshot_workspace()


def test_incomplete_scan_preserves_baseline_and_reports_event(scan):
    workspace, tracker, baseline, run = scan
    file = workspace / "file.txt"
    file.write_text("before")
    baseline.save(tracker.snapshot_workspace())
    before = baseline.path.read_bytes()
    file.write_text("after")
    with patch.object(FileEffectTracker, "SNAPSHOT_MAX_BYTES", 1):
        process_file_effect_batch(batch(workspace, run))
    assert baseline.path.read_bytes() == before
    rows = list(EventStore.iter_jsonl(Path(run.run_dir) / "events.jsonl"))
    assert any(row["type"] == "runtime.file_scan.incomplete" for row in rows)
    assert not list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))


def test_targeted_edit_preserves_unrelated_baseline(scan):
    workspace, tracker, baseline, run = scan
    target, other = workspace / "target.txt", workspace / "other.txt"
    target.write_text("before")
    other.write_text("other")
    baseline.save(tracker.snapshot_workspace())
    target.write_text("after")
    effect = FileEffect(type="file.edit", path=str(target.resolve()), tool_call_id="tool1",
        tool_name="edit", detected_by=["tool_args"], confidence="declared")
    with patch.object(FileEffectTracker, "_workspace_files", side_effect=AssertionError("full scan")):
        process_file_effect_batch(batch(workspace, run, "edit", [effect]))
    assert baseline.load()[str(other.resolve())].content == "other"
    rows = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
    assert [row["type"] for row in rows] == ["file.edit"]


def test_new_exclusions_do_not_emit_deletions(scan):
    workspace, tracker, baseline, run = scan
    directory = workspace / "node_modules"
    directory.mkdir()
    file = directory / "index.js"
    file.write_text("before")
    baseline.save(tracker.snapshot_workspace(paths=[file]))
    process_file_effect_batch(batch(workspace, run))
    assert not list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))


def test_target_only_baseline_does_not_invent_additions(scan):
    workspace, tracker, baseline, run = scan
    file = workspace / "existing.txt"
    file.write_text("existing")
    baseline.save({}, complete=False)
    process_file_effect_batch(batch(workspace, run))
    assert baseline.is_complete()
    assert str(file.resolve()) in baseline.load()
    assert not list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))


def test_deadline_checked_during_hashing(scan):
    workspace, tracker, _, _ = scan
    target = workspace / "large.txt"
    target.write_bytes(b"x" * (2 * 1024 * 1024))
    # Deadline expires after the first chunk, not only between files.
    with patch("lora.runtime.tools.time.monotonic", side_effect=[0, 0, 0, 6]):
        with pytest.raises(SnapshotBudgetExceeded, match="time budget"):
            tracker.snapshot_workspace(paths=[target])


def test_real_deleted_file_is_still_recorded(scan):
    workspace, tracker, baseline, run = scan
    target = workspace / "gone.txt"
    target.write_text("before")
    baseline.save(tracker.snapshot_workspace())
    target.unlink()
    process_file_effect_batch(batch(workspace, run))
    rows = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
    assert [row["type"] for row in rows] == ["file.delete"]
