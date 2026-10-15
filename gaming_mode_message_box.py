"""Gamescope-safe QMessageBox replacement for the G13 editor.

Desktop Mode delegates to Qt's normal QMessageBox implementation. Gaming Mode
renders the message box inside the existing editor window so Gamescope does not
scale a small confirmation dialog as a separate surface.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QEventLoop, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog as _QtInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox as _QtMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from editor_theme import theme_stylesheet as _theme_stylesheet


def _gaming_window(widget: QWidget | None):
    if not isinstance(widget, QWidget):
        return None

    window = widget.window()

    if getattr(window, "_gaming_mode_presentation", None) is None:
        return None

    return window


class _InlineMessageOverlay(QFrame):
    def __init__(self, window: QWidget):
        super().__init__(window)

        self._window = window

        self.setObjectName("gamingModeMessageOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setGeometry(window.rect())

        window.installEventFilter(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch()

        self.panel = QFrame(self)
        self.panel.setObjectName("gamingModeMessagePanel")
        self.panel.setMinimumWidth(460)
        self.panel.setMaximumWidth(680)

        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(20, 18, 20, 18)
        self.panel_layout.setSpacing(14)

        root.addWidget(
            self.panel,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        root.addStretch()

        self.setStyleSheet(
            _theme_stylesheet("""
            QFrame#gamingModeMessageOverlay {
                background-color: $BG_GAMING_OVERLAY;
                border: none;
            }

            QFrame#gamingModeMessagePanel {
                background-color: $BG_TAB_PANE;
                border: 2px solid $BORDER_TEAL;
                border-radius: 8px;
            }

            QLabel#gamingModeMessageTitle {
                background-color: transparent;
                border: none;
                color: $TEXT_ACCENT;
                font-size: 16px;
                font-weight: bold;
            }

            QLabel#gamingModeMessageText {
                background-color: transparent;
                border: none;
                color: $TEXT_PRIMARY_COOL;
                font-size: 12px;
            }

            QLineEdit#gamingModeMessageInput {
                background-color: $BG_INPUT;
                border: 1px solid $BORDER_CONTROL;
                border-radius: 4px;
                color: $TEXT_BRIGHT;
                padding: 7px 8px;
                selection-background-color: $BG_ACCENT;
                selection-color: $TEXT_BRIGHT;
            }

            QLineEdit#gamingModeMessageInput:focus {
                border: 2px solid $ACCENT;
            }

            QPushButton#gamingModeMessageButton {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_BRIGHT;
                min-width: 86px;
                padding: 7px 12px;
            }

            QPushButton#gamingModeMessageButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
            }

            QPushButton#gamingModeMessageButton:pressed {
                background-color: $BG_ACCENT;
            }
            """)
        )

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self._window
            and event.type() == QEvent.Type.Resize
        ):
            self.setGeometry(
                self._window.rect()
            )

        return False

    def finish(self) -> None:
        try:
            self._window.removeEventFilter(
                self
            )
        except RuntimeError:
            pass

        self.hide()
        self.deleteLater()


def _title_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("gamingModeMessageTitle")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _text_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("gamingModeMessageText")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
    )
    return label


def _button(text: str, parent: QWidget) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName("gamingModeMessageButton")
    return button

class GamingModeInputDialog:
    """QInputDialog.getText-compatible input with a Gaming Mode renderer."""

    @classmethod
    def getText(
        cls,
        parent,
        title,
        label,
        echo=QLineEdit.EchoMode.Normal,
        text="",
    ):
        window = _gaming_window(parent)

        if window is None:
            return _QtInputDialog.getText(
                parent,
                title,
                label,
                echo,
                text,
            )

        existing = getattr(
            window,
            "_gaming_mode_message_overlay",
            None,
        )

        if existing is not None:
            try:
                existing.finish()
            except RuntimeError:
                pass

        overlay = _InlineMessageOverlay(window)
        window._gaming_mode_message_overlay = overlay

        if title:
            overlay.panel_layout.addWidget(
                _title_label(
                    title,
                    overlay.panel,
                )
            )

        overlay.panel_layout.addWidget(
            _text_label(
                label,
                overlay.panel,
            )
        )

        input_edit = QLineEdit(overlay.panel)
        input_edit.setObjectName(
            "gamingModeMessageInput"
        )
        input_edit.setEchoMode(echo)
        input_edit.setText(text)

        overlay.panel_layout.addWidget(
            input_edit
        )

        button_row = QHBoxLayout()
        button_row.addStretch()

        cancel_button = _button(
            "Cancel",
            overlay.panel,
        )
        ok_button = _button(
            "OK",
            overlay.panel,
        )

        button_row.addWidget(cancel_button)
        button_row.addWidget(ok_button)

        overlay.panel_layout.addLayout(
            button_row
        )

        loop = QEventLoop()

        result = {
            "done": False,
            "accepted": False,
            "text": text,
        }

        def finish(accepted: bool) -> None:
            if result["done"]:
                return

            result["done"] = True
            result["accepted"] = accepted
            result["text"] = input_edit.text()

            if (
                getattr(
                    window,
                    "_gaming_mode_message_overlay",
                    None,
                )
                is overlay
            ):
                window._gaming_mode_message_overlay = None

            overlay.finish()

            if loop.isRunning():
                loop.quit()

        cancel_button.clicked.connect(
            lambda: finish(False)
        )
        ok_button.clicked.connect(
            lambda: finish(True)
        )
        input_edit.returnPressed.connect(
            lambda: finish(True)
        )

        escape_shortcut = QShortcut(
            QKeySequence(
                Qt.Key.Key_Escape
            ),
            overlay,
        )

        escape_shortcut.activated.connect(
            lambda: finish(False)
        )

        overlay._escape_shortcut = (
            escape_shortcut
        )

        overlay.destroyed.connect(
            lambda: (
                loop.quit()
                if loop.isRunning()
                else None
            )
        )

        overlay.show()
        overlay.raise_()

        input_edit.setFocus()
        input_edit.selectAll()

        loop.exec()

        return (
            result["text"],
            result["accepted"],
        )

class GamingModeMessageBox(_QtMessageBox):
    """QMessageBox-compatible API with an in-window Gaming Mode renderer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._gaming_clicked_button = None

    @classmethod
    def _static_message(
        cls,
        icon,
        parent,
        title,
        text,
        buttons,
        default_button,
    ):
        if _gaming_window(parent) is None:
            method = {
                _QtMessageBox.Icon.Information: _QtMessageBox.information,
                _QtMessageBox.Icon.Warning: _QtMessageBox.warning,
                _QtMessageBox.Icon.Critical: _QtMessageBox.critical,
                _QtMessageBox.Icon.Question: _QtMessageBox.question,
            }[icon]

            return method(
                parent,
                title,
                text,
                buttons,
                default_button,
            )

        box = cls(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)

        if (
            default_button
            != _QtMessageBox.StandardButton.NoButton
        ):
            box.setDefaultButton(
                default_button
            )

        box.exec()

        clicked = box.clickedButton()

        if clicked is None:
            return _QtMessageBox.StandardButton.NoButton

        return box.standardButton(
            clicked
        )

    @classmethod
    def information(
        cls,
        parent,
        title,
        text,
        buttons=_QtMessageBox.StandardButton.Ok,
        defaultButton=_QtMessageBox.StandardButton.NoButton,
    ):
        return cls._static_message(
            _QtMessageBox.Icon.Information,
            parent,
            title,
            text,
            buttons,
            defaultButton,
        )

    @classmethod
    def warning(
        cls,
        parent,
        title,
        text,
        buttons=_QtMessageBox.StandardButton.Ok,
        defaultButton=_QtMessageBox.StandardButton.NoButton,
    ):
        return cls._static_message(
            _QtMessageBox.Icon.Warning,
            parent,
            title,
            text,
            buttons,
            defaultButton,
        )

    @classmethod
    def critical(
        cls,
        parent,
        title,
        text,
        buttons=_QtMessageBox.StandardButton.Ok,
        defaultButton=_QtMessageBox.StandardButton.NoButton,
    ):
        return cls._static_message(
            _QtMessageBox.Icon.Critical,
            parent,
            title,
            text,
            buttons,
            defaultButton,
        )

    @classmethod
    def question(
        cls,
        parent,
        title,
        text,
        buttons=(
            _QtMessageBox.StandardButton.Yes
            | _QtMessageBox.StandardButton.No
        ),
        defaultButton=_QtMessageBox.StandardButton.NoButton,
    ):
        return cls._static_message(
            _QtMessageBox.Icon.Question,
            parent,
            title,
            text,
            buttons,
            defaultButton,
        )

    def clickedButton(self):
        if self._gaming_clicked_button is not None:
            return self._gaming_clicked_button

        return super().clickedButton()

    def exec(self) -> int:
        parent = self.parentWidget()
        window = _gaming_window(parent)

        if window is None:
            return super().exec()

        if not self.buttons():
            self.setStandardButtons(
                _QtMessageBox.StandardButton.Ok
            )

        existing = getattr(
            window,
            "_gaming_mode_message_overlay",
            None,
        )

        if existing is not None:
            try:
                existing.finish()
            except RuntimeError:
                pass

        overlay = _InlineMessageOverlay(window)
        window._gaming_mode_message_overlay = overlay

        if self.windowTitle():
            overlay.panel_layout.addWidget(
                _title_label(
                    self.windowTitle(),
                    overlay.panel,
                )
            )

        text_parts = [
            part
            for part in (
                self.text(),
                self.informativeText(),
                self.detailedText(),
            )
            if part
        ]

        overlay.panel_layout.addWidget(
            _text_label(
                "\n\n".join(
                    text_parts
                ),
                overlay.panel,
            )
        )

        source_buttons = list(
            self.buttons()
        )

        button_row = QHBoxLayout()
        button_row.addStretch()

        default_source = self.defaultButton()
        escape_source = self.escapeButton()

        if escape_source is None:
            for source_button in source_buttons:
                role = self.buttonRole(
                    source_button
                )

                if role in {
                    _QtMessageBox.ButtonRole.RejectRole,
                    _QtMessageBox.ButtonRole.NoRole,
                }:
                    escape_source = source_button
                    break

        display_for_source = {}

        for source_button in source_buttons:
            display_button = _button(
                source_button.text().replace(
                    "&",
                    "",
                ),
                overlay.panel,
            )

            display_for_source[
                source_button
            ] = display_button

            button_row.addWidget(
                display_button
            )

        overlay.panel_layout.addLayout(
            button_row
        )

        loop = QEventLoop()
        result = {
            "button": None,
        }

        def finish(source_button) -> None:
            if result["button"] is not None:
                return

            result["button"] = source_button

            if (
                getattr(
                    window,
                    "_gaming_mode_message_overlay",
                    None,
                )
                is overlay
            ):
                window._gaming_mode_message_overlay = None

            overlay.finish()

            if loop.isRunning():
                loop.quit()

        for source_button, display_button in (
            display_for_source.items()
        ):
            display_button.clicked.connect(
                lambda _checked=False, button=source_button:
                finish(button)
            )

        if escape_source is not None:
            escape_shortcut = QShortcut(
                QKeySequence(
                    Qt.Key.Key_Escape
                ),
                overlay,
            )

            escape_shortcut.activated.connect(
                lambda button=escape_source:
                finish(button)
            )

            overlay._escape_shortcut = (
                escape_shortcut
            )

        overlay.destroyed.connect(
            lambda: (
                loop.quit()
                if loop.isRunning()
                else None
            )
        )

        overlay.show()
        overlay.raise_()

        focus_button = (
            display_for_source.get(
                default_source
            )
        )

        if focus_button is None and display_for_source:
            focus_button = next(
                iter(
                    display_for_source.values()
                )
            )

        if focus_button is not None:
            focus_button.setFocus()

        loop.exec()

        chosen = result["button"]

        if chosen is None:
            chosen = (
                escape_source
                or default_source
            )

        self._gaming_clicked_button = chosen

        if chosen is None:
            return 0

        standard = self.standardButton(
            chosen
        )

        if (
            standard
            == _QtMessageBox.StandardButton.NoButton
        ):
            return 0

        return int(
            standard.value
        )
