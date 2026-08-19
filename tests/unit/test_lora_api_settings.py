from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from lora_api.dependencies import ApiContext


class _RecordingChatRegistry:
    def __init__(self) -> None:
        self.closed = False
        self.retired = []

    async def close(self) -> None:
        self.closed = True

    async def retire_runtime(self, runtime) -> None:
        self.retired.append(runtime)


class _RecordingRuntime:
    def __init__(self) -> None:
        self.closed = []

    async def close(self, *, cancel: bool) -> None:
        self.closed.append(cancel)

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
                "      context_window: 32000",
                "      routes:",
                "        - id: primary",
                "          provider: openai",
                "          api_key_env: GUI_TEST_KEY",
                "          model_name: original-model",
                "          base_url: https://example.test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(workspace_root=str(workspace), agent_alias="dev")
        before_manager = context.manager

        response = asyncio.run(update_settings(
            UpdateSettingsRequest(
                workspace_root=str(workspace),
                agent_alias="dev",
                max_steps=7,
                context_window=64000,
                api_key="secret-from-gui",
            ),
            context=context,
        ))

    credentials_path = home / ".lora" / "credentials.env"
    assert credentials_path.read_text(encoding="utf-8") == "GUI_TEST_KEY=secret-from-gui\n"
    assert response.workspace_root == str(workspace.resolve())
    assert response.agent == "dev"
    assert response.routes[0]["model_name"] == "original-model"
    assert response.max_steps == 7
    assert response.context_window == 64000
    assert response.routes[0]["api_key_env"] == "GUI_TEST_KEY"
    assert response.routes[0]["api_key_source"] == "user-file:GUI_TEST_KEY"
    assert context.manager is not before_manager


def test_update_settings_can_clear_context_window_override(tmp_path: Path) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "lora.yaml").write_text(
        "\n".join(
            [
                "agents:",
                "  - alias: dev",
                "    model_request:",
                "      context_window: 32000",
                "      routes:",
                "        - id: primary",
                "          provider: openai",
                "          model_name: original-model",
                "          base_url: https://example.test",
                "          api_key_env: GUI_TEST_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    context = ApiContext(
        workspace_root=str(workspace),
        agent_alias="dev",
        context_window=64000,
        state_path=str(tmp_path / "state.json"),
    )

    response = asyncio.run(update_settings(UpdateSettingsRequest(context_window=None), context=context))

    assert response.context_window == 32000
    assert context.context_window is None


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
                "      routes:",
                "        - id: primary",
                "          provider: openai",
                "          api_key_env: OTHER_GUI_KEY",
                "          model_name: workspace-b-model",
                "          base_url: https://example.test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(workspace_root=str(workspace_a))
        _ = context.manager

        response = asyncio.run(update_settings(
            UpdateSettingsRequest(workspace_root=str(workspace_b), agent_alias="other", max_steps=-1),
            context=context,
        ))

    assert response.workspace_root == str(workspace_b.resolve())
    assert response.lora_root == str((workspace_b / ".lora").resolve())
    assert response.agent == "other"
    assert response.routes[0]["model_name"] == "workspace-b-model"
    assert response.routes[0]["api_key_env"] == "OTHER_GUI_KEY"
    assert Path(context.manager.sessions_root) == (workspace_b / ".lora" / "sessions").resolve()


def test_update_settings_preserves_application_chat_registry(tmp_path: Path) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    context = ApiContext(workspace_root=str(workspace_a), state_path=str(tmp_path / "state.json"))
    registry = _RecordingChatRegistry()
    context.attach_chat_registry(registry)

    asyncio.run(update_settings(
        UpdateSettingsRequest(workspace_root=str(workspace_b), max_steps=-1),
        context=context,
    ))

    assert context.chat_registry is registry
    assert registry.closed is False


def test_update_settings_retires_runtime_without_breaking_active_waiters(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    context = ApiContext(workspace_root=str(workspace_a), state_path=str(tmp_path / "state.json"))
    registry = _RecordingChatRegistry()
    runtime = _RecordingRuntime()
    context.attach_chat_registry(registry)
    context._runtime_service = runtime

    asyncio.run(context.areload({"workspace_root": str(workspace_b)}))

    assert registry.retired == [runtime]
    assert runtime.closed == []
