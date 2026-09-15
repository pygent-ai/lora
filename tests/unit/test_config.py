from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lora.config import load_run_config, replace_user_model_config
from lora.config.yaml_subset import dump_yaml_subset, parse_yaml_subset


NATIVE_CONFIG = """
agent:
  default_alias: dev
agents:
  - alias: dev
    model_request:
      default_model_group: coding
      retry:
        max_attempts_per_model: 3
models:
  main:
    provider: openai
    model_id: model-a
    protocol: openai_chat_completions
    connection:
      base_url: https://example.test/v1
      credential:
        env: TEST_ROUTE_KEY
      verify_ssl: true
    provider_options: {}
    capabilities:
      modalities:
        input: [text]
        output: [text]
      streaming:
        output: [text]
      tools:
        call: true
        choice: [none, auto, required, named]
        parallel: true
      structured_output:
        json_object: true
        json_schema: true
      reasoning:
        supported: true
        controllable: true
      limits:
        context_tokens: 100000
        max_output_tokens: 10000
model_groups:
  coding:
    models: [main]
"""

LEGACY_CONFIG = """
agent:
  default_alias: dev
agents:
  - alias: dev
    model_request:
      profile: production
      routes:
        - id: primary
          provider: openai
          model_name: model-a
          base_url: https://example.test/v1
          api_key_env: TEST_ROUTE_KEY
      fallback: [primary]
"""


def write_user_config(tmp_path: Path, content: str) -> Path:
    home = tmp_path / "home"
    user_root = home / ".lora"
    user_root.mkdir(parents=True)
    (user_root / "config.yaml").write_text(content, encoding="utf-8")
    return home


def test_native_pygent_model_config_is_loaded_without_route_translation(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, NATIVE_CONFIG)
    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=tmp_path)
    assert config.model_config is not None
    assert tuple(config.model_config.models) == ("main",)
    assert tuple(entry.name for entry in config.model_config.model_groups["coding"].models) == ("main",)
    assert config.resolved_agent is not None
    assert config.resolved_agent.default_model_group == "coding"
    assert config.resolved_agent.retry.max_attempts_per_model == 3
    assert config.model_configuration_status == "configured"
    assert config.model_configuration_error is None


def test_legacy_routes_boot_as_explicitly_unconfigured(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, LEGACY_CONFIG)
    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=tmp_path)
    assert config.model_config is None
    assert config.resolved_agent is None
    assert config.model_configuration_status == "legacy"
    assert "legacy_model_configuration" in str(config.model_configuration_error)


def test_yaml_subset_dump_round_trips_native_model_values() -> None:
    data = {
        "lora_root": r"C:\Users\agent's data\.lora",
        "models": {"main": {"connection": {"base_url": "https://example.test/v1#chat", "credential": {"env": "MAIN_KEY"}}}},
        "model_groups": {"coding": {"models": ["main"]}},
    }
    assert parse_yaml_subset(dump_yaml_subset(data)) == data


def test_unknown_model_request_field_is_rejected(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, "agents:\n  - alias: dev\n    model_request:\n      model_name: old-model\n")
    with patch("lora.config.loader.Path.home", return_value=home), pytest.raises(ValueError, match=r"model_request contains unknown fields: model_name"):
        load_run_config(workspace_root=tmp_path, agent_alias="dev")


def test_native_model_fields_are_validated_by_pygent(tmp_path: Path) -> None:
    invalid = NATIVE_CONFIG.replace("    connection:\n      base_url: https://example.test/v1\n      credential:\n        env: TEST_ROUTE_KEY\n      verify_ssl: true\n", "")
    home = write_user_config(tmp_path, invalid)
    with patch("lora.config.loader.Path.home", return_value=home), pytest.raises(ValueError, match="missing model fields: connection"):
        load_run_config(workspace_root=tmp_path)


def test_default_configuration_is_explicitly_unconfigured(tmp_path: Path) -> None:
    with patch("lora.config.loader.Path.home", return_value=tmp_path / "home"):
        config = load_run_config(workspace_root=tmp_path)
    assert config.resolved_agent is None
    assert config.model_config is None
    assert config.model_configuration_status == "unconfigured"
    assert config.model_configuration_error == "model_configuration_required"


def test_runtime_and_context_settings_are_loaded(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, NATIVE_CONFIG + "\ncontext_window: 64000\ncontext_compression:\n  enabled: false\n" + "runtime:\n  capacity:\n    scope: deployment\n    coordinator_path: .state/capacity.sqlite3\n")
    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=tmp_path)
    assert config.context_window == 64000
    assert config.context_compression_enabled is False
    assert config.runtime_capacity.scope == "deployment"
    assert config.runtime_capacity.coordinator_path == str((Path(config.lora_root) / ".state/capacity.sqlite3").resolve())


def test_missing_agent_alias_is_rejected(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, NATIVE_CONFIG)
    with patch("lora.config.loader.Path.home", return_value=home), pytest.raises(ValueError, match="not configured"):
        load_run_config(workspace_root=tmp_path, agent_alias="missing")


def test_user_model_config_is_shared_across_project_workspaces(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, NATIVE_CONFIG.replace("TEST_ROUTE_KEY", "USER_MODEL_KEY").replace("model-a", "user-model"))
    (home / ".lora" / "credentials.env").write_text("USER_MODEL_KEY=user-secret\n", encoding="utf-8")
    workspaces = [tmp_path / "project-a", tmp_path / "project-b"]
    for workspace in workspaces:
        workspace.mkdir()
    os.environ.pop("USER_MODEL_KEY", None)
    try:
        with patch("lora.config.loader.Path.home", return_value=home):
            configs = [load_run_config(workspace_root=workspace) for workspace in workspaces]
        for config in configs:
            assert config.model_config is not None
            assert config.model_config.models["main"].spec.model_id == "user-model"
            assert config.model_config.connections["main"].credential.resolve() == "user-secret"
    finally:
        os.environ.pop("USER_MODEL_KEY", None)


def test_project_config_is_not_a_configuration_source(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "lora.yaml").write_text(NATIVE_CONFIG, encoding="utf-8")
    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=workspace)
    assert config.model_configuration_status == "unconfigured"


def test_user_config_owns_runtime_and_approval_settings(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, NATIVE_CONFIG + "\nruntime:\n  durability:\n    mode: required\n  approvals:\n    enabled: true\n    timeout_seconds: 900\n    preauthorized_tools: [write]\n")
    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=tmp_path)
    assert config.runtime_durability.mode == "required"
    assert config.model_config is not None
    assert config.model_config.models["main"].spec.model_id == "model-a"
    assert config.runtime_approvals.enabled is True
    assert config.runtime_approvals.timeout_seconds == 900
    assert config.runtime_approvals.preauthorized_tools == ("write",)


def test_user_config_accepts_runtime_capacity_settings(tmp_path: Path) -> None:
    home = write_user_config(tmp_path, "runtime:\n  capacity:\n    scope: deployment\n")
    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=tmp_path)
    assert config.runtime_capacity.scope == "deployment"


def test_replace_user_model_config_preserves_non_model_settings(tmp_path: Path) -> None:
    user_root = tmp_path / ".lora"
    user_root.mkdir()
    path = user_root / "config.yaml"
    path.write_text("runtime:\n  approvals:\n    enabled: false\n", encoding="utf-8")
    native = parse_yaml_subset(NATIVE_CONFIG)
    model_config = {"models": native["models"], "model_groups": native["model_groups"]}
    replace_user_model_config(user_root, model_config=model_config, agents=native["agents"])
    saved = parse_yaml_subset(path.read_text(encoding="utf-8"))
    assert saved["runtime"]["approvals"]["enabled"] is False
    assert saved["models"] == model_config["models"]
    assert saved["model_groups"] == model_config["model_groups"]
    assert "routes" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("content", "message"), [("max_steps: 0\n", "max_steps"), ("delegation:\n  max_depth: 0\n", "delegation limits"), ("delegation:\n  max_parallel: 0\n", "delegation limits")])
def test_zero_limits_are_rejected_instead_of_replaced(tmp_path: Path, content: str, message: str) -> None:
    home = write_user_config(tmp_path, content)
    with patch("lora.config.loader.Path.home", return_value=home), pytest.raises(ValueError, match=message):
        load_run_config(workspace_root=tmp_path)
