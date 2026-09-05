from __future__ import annotations

from pathlib import Path, PurePosixPath

from lora_api.container import ApiContext
from lora_api.models.responses import (
    WorkspaceEntriesResponse,
    WorkspaceEntryResponse,
    WorkspaceFileResponse,
)
from lora_api.project_state import active_project_scope_id, build_session_scopes

IGNORED_DIRECTORIES = {".git", ".lora", ".venv", "node_modules", "__pycache__"}
IGNORED_FILES = {".env", "nul"}
MAX_DIRECTORY_ENTRIES = 2_000
MAX_TEXT_FILE_BYTES = 1_000_000


def list_workspace_entries(
    context: ApiContext,
    *,
    scope_id: str,
    relative_path: str = "",
) -> WorkspaceEntriesResponse:
    root = workspace_root_for_scope(context, scope_id)
    directory = resolve_workspace_path(root, relative_path)
    if not directory.is_dir():
        raise NotADirectoryError(relative_path or ".")
    entries: list[WorkspaceEntryResponse] = []
    for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
        if child.is_dir() and child.name in IGNORED_DIRECTORIES:
            continue
        if child.is_file() and (child.name.casefold() in IGNORED_FILES or child.name.startswith(".pygent_bash_output_")):
            continue
        try:
            resolved = child.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        is_directory = resolved.is_dir()
        entries.append(
            WorkspaceEntryResponse(
                name=child.name,
                path=resolved.relative_to(root).as_posix(),
                kind="directory" if is_directory else "file",
                size=None if is_directory else resolved.stat().st_size,
            )
        )
        if len(entries) >= MAX_DIRECTORY_ENTRIES:
            break
    return WorkspaceEntriesResponse(
        root=str(root),
        path=_clean_relative_path(relative_path),
        entries=entries,
    )


def read_workspace_file(
    context: ApiContext,
    *,
    scope_id: str,
    relative_path: str,
) -> WorkspaceFileResponse:
    root = workspace_root_for_scope(context, scope_id)
    file_path = resolve_workspace_path(root, relative_path)
    if not file_path.is_file():
        raise FileNotFoundError(relative_path)
    size = file_path.stat().st_size
    if size > MAX_TEXT_FILE_BYTES:
        raise ValueError(f"File is larger than {MAX_TEXT_FILE_BYTES} bytes")
    data = file_path.read_bytes()
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("Binary files cannot be previewed")
    content, encoding = _decode_text(data)
    return WorkspaceFileResponse(
        path=file_path.relative_to(root).as_posix(),
        content=content,
        encoding=encoding,
        size=size,
    )


def workspace_root_for_scope(context: ApiContext, scope_id: str) -> Path:
    if not scope_id or scope_id == "conversation":
        raise ValueError("Select a project to browse files")
    if scope_id == active_project_scope_id(context.config.workspace_root):
        return Path(context.config.workspace_root).resolve()
    scope = next(
        (
            item
            for item in build_session_scopes(
                context.project_state,
                active_workspace_root=context.config.workspace_root,
            )
            if item.scope_id == scope_id and item.workspace_root is not None
        ),
        None,
    )
    if scope is None or scope.workspace_root is None:
        raise ValueError("Unknown project scope")
    return Path(scope.workspace_root).resolve()


def resolve_workspace_path(root: Path, relative_path: str) -> Path:
    clean = _clean_relative_path(relative_path)
    candidate = (root / Path(*PurePosixPath(clean).parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes the project root") from exc
    return candidate


def _clean_relative_path(value: str) -> str:
    clean = str(value or "").replace("\\", "/").strip("/")
    if PurePosixPath(clean).is_absolute():
        raise ValueError("Path must be relative to the project root")
    return clean


def _decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("File encoding is not supported")
