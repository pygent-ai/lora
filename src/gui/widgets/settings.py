from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lora.schema import RunConfig


@dataclass(frozen=True, slots=True)
class SettingsValues:
    workspace_root: str
    config_path: str
    agent_alias: str
    model: str
    max_steps: int


class SettingsDialog(QDialog):
    def __init__(self, config: RunConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Lora Settings")
        self.setObjectName("SettingsDialog")
        self.setModal(True)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.workspace = QLineEdit(config.workspace_root)
        self.config_path = QLineEdit("")
        self.agent_alias = QLineEdit(config.agent_alias)
        self.model = QLineEdit(config.model or config.model_name or "")
        self.max_steps = QSpinBox()
        self.max_steps.setRange(-1, 100000)
        self.max_steps.setValue(config.max_steps)

        form.addRow("Workspace", self.workspace)
        form.addRow("Config path", self.config_path)
        form.addRow("Agent alias", self.agent_alias)
        form.addRow("Model override", self.model)
        form.addRow("Max steps", self.max_steps)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        apply_button = buttons.button(QDialogButtonBox.Ok)
        if isinstance(apply_button, QPushButton):
            apply_button.setText("Apply")
            apply_button.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> SettingsValues:
        return SettingsValues(
            workspace_root=self.workspace.text().strip(),
            config_path=self.config_path.text().strip(),
            agent_alias=self.agent_alias.text().strip(),
            model=self.model.text().strip(),
            max_steps=self.max_steps.value(),
        )
