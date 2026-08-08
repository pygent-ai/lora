from __future__ import annotations

import os
from pathlib import Path

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
