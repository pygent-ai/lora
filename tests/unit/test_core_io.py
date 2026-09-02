from __future__ import annotations

import threading
from pathlib import Path

from lora.core.io import append_jsonl, read_jsonl_snapshot


def test_jsonl_snapshot_waits_for_an_in_process_append(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "events.jsonl"
    original_open = Path.open
    write_started = threading.Event()
    release_write = threading.Event()
    read_finished = threading.Event()
    observed: list[str] = []

    class SlowAppendHandle:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, value: str):
            split = len(value) // 2
            self.handle.write(value[:split])
            self.handle.flush()
            write_started.set()
            assert release_write.wait(2)
            return split + self.handle.write(value[split:])

        def __getattr__(self, name: str):
            return getattr(self.handle, name)

    def controlled_open(self: Path, mode: str = "r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == path and mode == "a":
            return SlowAppendHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", controlled_open)
    writer = threading.Thread(target=append_jsonl, args=(path, {"payload": "x" * 20_000}))

    def read_snapshot() -> None:
        observed.append(read_jsonl_snapshot(path))
        read_finished.set()

    writer.start()
    assert write_started.wait(2)
    reader = threading.Thread(target=read_snapshot)
    reader.start()
    assert not read_finished.wait(0.05)
    release_write.set()
    writer.join(2)
    reader.join(2)

    assert read_finished.is_set()
    assert observed == [path.read_text(encoding="utf-8")]
    assert observed[0].endswith("\n")
