from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import (
    QKeyEvent,
    QMouseEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)


MacroStep = dict[str, Any]


def _build_qt_key_map() -> dict[int, str]:
    mapping: dict[int, str] = {}

    def add(
        qt_name: str,
        evdev_name: str,
    ) -> None:
        qt_key = getattr(
            Qt.Key,
            qt_name,
            None,
        )

        if qt_key is not None:
            mapping[int(qt_key)] = evdev_name

    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        add(
            f"Key_{letter}",
            f"KEY_{letter}",
        )

    for digit in "0123456789":
        add(
            f"Key_{digit}",
            f"KEY_{digit}",
        )

    for number in range(1, 25):
        add(
            f"Key_F{number}",
            f"KEY_F{number}",
        )

    special_keys = {
        "Key_Escape": "KEY_ESC",
        "Key_Tab": "KEY_TAB",
        "Key_Backtab": "KEY_TAB",
        "Key_Backspace": "KEY_BACKSPACE",
        "Key_Return": "KEY_ENTER",
        "Key_Enter": "KEY_ENTER",
        "Key_Insert": "KEY_INSERT",
        "Key_Delete": "KEY_DELETE",
        "Key_Pause": "KEY_PAUSE",
        "Key_Print": "KEY_PRINT",
        "Key_Home": "KEY_HOME",
        "Key_End": "KEY_END",
        "Key_Left": "KEY_LEFT",
        "Key_Up": "KEY_UP",
        "Key_Right": "KEY_RIGHT",
        "Key_Down": "KEY_DOWN",
        "Key_PageUp": "KEY_PAGEUP",
        "Key_PageDown": "KEY_PAGEDOWN",
        "Key_Shift": "KEY_LEFTSHIFT",
        "Key_Control": "KEY_LEFTCTRL",
        "Key_Alt": "KEY_LEFTALT",
        "Key_AltGr": "KEY_RIGHTALT",
        "Key_Meta": "KEY_LEFTMETA",
        "Key_CapsLock": "KEY_CAPSLOCK",
        "Key_NumLock": "KEY_NUMLOCK",
        "Key_ScrollLock": "KEY_SCROLLLOCK",
        "Key_Space": "KEY_SPACE",
        "Key_Minus": "KEY_MINUS",
        "Key_Equal": "KEY_EQUAL",
        "Key_BracketLeft": "KEY_LEFTBRACE",
        "Key_BracketRight": "KEY_RIGHTBRACE",
        "Key_Backslash": "KEY_BACKSLASH",
        "Key_Semicolon": "KEY_SEMICOLON",
        "Key_Apostrophe": "KEY_APOSTROPHE",
        "Key_Comma": "KEY_COMMA",
        "Key_Period": "KEY_DOT",
        "Key_Slash": "KEY_SLASH",
        "Key_QuoteLeft": "KEY_GRAVE",
    }

    for qt_name, evdev_name in special_keys.items():
        add(qt_name, evdev_name)

    return mapping


QT_KEY_MAP = _build_qt_key_map()


def _keypad_key_name(
    event: QKeyEvent,
) -> str | None:
    modifiers = event.modifiers()

    if not (
        modifiers
        & Qt.KeyboardModifier.KeypadModifier
    ):
        return None

    key = int(event.key())

    for digit in "0123456789":
        qt_key = getattr(
            Qt.Key,
            f"Key_{digit}",
            None,
        )

        if qt_key is not None and key == int(qt_key):
            return f"KEY_KP{digit}"

    keypad_special = {
        "Key_Plus": "KEY_KPPLUS",
        "Key_Minus": "KEY_KPMINUS",
        "Key_Asterisk": "KEY_KPASTERISK",
        "Key_Slash": "KEY_KPSLASH",
        "Key_Period": "KEY_KPDOT",
        "Key_Return": "KEY_KPENTER",
        "Key_Enter": "KEY_KPENTER",
    }

    for qt_name, evdev_name in keypad_special.items():
        qt_key = getattr(
            Qt.Key,
            qt_name,
            None,
        )

        if qt_key is not None and key == int(qt_key):
            return evdev_name

    return None


def key_event_name(
    event: QKeyEvent,
) -> str | None:
    keypad_name = _keypad_key_name(event)

    if keypad_name is not None:
        return keypad_name

    return QT_KEY_MAP.get(
        int(event.key())
    )


def mouse_button_name(
    button: Qt.MouseButton,
) -> str | None:
    button_map = {
        Qt.MouseButton.LeftButton: "left",
        Qt.MouseButton.RightButton: "right",
        Qt.MouseButton.MiddleButton: "middle",
    }

    return button_map.get(button)


class MacroRecorderDialog(QDialog):
    """
    Records keyboard and mouse events inside the G13 editor.

    Timing between events is preserved as delay steps.
    """

    MINIMUM_DELAY_MS = 5

    def __init__(
        self,
        parent=None,
        record_delays: bool = True,
    ):
        super().__init__(parent)

        self.steps: list[MacroStep] = []

        self.record_delays = bool(
            record_delays
        )

        self._recording = True
        self._last_event_time: float | None = None

        self._held_keys: list[str] = []
        self._held_mouse_buttons: list[str] = []

        self.setWindowTitle(
            "Record G13 Macro"
        )

        self.setMinimumSize(
            540,
            440,
        )

        layout = QVBoxLayout(self)

        timing_text = (
            "Timing between events will be preserved."
            if self.record_delays
            else
            "Timing between events will not be recorded."
        )

        instructions = QLabel(
            "Type the desired keyboard sequence.\n\n"
            "Mouse buttons and wheel movement can also be "
            "recorded by using them over the main editor window. "
            "Clicks inside this recording window are ignored.\n\n"
            "Press MR again to finish the recording. "
            + timing_text
        )

        instructions.setWordWrap(True)

        layout.addWidget(instructions)

        self.steps_list = QListWidget()

        layout.addWidget(
            self.steps_list
        )

        application = QApplication.instance()

        if application is not None:
            application.installEventFilter(self)

    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:
        if not self._recording:
            return False

        event_type = event.type()

        # -----------------------------------------------------
        # Keyboard recording
        # -----------------------------------------------------

        if event_type in {
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        }:
            if not isinstance(event, QKeyEvent):
                return False

            if event.isAutoRepeat():
                return True

            key_name = key_event_name(event)

            if key_name is None:
                self.setWindowTitle(
                    "Record G13 Macro — Unsupported key ignored"
                )
                return True

            pressed = (
                event_type
                == QEvent.Type.KeyPress
            )

            self._record_key_event(
                key_name,
                pressed,
            )

            return True

        # Mouse actions on the recorder's own controls must
        # remain usable and must not become macro steps.
        if self._is_recorder_widget(watched):
            return False

        # -----------------------------------------------------
        # Mouse-button recording
        # -----------------------------------------------------

        if event_type in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        }:
            if not isinstance(event, QMouseEvent):
                return False

            button_name = mouse_button_name(
                event.button()
            )

            if button_name is None:
                return False

            pressed = event_type in {
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonDblClick,
            }

            self._record_mouse_button(
                button_name,
                pressed,
            )

            # Consume the click so it does not activate or alter
            # controls in the main editor during recording.
            return True

        # -----------------------------------------------------
        # Mouse-wheel recording
        # -----------------------------------------------------

        if event_type == QEvent.Type.Wheel:
            if not isinstance(event, QWheelEvent):
                return False

            return self._record_wheel_event(event)

        return False

    def _is_recorder_widget(
        self,
        watched,
    ) -> bool:
        if watched is self:
            return True

        if isinstance(watched, QWidget):
            return self.isAncestorOf(watched)

        return False

    def _record_key_event(
        self,
        key_name: str,
        pressed: bool,
    ) -> None:
        now = time.monotonic()

        if pressed:
            if key_name in self._held_keys:
                return
        else:
            if key_name not in self._held_keys:
                return

        self._append_delay(now)

        if pressed:
            self.steps.append(
                {
                    "type": "key_down",
                    "key": key_name,
                }
            )

            self._held_keys.append(
                key_name
            )

        else:
            self.steps.append(
                {
                    "type": "key_up",
                    "key": key_name,
                }
            )

            self._held_keys.remove(
                key_name
            )

        self._last_event_time = now
        self._refresh_steps()

    def _record_mouse_button(
        self,
        button_name: str,
        pressed: bool,
    ) -> None:
        now = time.monotonic()

        if pressed:
            if button_name in self._held_mouse_buttons:
                return
        else:
            if button_name not in self._held_mouse_buttons:
                return

        self._append_delay(now)

        if pressed:
            self.steps.append(
                {
                    "type": "mouse_down",
                    "button": button_name,
                }
            )

            self._held_mouse_buttons.append(
                button_name
            )

        else:
            self.steps.append(
                {
                    "type": "mouse_up",
                    "button": button_name,
                }
            )

            self._held_mouse_buttons.remove(
                button_name
            )

        self._last_event_time = now
        self._refresh_steps()

    def _record_wheel_event(
        self,
        event: QWheelEvent,
    ) -> bool:
        delta = event.angleDelta()

        horizontal_delta = delta.x()
        vertical_delta = delta.y()

        if (
            horizontal_delta == 0
            and vertical_delta == 0
        ):
            return False

        if (
            abs(vertical_delta)
            >= abs(horizontal_delta)
        ):
            direction = "vertical"
            raw_amount = vertical_delta
        else:
            direction = "horizontal"
            raw_amount = horizontal_delta

        amount = round(
            raw_amount / 120
        )

        # Some high-resolution wheels report values smaller than
        # 120. Preserve their direction as one wheel step.
        if amount == 0:
            amount = (
                1
                if raw_amount > 0
                else -1
            )

        now = time.monotonic()

        self._append_delay(now)

        self.steps.append(
            {
                "type": "wheel",
                "direction": direction,
                "amount": amount,
            }
        )

        self._last_event_time = now
        self._refresh_steps()

        # Prevent scrolling the editor while recording.
        return True

    def _append_delay(
        self,
        now: float,
    ) -> None:
        if not self.record_delays:
            return

        if self._last_event_time is None:
            return

        milliseconds = round(
            (
                now
                - self._last_event_time
            )
            * 1000
        )

        if milliseconds < self.MINIMUM_DELAY_MS:
            return

        self.steps.append(
            {
                "type": "delay",
                "milliseconds": milliseconds,
            }
        )

    def _refresh_steps(self) -> None:
        self.steps_list.clear()

        for index, step in enumerate(
            self.steps,
            start=1,
        ):
            step_type = step["type"]

            if step_type == "key_down":
                text = (
                    f"Key down: {step['key']}"
                )

            elif step_type == "key_up":
                text = (
                    f"Key up: {step['key']}"
                )

            elif step_type == "mouse_down":
                text = (
                    "Mouse down: "
                    f"{step['button']}"
                )

            elif step_type == "mouse_up":
                text = (
                    "Mouse up: "
                    f"{step['button']}"
                )

            elif step_type == "wheel":
                amount = step["amount"]

                text = (
                    f"Wheel {step['direction']}: "
                    f"{amount:+d}"
                )

            elif step_type == "delay":
                text = (
                    "Delay: "
                    f"{step['milliseconds']} ms"
                )

            else:
                text = str(step)

            self.steps_list.addItem(
                f"{index}. {text}"
            )

        self.steps_list.scrollToBottom()

    def finish_recording(self) -> None:
        if not self._recording:
            return

        # Ensure the generated macro cannot finish with any
        # keyboard key still held down.
        for key_name in reversed(
            self._held_keys
        ):
            self.steps.append(
                {
                    "type": "key_up",
                    "key": key_name,
                }
            )

        # Ensure no mouse button remains held.
        for button_name in reversed(
            self._held_mouse_buttons
        ):
            self.steps.append(
                {
                    "type": "mouse_up",
                    "button": button_name,
                }
            )

        self._held_keys.clear()
        self._held_mouse_buttons.clear()

        self._stop_capture()
        self.accept()

    def cancel_recording(self) -> None:
        self.steps.clear()

        self._held_keys.clear()
        self._held_mouse_buttons.clear()

        self._stop_capture()
        self.reject()

    def _stop_capture(self) -> None:
        if not self._recording:
            return

        self._recording = False

        application = QApplication.instance()

        if application is not None:
            application.removeEventFilter(
                self
            )

    def closeEvent(self, event) -> None:
        self._stop_capture()
        super().closeEvent(event)
