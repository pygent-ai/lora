from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lora.cli import build_parser, main


def test_automation_create_cli_contract() -> None:
    args = build_parser().parse_args(
        [
            "automation",
            "create",
            "--name",
            "Daily check",
            "--prompt",
            "Inspect",
            "--rrule",
            "FREQ=DAILY;BYHOUR=9",
            "--timezone",
            "Asia/Shanghai",
            "--session",
            "chat-1",
        ]
    )

    assert args.automation_command == "create"
    assert args.target_session_id == "chat-1"
    assert args.rrule == "FREQ=DAILY;BYHOUR=9"


def test_automation_create_requires_one_schedule() -> None:
    parser = build_parser()
    try:
        parser.parse_args(
            [
                "automation",
                "create",
                "--name",
                "check",
                "--prompt",
                "Inspect",
                "--standalone",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover
        raise AssertionError("parser accepted an automation without a schedule")


def test_automation_cli_lifecycle_emits_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Inspect from file", encoding="utf-8")
    base = ["--workspace-root", str(workspace), "automation"]
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    def invoke(*arguments: str):
        assert main([*base, *arguments]) == 0
        return json.loads(capsys.readouterr().out)

    created = invoke(
        "create",
        "--name",
        "check",
        "--prompt-file",
        str(prompt_file),
        "--at",
        future,
        "--standalone",
    )
    automation_id = created["automation_id"]
    assert created["prompt"] == "Inspect from file"
    assert invoke("list")["automations"][0]["automation_id"] == automation_id
    assert invoke("show", automation_id)["name"] == "check"
    assert invoke("update", automation_id, "--name", "updated")["name"] == "updated"
    assert invoke("pause", automation_id)["status"] == "paused"
    assert invoke("resume", automation_id)["status"] == "active"
    run = invoke("run", automation_id)
    assert invoke("runs", automation_id)["runs"][0]["run_id"] == run["run_id"]
    assert invoke("delete", automation_id) == {"deleted": True}


def test_automation_cli_reads_prompt_from_stdin_and_rejects_bad_rrule(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("Inspect from stdin"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base = ["--workspace-root", str(workspace), "automation", "create"]
    assert main(
        [
            *base,
            "--name",
            "stdin",
            "--prompt-file",
            "-",
            "--rrule",
            "FREQ=DAILY",
            "--standalone",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["prompt"] == "Inspect from stdin"

    assert main(
        [
            *base,
            "--name",
            "invalid",
            "--prompt",
            "Inspect",
            "--rrule",
            "FREQ=NOT-A-FREQUENCY",
            "--standalone",
        ]
    ) == 2
    assert "error" in capsys.readouterr().err.lower()
