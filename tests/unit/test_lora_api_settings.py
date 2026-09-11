from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from lora.core.paths import project_lora_root
from lora_api.dependencies import ApiContext


def test_settings_toggle_approvals_persists_and_preserves_runtime(
    tmp_path: Path,
) -> None:
    from lora.config import load_run_config
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import get_settings, update_settings

    home = tmp_path / "home"
    write_user_config(
        home,
        [
            "runtime:",
            "  approvals:",
            "    enabled: true",
            "    timeout_seconds: 123",
            "    preauthorized_tools: [write]",
        ],
    )
    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(
            workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json")
        )
        assert get_settings(context).approvals_enabled is True
        for enabled in (False, True):
            response = asyncio.run(
                update_settings(
                    UpdateSettingsRequest(approvals_enabled=enabled),
                    context=context,
                )
            )
            assert response.approvals_enabled is enabled
            assert get_settings(context).approvals_enabled is enabled
            config = load_run_config(workspace_root=tmp_path)
            assert config.runtime_approvals.enabled is enabled
            assert config.runtime_approvals.timeout_seconds == 123
            assert config.runtime_approvals.preauthorized_tools == ("write",)
        asyncio.run(
            update_settings(UpdateSettingsRequest(max_steps=7), context=context)
        )
        assert context.config.runtime_approvals.enabled is True


class _RecordingChatRegistry:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RecordingRuntimePool:
    def __init__(self) -> None:
        self.retired = []

    async def retire_scope(self, scope) -> None:
        self.retired.append(scope)


def write_user_config(home: Path, lines: list[str]) -> None:
    user_root = home / ".lora"
    user_root.mkdir(parents=True)
    (user_root / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_update_settings_saves_api_key_and_reloads_runtime_config(
    tmp_path: Path,
) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_user_config(
        home,
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
        ],
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(workspace_root=str(workspace), agent_alias="dev")
        before_manager = context.manager

        response = asyncio.run(
            update_settings(
                UpdateSettingsRequest(
                    workspace_root=str(workspace),
                    agent_alias="dev",
                    max_steps=7,
                    context_window=64000,
                    api_key="secret-from-gui",
                ),
                context=context,
            )
        )

    credentials_path = home / ".lora" / "credentials.env"
    assert (
        credentials_path.read_text(encoding="utf-8") == "GUI_TEST_KEY=secret-from-gui\n"
    )
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

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_user_config(
        home,
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
        ],
    )

    context = ApiContext(
        workspace_root=str(workspace),
        agent_alias="dev",
        context_window=64000,
        state_path=str(tmp_path / "state.json"),
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        response = asyncio.run(
            update_settings(UpdateSettingsRequest(context_window=None), context=context)
        )

    assert response.context_window == 32000
    assert context.context_window is None


def test_update_settings_switches_workspace_and_rebuilds_session_manager(
    tmp_path: Path,
) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    home = tmp_path / "home"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    write_user_config(
        home,
        [
            "agent:",
            "  default_alias: other",
            "agents:",
            "  - alias: other",
            "    model_request:",
            "      routes:",
            "        - id: primary",
            "          provider: openai",
            "          api_key_env: OTHER_GUI_KEY",
            "          model_name: user-model",
            "          base_url: https://example.test",
        ],
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(workspace_root=str(workspace_a))
        _ = context.manager

        response = asyncio.run(
            update_settings(
                UpdateSettingsRequest(
                    workspace_root=str(workspace_b), agent_alias="other", max_steps=-1
                ),
                context=context,
            )
        )

    assert response.workspace_root == str(workspace_b.resolve())
    assert response.lora_root == str(project_lora_root(workspace_b, home / ".lora"))
    assert response.agent == "other"
    assert response.routes[0]["model_name"] == "user-model"
    assert response.routes[0]["api_key_env"] == "OTHER_GUI_KEY"
    assert (
        Path(context.manager.sessions_root)
        == project_lora_root(workspace_b, home / ".lora") / "sessions"
    )


def test_update_settings_preserves_application_chat_registry(tmp_path: Path) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    context = ApiContext(
        workspace_root=str(workspace_a), state_path=str(tmp_path / "state.json")
    )
    registry = _RecordingChatRegistry()
    context.attach_chat_registry(registry)

    asyncio.run(
        update_settings(
            UpdateSettingsRequest(workspace_root=str(workspace_b), max_steps=-1),
            context=context,
        )
    )

    assert context.chat_registry is registry
    assert registry.closed is False


def test_update_settings_retires_runtime_scope_without_breaking_active_waiters(
    tmp_path: Path,
) -> None:
    from lora.orchestration import RuntimeScopeKey

    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    context = ApiContext(
        workspace_root=str(workspace_a), state_path=str(tmp_path / "state.json")
    )
    registry = _RecordingChatRegistry()
    pool = _RecordingRuntimePool()
    context.attach_chat_registry(registry)
    old_scope = RuntimeScopeKey.from_config(context.config)
    context._runtime_pool = pool

    asyncio.run(context.areload({"workspace_root": str(workspace_b)}))

    assert pool.retired == [old_scope]


def test_update_settings_persists_model_group_and_fallback_order(
    tmp_path: Path,
) -> None:
    from lora_api.models.requests import UpdateSettingsRequest
    from lora_api.routers.settings import update_settings

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_user_config(
        home,
        [
            "runtime:",
            "  approvals:",
            "    enabled: true",
        ],
    )

    request = UpdateSettingsRequest.model_validate(
        {
            "agent_alias": "dev",
            "model_group": {
                "profile": "production",
                "routes": [
                    {
                        "id": "primary",
                        "provider": "openai",
                        "model_name": "model-a",
                        "base_url": "https://main.test/v1",
                        "api_key_env": "MAIN_KEY",
                    },
                    {
                        "id": "backup",
                        "provider": "openai",
                        "model_name": "model-b",
                        "base_url": "https://backup.test/v1",
                        "api_key_env": "BACKUP_KEY",
                        "api_key": "backup-secret",
                    },
                ],
                "fallback": ["primary", "backup"],
                "retry": {
                    "max_attempts_per_route": 3,
                    "attempt_idle_timeout_seconds": 45,
                    "backoff_initial": 0,
                    "backoff_maximum": 3,
                    "backoff_multiplier": 2,
                },
            },
        }
    )

    with patch("lora.config.loader.Path.home", return_value=home):
        context = ApiContext(workspace_root=str(workspace))
        response = asyncio.run(update_settings(request, context=context))

    saved = (home / ".lora" / "config.yaml").read_text(encoding="utf-8")
    assert "enabled: true" in saved
    assert response.agent == "dev"
    assert response.profile == "production"
    assert response.fallback == ["primary", "backup"]
    assert response.retry["max_attempts_per_route"] == 3
    assert [route["model_name"] for route in response.routes] == ["model-a", "model-b"]
    assert (home / ".lora" / "credentials.env").read_text(
        encoding="utf-8"
    ) == "BACKUP_KEY=backup-secret\n"
