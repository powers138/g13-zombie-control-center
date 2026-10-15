"""Offscreen tests for Gaming Mode-safe message boxes.

No editor import, project data, sockets, Steam, or HID access.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox as QtMessageBox,
    QPushButton,
    QWidget,
)

import gaming_mode_message_box as controls


APP = QApplication.instance() or QApplication(["test-gaming-mode-message-box"])
APP.setQuitOnLastWindowClosed(False)


class GamingModeMessageBoxTests(unittest.TestCase):
    def setUp(self):
        self.window = QMainWindow()
        self.window.resize(900, 700)
        self.window.setCentralWidget(QWidget())
        self.window._gaming_mode_presentation = object()
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        overlay = getattr(
            self.window,
            "_gaming_mode_message_overlay",
            None,
        )

        if overlay is not None:
            overlay.finish()

        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(
            None,
            QEvent.Type.DeferredDelete,
        )
        APP.processEvents()

    def overlay(self):
        overlay = getattr(
            self.window,
            "_gaming_mode_message_overlay",
            None,
        )

        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.isVisible())
        self.assertFalse(overlay.isWindow())
        return overlay

    @staticmethod
    def button_by_text(parent, text):
        for button in parent.findChildren(QPushButton):
            if button.text() == text:
                return button

        raise AssertionError(
            f"Button not found: {text}"
        )

    def test_question_returns_selected_standard_button_inline(self):
        before = {
            widget
            for widget in APP.topLevelWidgets()
            if widget.isVisible()
        }

        def choose_yes():
            overlay = self.overlay()
            self.button_by_text(
                overlay,
                "Yes",
            ).click()

        QTimer.singleShot(
            0,
            choose_yes,
        )

        result = controls.GamingModeMessageBox.question(
            self.window,
            "Question",
            "Continue?",
            (
                QtMessageBox.StandardButton.Yes
                | QtMessageBox.StandardButton.No
            ),
            QtMessageBox.StandardButton.No,
        )

        after = {
            widget
            for widget in APP.topLevelWidgets()
            if widget.isVisible()
        }

        self.assertEqual(
            result,
            QtMessageBox.StandardButton.Yes,
        )
        self.assertEqual(
            after,
            before,
        )

    def test_information_returns_ok_inline(self):
        def choose_ok():
            overlay = self.overlay()
            self.button_by_text(
                overlay,
                "OK",
            ).click()

        QTimer.singleShot(
            0,
            choose_ok,
        )

        result = controls.GamingModeMessageBox.information(
            self.window,
            "Information",
            "Saved.",
        )

        self.assertEqual(
            result,
            QtMessageBox.StandardButton.Ok,
        )

    def test_custom_message_box_preserves_clicked_button_identity(self):
        box = controls.GamingModeMessageBox(
            self.window
        )
        box.setWindowTitle(
            "Conflict"
        )
        box.setText(
            "Move this item?"
        )

        cancel = box.addButton(
            "Cancel",
            QtMessageBox.ButtonRole.RejectRole,
        )

        move = box.addButton(
            "Move",
            QtMessageBox.ButtonRole.AcceptRole,
        )

        box.setDefaultButton(
            cancel
        )
        box.setEscapeButton(
            cancel
        )

        def choose_move():
            overlay = self.overlay()
            self.button_by_text(
                overlay,
                "Move",
            ).click()

        QTimer.singleShot(
            0,
            choose_move,
        )

        box.exec()

        self.assertIs(
            box.clickedButton(),
            move,
        )

    def test_desktop_message_box_path_delegates_to_qt(self):
        delattr(
            self.window,
            "_gaming_mode_presentation",
        )

        with patch.object(
            controls._QtMessageBox,
            "warning",
            return_value=QtMessageBox.StandardButton.Cancel,
        ) as original:
            result = controls.GamingModeMessageBox.warning(
                self.window,
                "Desktop",
                "Normal Qt path",
                (
                    QtMessageBox.StandardButton.Ok
                    | QtMessageBox.StandardButton.Cancel
                ),
                QtMessageBox.StandardButton.Cancel,
            )

        self.assertEqual(
            result,
            QtMessageBox.StandardButton.Cancel,
        )

        original.assert_called_once()


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
