from __future__ import annotations

from pathlib import Path

from lora.config import load_run_config
from lora.core.io import append_jsonl
from lora.runtime.context_compression import (
    collect_recent_file_reads,
    parse_summary,
    render_file_read_block,
)


def test_parse_summary_requires_non_empty_summary_tags() -> None:
    assert (
        parse_summary("<analysis>x</analysis><summary>\n这是压缩摘要\n</summary>")
        == "这是压缩摘要"
    )
    assert parse_summary("这是普通文本") is None
    assert parse_summary("<summary>   </summary>") is None
    assert parse_summary("</summary><summary>bad") is None
    assert parse_summary("<summary>valid</summary>", has_tool_call=True) is None


def test_file_read_block_restores_recent_five_and_hides_over_limit_results(
    tmp_path: Path,
) -> None:
    logs_dir = tmp_path / "logs"
    for index in range(1, 8):
        append_jsonl(
            logs_dir / "file_events.jsonl",
            {
                "type": "file.read",
                "path": f"src/file_{index}.py",
                "created_at": f"2026-06-15T00:00:0{index}+00:00",
                "payload": {
                    "path": f"src/file_{index}.py",
                    "returned_range": {
                        "unit": "line",
                        "start": index,
                        "end": index + 10,
                    },
                    "returned_content": f"content-{index}",
                },
            },
        )
    append_jsonl(
        logs_dir / "file_events.jsonl",
        {
            "type": "file.read",
            "path": "src/large.py",
            "created_at": "2026-06-15T00:00:08+00:00",
            "payload": {
                "path": "src/large.py",
                "returned_range": {"unit": "line", "start": 120, "end": 220},
                "returned_content": "x" * 5001,
            },
        },
    )

    records = collect_recent_file_reads(tmp_path, count=5)
    xml, metadata = render_file_read_block(records, max_chars=5000)

    assert "src/file_1.py" not in xml
    assert "src/file_2.py" not in xml
    assert "src/file_4.py" in xml
    assert xml.index("src/file_4.py") < xml.index("src/file_7.py")
    assert 'path="src/large.py"' in xml
    assert 'range="120-220"' in xml
    assert 'truncated="true"' in xml
    assert 'char_count="5001"' in xml
    assert "The previous file read result is too large" in xml
    assert "x" * 100 not in xml
    assert metadata[-1] == {
        "path": "src/large.py",
        "mode": "partial",
        "included": False,
        "char_count": 5001,
        "truncated": True,
        "range": "120-220",
    }


def test_file_read_length_threshold_is_inclusive(tmp_path: Path) -> None:
    for name, size in (("lt", 4999), ("eq", 5000), ("gt", 5001)):
        append_jsonl(
            tmp_path / "logs" / "file_events.jsonl",
            {
                "type": "file.read",
                "path": f"src/{name}.txt",
                "created_at": f"2026-06-15T00:00:0{len(name)}+00:00",
                "payload": {
                    "path": f"src/{name}.txt",
                    "returned_range": {"unit": "full", "start": 1, "end": "EOF"},
                    "returned_content": name * (size // len(name))
                    + name[: size % len(name)],
                },
            },
        )

    xml, metadata = render_file_read_block(
        collect_recent_file_reads(tmp_path),
        max_chars=5000,
    )

    assert 'path="src/lt.txt" mode="full" truncated="false" char_count="4999"' in xml
    assert 'path="src/eq.txt" mode="full" truncated="false" char_count="5000"' in xml
    assert 'path="src/gt.txt" mode="full" truncated="true" char_count="5001"' in xml
    assert [item["included"] for item in metadata] == [True, True, False]


def test_zero_file_read_count_disables_file_read_injection(tmp_path: Path) -> None:
    append_jsonl(
        tmp_path / "logs" / "file_events.jsonl",
        {
            "type": "file.read",
            "payload": {"path": "secret.txt", "returned_content": "secret"},
        },
    )

    assert collect_recent_file_reads(tmp_path, count=0) == []


def test_context_compression_config_resolves_from_model_and_runtime_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    user_root = home / ".lora"
    user_root.mkdir(parents=True)
    (user_root / "config.yaml").write_text(
        """agent:
  default_alias: dev
agents:
  - alias: dev
    model_request:
      context_window: 32000
      routes:
        - id: primary
          provider: openai
          model_name: profile-model
          base_url: https://example.test/v1
          api_key_env: DEV_API_KEY
context_compression:
  enabled: true
  trigger_ratio: 0.75
  file_read_count: 4
  file_read_max_chars: 1234
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("lora.config.loader.Path.home", lambda: home)

    config = load_run_config(workspace_root=tmp_path)

    assert config.context_window == 32000
    assert config.context_compression_enabled is True
    assert config.context_compression_trigger_ratio == 0.75
    assert config.context_compression_file_read_count == 4
    assert config.context_compression_file_read_max_chars == 1234
