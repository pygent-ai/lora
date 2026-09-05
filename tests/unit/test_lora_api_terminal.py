from pathlib import Path

import pytest

from lora.schema import RunConfig
from lora_api.container import ApiContext
from lora_api.services.terminal_service import TerminalService


@pytest.mark.skipif(not Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").exists(), reason="PowerShell required")
def test_powershell_session_runs_in_project_and_preserves_working_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)
    config = RunConfig(workspace_root=str(project), lora_root=str(project / ".lora"))
    context = ApiContext(workspace_root=str(project), state_path=str(tmp_path / "state.json"), _config=config)
    service = TerminalService()
    scope_id = f"project:{project.resolve()}"
    try:
        first = service.execute(context, scope_id, "Write-Output 'hello from lora'")
        service.execute(context, scope_id, "Set-Location child")
        third = service.execute(context, scope_id, "(Get-Location).Path")
    finally:
        service.close()

    assert first.output.strip() == "hello from lora"
    assert first.exit_code == 0
    assert Path(third.output.strip()) == child
    assert Path(third.cwd) == child


def test_terminal_rejects_conversation_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = RunConfig(workspace_root=str(project), lora_root=str(project / ".lora"))
    context = ApiContext(workspace_root=str(project), state_path=str(tmp_path / "state.json"), _config=config)

    with pytest.raises(ValueError, match="Select a project"):
        TerminalService().execute(context, "conversation", "Get-Location")
