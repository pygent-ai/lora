from types import SimpleNamespace

from lora.tracing.diffing import DiffRecorder


class _Store:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.events = []

    def append(self, event, **kwargs):
        self.events.append((event, kwargs))


def test_external_file_effect_is_audited_without_aborting(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _Store(tmp_path / "run")
    effect = SimpleNamespace(
        type="file.write",
        path=tmp_path / "outside.txt",
        tool_call_id="tool-1",
        tool_name="bash",
    )

    DiffRecorder(store, workspace).record_effect(effect, turn_id="turn-1")

    assert len(store.events) == 1
    event, data = store.events[0]
    assert event == "diff.created"
    assert data["payload"]["external_to_workspace"] is True
    assert data["payload"]["relative_path"] is None
    assert data["payload"]["patch_available"] is False
    assert data["turn_id"] == "turn-1"
