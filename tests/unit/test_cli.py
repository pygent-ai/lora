from __future__ import annotations

import argparse
import io
from pathlib import Path
from unittest.mock import patch

from lora.cli import _chat_message_payload, main
from lora.schema import CaseRunRef


def test_main_prints_json_without_escaping_non_ascii() -> None:
    parser = argparse.ArgumentParser()
    parser.set_defaults(handler=lambda args: {"final_answer": "开发指南"})
    stdout = io.StringIO()
    with patch("lora.cli.main.build_parser", return_value=parser), patch("sys.stdout", stdout):
        assert main([]) == 0
    assert '"final_answer": "开发指南"' in stdout.getvalue()


def test_chat_message_payload_is_the_cli_result_contract() -> None:
    run_ref = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir="runs/r1")
    assert _chat_message_payload(
        run_ref,
        {"status": "passed", "final_answer": "agent answer", "error": None},
    ) == {
        "final_answer": "agent answer",
        "session_id": "s1",
        "case_run_id": "r1",
        "run_dir": str(Path("runs/r1").resolve()),
    }
