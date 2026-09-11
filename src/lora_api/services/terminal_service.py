from __future__ import annotations

import base64
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from lora_api.container import ApiContext
from lora_api.models.responses import TerminalCommandResponse
from lora_api.services.workspace_service import workspace_root_for_scope

MAX_TERMINAL_OUTPUT_CHARS = 1_000_000
TERMINAL_COMMAND_TIMEOUT_SECONDS = 120


class PowerShellSession:
    """Keep the location while running each command in an isolated process."""

    def __init__(self, cwd: Path) -> None:
        self.initial_cwd = cwd
        self.cwd = cwd
        self._lock = threading.Lock()
        self.executable = (
            shutil.which("powershell.exe")
            or shutil.which("pwsh")
            or shutil.which("powershell")
        )
        if self.executable is None:
            raise RuntimeError("PowerShell is not installed")

    def execute(self, command: str) -> TerminalCommandResponse:
        with self._lock:
            token = uuid.uuid4().hex
            exit_marker = f"__LORA_EXIT_{token}__"
            cwd_marker = f"__LORA_CWD_{token}__"
            command_encoded = base64.b64encode(command.encode("utf-16-le")).decode(
                "ascii"
            )
            script = (
                "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
                f"Set-Location -LiteralPath '{_powershell_quote(str(self.cwd))}';"
                f"$c=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{command_encoded}'));"
                "$global:LASTEXITCODE=0;$loraOk=$true;"
                "try { Invoke-Expression $c; $loraOk=$? } "
                "catch { Write-Output ('ERROR: ' + $_.Exception.Message); $loraOk=$false };"
                "$loraCode=if($loraOk){[int]$LASTEXITCODE}else{1};"
                f"Write-Output ('{exit_marker}'+$loraCode);"
                f"Write-Output ('{cwd_marker}'+(Get-Location).Path)"
            )
            payload = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            executable = self.executable
            assert executable is not None
            try:
                completed = subprocess.run(
                    [
                        executable,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-EncodedCommand",
                        payload,
                    ],
                    cwd=str(self.cwd),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=TERMINAL_COMMAND_TIMEOUT_SECONDS,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError("PowerShell command timed out") from exc

            output: list[str] = []
            exit_code = completed.returncode
            cwd = self.cwd
            for line in completed.stdout.splitlines(keepends=True):
                clean = line.rstrip("\r\n")
                if clean.startswith(exit_marker):
                    try:
                        exit_code = int(clean.removeprefix(exit_marker))
                    except ValueError:
                        exit_code = 1
                elif clean.startswith(cwd_marker):
                    cwd = Path(clean.removeprefix(cwd_marker))
                else:
                    output.append(line)
            if completed.stderr and not completed.stderr.lstrip().startswith(
                "#< CLIXML"
            ):
                output.append(completed.stderr)
            self.cwd = cwd
            rendered = "".join(output)
            if len(rendered) > MAX_TERMINAL_OUTPUT_CHARS:
                rendered = (
                    rendered[:MAX_TERMINAL_OUTPUT_CHARS] + "\n[output truncated]\n"
                )
            return TerminalCommandResponse(
                output=rendered, exit_code=exit_code, cwd=str(cwd)
            )

    def close(self) -> None:
        return


class TerminalService:
    def __init__(self) -> None:
        self._sessions: dict[str, PowerShellSession] = {}
        self._lock = threading.Lock()

    def execute(
        self, context: ApiContext, scope_id: str, command: str
    ) -> TerminalCommandResponse:
        root = workspace_root_for_scope(context, scope_id)
        with self._lock:
            session = self._sessions.get(scope_id)
            if session is None:
                session = PowerShellSession(root)
                self._sessions[scope_id] = session
        return session.execute(command)

    def reset(self, scope_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(scope_id, None)
        if session is not None:
            session.close()
            return True
        return False

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()


def _powershell_quote(value: str) -> str:
    return value.replace("'", "''")
