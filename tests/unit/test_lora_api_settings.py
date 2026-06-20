from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lora_api.dependencies import ApiContext

def test_update_settings_saves_api_key_and_reloads_runtime_config(tmp_path: Path) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "lora.yaml").write_text(
        "\n".join(
            [
                "agent:",
                "  default_alias: dev",
                "agents:",
                "  - alias: dev",
                "    model_request:",
                "      api_key_env: GUI_TEST_KEY",
                "      model_name: original-model",
                "      base_url: https://example.test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(workspace_root=str(workspace), agent_alias="dev")
        before_manager = context.manager

        response = update_settings(
            UpdateSettingsRequest(
                workspace_root=str(workspace),
                agent_alias="dev",
                model="updated-model",
                max_steps=7,
                api_key="secret-from-gui",
            ),
            context=context,
        )

    credentials_path = home / ".lora" / "credentials.env"
    assert credentials_path.read_text(encoding="utf-8") == "GUI_TEST_KEY=secret-from-gui\n"
    assert response.workspace_root == str(workspace.resolve())
    assert response.agent == "dev"
    assert response.model == "updated-model"
    assert response.max_steps == 7
    assert response.api_key_env == "GUI_TEST_KEY"
    assert response.api_key_source == "env:GUI_TEST_KEY"
    assert context.manager is not before_manager


def test_update_settings_switches_workspace_and_rebuilds_session_manager(tmp_path: Path) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    home = tmp_path / "home"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    (workspace_b / "lora.yaml").write_text(
        "\n".join(
            [
                "agents:",
                "  - alias: other",
                "    model_request:",
                "      api_key_env: OTHER_GUI_KEY",
                "      model_name: workspace-b-model",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(workspace_root=str(workspace_a))
        _ = context.manager

        response = update_settings(
            UpdateSettingsRequest(workspace_root=str(workspace_b), agent_alias="other", max_steps=-1),
            context=context,
        )

    assert response.workspace_root == str(workspace_b.resolve())
    assert response.lora_root == str((workspace_b / ".lora").resolve())
    assert response.agent == "other"
    assert response.model == "workspace-b-model"
    assert response.api_key_env == "OTHER_GUI_KEY"
    assert Path(context.manager.sessions_root) == (workspace_b / ".lora" / "sessions").resolve()
