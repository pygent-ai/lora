from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lora.config import load_run_config


ROUTES_CONFIG = """
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
      retry:
        max_attempts_per_route: 3
runtime:
  approvals:
    enabled: true
"""


def test_routes_are_the_only_model_configuration(tmp_path: Path) -> None:
    (tmp_path / "lora.yaml").write_text(ROUTES_CONFIG, encoding="utf-8")
    os.environ["TEST_ROUTE_KEY"] = "secret"
    try:
        config = load_run_config(workspace_root=tmp_path)
    finally:
        os.environ.pop("TEST_ROUTE_KEY", None)
    agent = config.resolved_agent
    assert agent is not None
    assert agent.profile == "production"
    assert agent.fallback == ("primary",)
    assert agent.retry.max_attempts_per_route == 3
    assert agent.routes[0].model_name == "model-a"
    assert agent.routes[0].api_key == "secret"


def test_unknown_model_request_field_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "lora.yaml").write_text(
        "agents:\n  - alias: dev\n    model_request:\n      model_name: old-model\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"model_request contains unknown fields: model_name"):
        load_run_config(workspace_root=tmp_path, agent_alias="dev")


def test_route_fields_are_required(tmp_path: Path) -> None:
    (tmp_path / "lora.yaml").write_text(
        "agents:\n  - alias: dev\n    model_request:\n      routes:\n        - id: primary\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="provider is required"):
        load_run_config(workspace_root=tmp_path, agent_alias="dev")


def test_default_configuration_is_routes_based(tmp_path: Path) -> None:
    config = load_run_config(workspace_root=tmp_path)
    assert config.resolved_agent is not None
    assert config.resolved_agent.routes[0].id == "primary"
    assert config.resolved_agent.routes[0].model_name == "deepseek-v4-flash"


def test_runtime_and_context_settings_are_loaded(tmp_path: Path) -> None:
    (tmp_path / "lora.yaml").write_text(
        ROUTES_CONFIG
        + "\ncontext_window: 64000\ncontext_compression:\n  enabled: false\n"
        + "runtime:\n  capacity:\n    scope: deployment\n    coordinator_path: .state/capacity.sqlite3\n",
        encoding="utf-8",
    )
    config = load_run_config(workspace_root=tmp_path)
    assert config.context_window == 64000
    assert config.context_compression_enabled is False
    assert config.runtime_capacity.scope == "deployment"
    assert config.runtime_capacity.coordinator_path == str((tmp_path / ".state/capacity.sqlite3").resolve())


def test_missing_agent_alias_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "lora.yaml").write_text(ROUTES_CONFIG, encoding="utf-8")
    with pytest.raises(ValueError, match="not configured"):
        load_run_config(workspace_root=tmp_path, agent_alias="missing")


def test_user_model_config_is_shared_across_project_workspaces(tmp_path: Path) -> None:
    home = tmp_path / "home"
    user_root = home / ".lora"
    user_root.mkdir(parents=True)
    (user_root / "config.yaml").write_text(
        "agent:\n"
        "  default_alias: user\n"
        "agents:\n"
        "  - alias: user\n"
        "    model_request:\n"
        "      profile: user-default\n"
        "      routes:\n"
        "        - id: primary\n"
        "          provider: openai\n"
        "          model_name: user-model\n"
        "          base_url: https://user.example/v1\n"
        "          api_key_env: USER_MODEL_KEY\n",
        encoding="utf-8",
    )
    (user_root / "credentials.env").write_text("USER_MODEL_KEY=user-secret\n", encoding="utf-8")
    project_config = ROUTES_CONFIG.replace("model-a", "project-model")
    workspaces = [tmp_path / "project-a", tmp_path / "project-b"]
    for workspace in workspaces:
        workspace.mkdir()
        (workspace / "lora.yaml").write_text(project_config, encoding="utf-8")

    with patch("lora.config.loader.Path.home", return_value=home):
        configs = [load_run_config(workspace_root=workspace) for workspace in workspaces]

    for config in configs:
        assert config.agent_alias == "user"
        assert config.resolved_agent is not None
        assert config.resolved_agent.profile == "user-default"
        assert config.resolved_agent.routes[0].model_name == "user-model"
        assert config.resolved_agent.routes[0].api_key == "user-secret"
        assert config.resolved_agent.routes[0].api_key_source == "user-file:USER_MODEL_KEY"


def test_project_model_config_remains_compatible_without_user_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "lora.yaml").write_text(ROUTES_CONFIG, encoding="utf-8")

    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=workspace)

    assert config.resolved_agent is not None
    assert config.resolved_agent.routes[0].model_name == "model-a"


def test_user_approval_config_is_shared_and_preserves_project_runtime(tmp_path: Path) -> None:
    home = tmp_path / "home"
    user_root = home / ".lora"
    user_root.mkdir(parents=True)
    (user_root / "config.yaml").write_text(
        "runtime:\n  approvals:\n    timeout_seconds: 900\n    preauthorized_tools: [write]\n",
        encoding="utf-8",
    )
    (tmp_path / "lora.yaml").write_text(
        ROUTES_CONFIG
        + "\n"
        "runtime:\n"
        "  durability:\n    mode: required\n"
        "  approvals:\n    enabled: true\n    timeout_seconds: 30\n",
        encoding="utf-8",
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        config = load_run_config(workspace_root=tmp_path)

    assert config.runtime_durability.mode == "required"
    assert config.resolved_agent is not None
    assert config.resolved_agent.routes[0].model_name == "model-a"
    assert config.runtime_approvals.enabled is True
    assert config.runtime_approvals.timeout_seconds == 900
    assert config.runtime_approvals.preauthorized_tools == ("write",)


def test_user_config_rejects_non_approval_runtime_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    user_root = home / ".lora"
    user_root.mkdir(parents=True)
    (user_root / "config.yaml").write_text("runtime:\n  capacity:\n    scope: deployment\n", encoding="utf-8")

    with patch("lora.config.loader.Path.home", return_value=home), pytest.raises(
        ValueError,
        match=r"user runtime contains unknown fields: capacity",
    ):
        load_run_config(workspace_root=tmp_path)
