from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)


class ScriptEditorDialog(QDialog):
    def __init__(
        self,
        profile_name: str,
        script: dict,
        parent=None,
    ):
        super().__init__(parent)

        self.setWindowTitle(
            f"Lua Scripting — {profile_name}"
        )

        self.setMinimumSize(
            820,
            620,
        )

        layout = QVBoxLayout(
            self
        )

        description = QLabel(
            "Profile-specific Lua script. "
            "Execution support will be added separately."
        )

        description.setWordWrap(
            True
        )

        layout.addWidget(
            description
        )

        self.enabled_check = QCheckBox(
            "Enable Lua script for this profile"
        )

        self.enabled_check.setChecked(
            bool(
                script.get(
                    "enabled",
                    False,
                )
            )
        )

        layout.addWidget(
            self.enabled_check
        )

        self.editor = QPlainTextEdit()

        self.editor.setPlainText(
            str(
                script.get(
                    "source",
                    "",
                )
            )
        )

        self.editor.setPlaceholderText(
            "function OnEvent(event, arg, family)\n"
            "    -- Logitech-compatible Lua script\n"
            "end"
        )

        layout.addWidget(
            self.editor,
            1,
        )

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        self.button_box.accepted.connect(
            self.accept
        )

        self.button_box.rejected.connect(
            self.reject
        )

        layout.addWidget(
            self.button_box
        )

    def script_data(
        self,
    ) -> dict:
        return {
            "language": "lua",
            "enabled": (
                self.enabled_check.isChecked()
            ),
            "source": (
                self.editor
                .toPlainText()
            ),
        }
