from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lora.schema import RunConfig
from lora.secrets import DEFAULT_API_KEY_ENV, credential_is_configured


@dataclass(frozen=True, slots=True)
class SettingsValues:
    workspace_root: str
    config_path: str
    agent_alias: str
    model: str
    max_steps: int
    api_key_env: str
    api_key: str


class SettingsDialog(QDialog):
    def __init__(self, config: RunConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Lora Settings")
        self.setObjectName("SettingsDialog")
        self.setModal(True)
        self._config = config
        self._api_key_env = _api_key_env_name(config)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setObjectName("SettingsForm")

        self.workspace = QLineEdit(config.workspace_root)
        self.workspace.setObjectName("SettingsWorkspaceInput")
        self.config_path = QLineEdit("")
        self.config_path.setObjectName("SettingsConfigPathInput")
        self.agent_alias = QLineEdit(config.agent_alias)
        self.agent_alias.setObjectName("SettingsAgentAliasInput")
        self.model = QLineEdit(config.model or config.model_name or "")
        self.model.setObjectName("SettingsModelInput")
        self.max_steps = QSpinBox()
        self.max_steps.setObjectName("SettingsMaxStepsInput")
        self.max_steps.setRange(-1, 100000)
        self.max_steps.setValue(config.max_steps)

        self.api_key_env_label = QLabel(self._api_key_env)
        self.api_key_env_label.setObjectName("SettingsApiKeyEnvLabel")
        self.api_key_status = QLabel(_api_key_status_text(config, self._api_key_env))
        self.api_key_status.setObjectName("SettingsApiKeyStatusLabel")
        self.api_key = QLineEdit()
        self.api_key.setObjectName("SettingsApiKeyInput")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Leave blank to keep the current key")

        form.addRow("Workspace", self.workspace)
        form.addRow("Config path", self.config_path)
        form.addRow("Agent alias", self.agent_alias)
        form.addRow("Model override", self.model)
        form.addRow("Max steps", self.max_steps)
        form.addRow("API key env", self.api_key_env_label)
        form.addRow("API key status", self.api_key_status)
        form.addRow("API key", self.api_key)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.setObjectName("SettingsButtonBox")
        self.apply_button = self.buttons.button(QDialogButtonBox.Ok)
        if isinstance(self.apply_button, QPushButton):
            self.apply_button.setText("Apply")
            self.apply_button.setObjectName("SettingsApplyButton")
        self.cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        if isinstance(self.cancel_button, QPushButton):
            self.cancel_button.setObjectName("SettingsCancelButton")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def values(self) -> SettingsValues:
        return SettingsValues(
            workspace_root=self.workspace.text().strip(),
            config_path=self.config_path.text().strip(),
            agent_alias=self.agent_alias.text().strip(),
            model=self.model.text().strip(),
            max_steps=self.max_steps.value(),
            api_key_env=self._api_key_env,
            api_key=self.api_key.text().strip(),
        )


def _api_key_env_name(config: RunConfig) -> str:
    resolved = config.resolved_agent
    if resolved is not None and resolved.api_key_env:
        return resolved.api_key_env
    return DEFAULT_API_KEY_ENV


def _api_key_status_text(config: RunConfig, env_name: str) -> str:
    if config.api_key_source != "missing":
        return f"Configured ({config.api_key_source})"
    user_lora_root = config.user_lora_root or str(Path.home() / ".lora")
    if credential_is_configured(env_name, user_lora_root=user_lora_root):
        return "Configured (stored credential)"
    return "Not configured"
