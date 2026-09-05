from pathlib import Path

import pytest

from lora.schema import RunConfig
from lora_api.container import ApiContext
from lora_api.services.workspace_service import list_workspace_entries, read_workspace_file


def _context(project: Path, state_path: Path) -> ApiContext:
    project.mkdir()
    config = RunConfig(workspace_root=str(project), lora_root=str(project / ".lora"))
    return ApiContext(workspace_root=str(project), state_path=str(state_path), _config=config)


def test_workspace_lists_directories_first_and_reads_text(tmp_path: Path) -> None:
    project = tmp_path / "project"
    context = _context(project, tmp_path / "state.json")
    (project / "src").mkdir()
    (project / "src" / "main.py").write_bytes(b"print('ok')\n")
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    for name in (".git", ".lora", ".venv", "node_modules", "__pycache__"):
        (project / name).mkdir()
    (project / ".env").write_text("SECRET=value", encoding="utf-8")
    (project / ".pygent_bash_output_test").write_text("output", encoding="utf-8")
    scope_id = f"project:{project.resolve()}"

    root = list_workspace_entries(context, scope_id=scope_id)
    source = list_workspace_entries(context, scope_id=scope_id, relative_path="src")
    opened = read_workspace_file(context, scope_id=scope_id, relative_path="src/main.py")

    assert [(item.name, item.kind) for item in root.entries] == [
        (".git", "directory"),
        (".lora", "directory"),
        (".venv", "directory"),
        ("__pycache__", "directory"),
        ("node_modules", "directory"),
        ("src", "directory"),
        (".env", "file"),
        (".pygent_bash_output_test", "file"),
        ("README.md", "file"),
    ]
    assert [item.path for item in source.entries] == ["src/main.py"]
    assert opened.content == "print('ok')\n"
    assert opened.encoding == "utf-8"


def test_workspace_rejects_conversation_scope_and_escaping_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    context = _context(project, tmp_path / "state.json")
    scope_id = f"project:{project.resolve()}"

    with pytest.raises(ValueError, match="Select a project"):
        list_workspace_entries(context, scope_id="conversation")
    with pytest.raises(ValueError, match="escapes"):
        read_workspace_file(context, scope_id=scope_id, relative_path="../outside.txt")


def test_workspace_rejects_binary_and_large_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    context = _context(project, tmp_path / "state.json")
    scope_id = f"project:{project.resolve()}"
    (project / "binary.bin").write_bytes(b"abc\x00def")
    (project / "large.txt").write_bytes(b"x" * 1_000_001)

    with pytest.raises(ValueError, match="Binary"):
        read_workspace_file(context, scope_id=scope_id, relative_path="binary.bin")
    with pytest.raises(ValueError, match="larger"):
        read_workspace_file(context, scope_id=scope_id, relative_path="large.txt")
