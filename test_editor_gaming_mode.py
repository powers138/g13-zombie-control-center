"""Offscreen Qt tests. No editor import, project data, sockets or HID access.

Run with the project's .venv Python. The only editor code executed is its actual
closeEvent(), attached to a small test window with mocked timers/connections.
Windowed revision: checks bounded normal-window geometry and Gaming Mode-only
setup-button restrictions. It does not test the actual Gamescope compositor.
"""
from __future__ import annotations

import ast
import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QLabel, QListWidget, QMainWindow,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

import editor_gaming_mode as gaming

PROJECT = Path(__file__).resolve().parent
APP = QApplication.instance() or QApplication(["test-editor-gaming-mode"])
APP.setQuitOnLastWindowClosed(False)


def close_handler_window_class():
    tree = ast.parse((PROJECT / "editor.py").read_text(encoding="utf-8"))
    original = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "G13Editor")
    close = next(node for node in original.body
                 if isinstance(node, ast.FunctionDef) and node.name == "closeEvent")
    # No original constructor, project import, or project-level expression runs.
    shell = ast.ClassDef(name="G13Editor", bases=[ast.Name(id="QMainWindow", ctx=ast.Load())],
                         keywords=[], body=[close], decorator_list=[])
    module = ast.Module(body=[shell], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "QMainWindow": QMainWindow,
        "QMessageBox": QMessageBox,
    }
    exec(compile(module, "<isolated editor.closeEvent>", "exec"), namespace)
    return namespace["G13Editor"]


class SessionTests(unittest.TestCase):
    def test_gamescope_desktop_is_detected(self):
        self.assertTrue(gaming.is_gamescope_session({"XDG_CURRENT_DESKTOP": "gamescope"}))

    def test_case_and_desktop_list_are_supported(self):
        self.assertTrue(gaming.is_gamescope_session({"XDG_CURRENT_DESKTOP": "other: GameScope "}))

    def test_gamescope_session_fallback_is_detected(self):
        self.assertTrue(gaming.is_gamescope_session({"DESKTOP_SESSION": "gamescope-session"}))

    def test_steam_on_kde_is_not_gaming_mode(self):
        self.assertFalse(gaming.is_gamescope_session({
            "XDG_CURRENT_DESKTOP": "KDE", "DESKTOP_SESSION": "plasma",
            "DISPLAY": ":1", "SteamAppId": "1234567890",
        }))

    def test_empty_environment_is_not_gaming_mode(self):
        self.assertFalse(gaming.is_gamescope_session({}))


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "XDG_CURRENT_DESKTOP": "gamescope", "DESKTOP_SESSION": "gamescope-session",
        })
        self.environment.start()
        self.windows = []
        self.window = self.new_window()
        self.window.setMinimumSize(1080, 900)
        self.window.resize(1120, 900)
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        self.detect = QPushButton("Detect Active App", self.content)
        self.detect.setToolTip("Click, then focus the application. Check occurs after 3 seconds.")
        layout.addWidget(self.detect)
        self.child = QLabel("child", self.detect)
        self.child.setToolTip("")
        self.list = QListWidget(self.content)
        self.list.addItem("Profile")
        self.list.item(0).setToolTip("A profile item's tooltip")
        layout.addWidget(self.list)
        self.window.setCentralWidget(self.content)
        self.window.pa_detect_app_button = self.detect
        self.setup_buttons = [self.detect]
        for attribute, text in (
            ("pa_scan_steam_button", "Scan Steam Games"),
            ("pa_assign_app_button", "Assign Detected App to Editing Profile"),
            ("pa_remove_rule_button", "Remove Selected Rule"),
        ):
            button = QPushButton(text, self.content)
            setattr(self.window, attribute, button)
            layout.addWidget(button)
            self.setup_buttons.append(button)
        self.setup_callbacks = []
        for button in self.setup_buttons:
            callback = Mock()
            button.clicked.connect(callback)
            self.setup_callbacks.append(callback)
        self.window.pa_automatic_switching_check = QCheckBox("Automatic switching", self.content)
        self.window.pa_lock_while_running_check = QCheckBox("Lock while running", self.content)
        for checkbox in (self.window.pa_automatic_switching_check,
                         self.window.pa_lock_while_running_check):
            checkbox.setChecked(True)
            layout.addWidget(checkbox)
        self.other_button = QPushButton("Unrelated editor action", self.content)
        layout.addWidget(self.other_button)
        self.window.pa_rules_list = self.list

    def new_window(self):
        cls = close_handler_window_class()
        window = cls()
        window.active_macro_recorder = None
        window.event_timer = Mock()
        window.driver_connection_timer = Mock()
        window.event_listener = Mock()
        window.command_publisher = Mock()
        window.profile_dirty = False
        window.joystick_settings_dirty = False
        self.windows.append(window)
        return window

    def gaming_show(self):
        with contextlib.redirect_stdout(io.StringIO()):
            gaming.show_editor(self.window)
        APP.processEvents()
        return self.window._gaming_mode_presentation

    def help_event(self, widget, point=QPoint(2, 2)):
        return QHelpEvent(QEvent.Type.ToolTip, point, widget.mapToGlobal(point))

    def tearDown(self):
        for window in self.windows:
            presentation = getattr(window, "_gaming_mode_presentation", None)
            if presentation is not None:
                presentation._geometry_timer.stop()
                APP.removeEventFilter(presentation)
            # Tests may intentionally leave a dirty editor open.
            # Clear those test states before closing the window
            # so teardown cannot open a real confirmation dialog.
            window.profile_dirty = False
            window.joystick_settings_dirty = False

            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.environment.stop()

    def test_desktop_keeps_original_central_widget_and_minimum(self):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "DESKTOP_SESSION": "plasma"}):
            gaming.show_editor(self.window)
        APP.processEvents()
        self.assertIs(self.window.centralWidget(), self.content)
        self.assertFalse(hasattr(self.window, "_gaming_mode_presentation"))
        self.assertFalse(self.window.isFullScreen())
        self.assertEqual(self.window.minimumHeight(), 900)

    def test_gaming_uses_normal_window_with_original_widgets(self):
        presentation = self.gaming_show()
        self.assertFalse(self.window.isFullScreen())
        self.assertFalse(self.window.isMaximized())
        self.assertIs(presentation.scroll.widget(), self.content)
        self.assertIs(self.detect.window(), self.window)
        self.assertTrue(presentation.close_button.isVisible())

    def test_small_window_scrolls_but_close_remains_visible(self):
        # Explicitly oversized contents still need scrolling; the old outer
        # window minimum must not be the reason scrolling is required.
        self.content.setMinimumSize(1080, 950)
        presentation = self.gaming_show()
        self.window.showNormal()
        self.window.resize(800, 600)
        APP.processEvents()
        self.assertEqual(self.window.width(), 800)
        self.assertEqual(self.window.height(), 600)
        self.assertGreater(presentation.scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(presentation.scroll.verticalScrollBar().maximum(), 0)
        button = presentation.close_button
        top_left = button.mapTo(self.window, QPoint())
        self.assertTrue(self.window.rect().contains(top_left))
        self.assertTrue(self.window.rect().contains(top_left + QPoint(button.width()-1, button.height()-1)))

    def test_tooltip_text_stays_in_bar_without_new_window_or_move(self):
        presentation = self.gaming_show()
        before_geometry = self.window.geometry()
        before_bar_height = presentation.bar.height()
        before_windows = set(APP.topLevelWidgets())
        QApplication.sendEvent(self.detect, self.help_event(self.detect))
        APP.processEvents()
        self.assertEqual(presentation.help_label.text(), self.detect.toolTip())
        self.assertEqual(self.window.geometry(), before_geometry)
        self.assertEqual(presentation.bar.height(), before_bar_height)
        self.assertEqual(set(APP.topLevelWidgets()), before_windows)
        self.assertFalse(presentation.help_label.isWindow())

    def test_long_hover_help_does_not_change_geometry(self):
        presentation = self.gaming_show()
        before = self.window.geometry()
        self.detect.setToolTip("Long help text. " * 200)
        QApplication.sendEvent(self.detect, self.help_event(self.detect))
        APP.processEvents()
        self.assertEqual(self.window.geometry(), before)
        self.assertEqual(presentation.help_label.text(), self.detect.toolTip())

    def test_parent_tooltip_is_found(self):
        presentation = self.gaming_show()
        self.assertEqual(presentation._tooltip_text(self.child, self.help_event(self.child)),
                         self.detect.toolTip())

    def test_list_item_tooltip_is_found(self):
        presentation = self.gaming_show()
        point = self.list.visualItemRect(self.list.item(0)).center()
        viewport = self.list.viewport()
        event = self.help_event(viewport, point)
        self.assertEqual(presentation._tooltip_text(viewport, event), "A profile item's tooltip")

    def test_leaving_hover_source_clears_help(self):
        presentation = self.gaming_show()
        QApplication.sendEvent(self.detect, self.help_event(self.detect))
        presentation.eventFilter(self.detect, QEvent(QEvent.Type.Leave))
        self.assertEqual(presentation.help_label.text(), presentation._default_help)

    def test_owned_dialogs_get_help_but_unrelated_windows_are_untouched(self):
        presentation = self.gaming_show()
        dialog = QDialog(self.window)
        button = QPushButton("Dialog", dialog)
        button.setToolTip("Dialog help")
        self.assertTrue(presentation.eventFilter(button, self.help_event(button)))
        other = self.new_window()
        other_button = QPushButton("Other", other)
        other_button.setToolTip("Other help")
        self.assertFalse(presentation.eventFilter(other_button, self.help_event(other_button)))
        self.assertEqual(presentation.help_label.text(), "Dialog help")

    def test_normal_input_and_drop_events_are_not_consumed(self):
        presentation = self.gaming_show()
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.DragEnter,
                     QEvent.Type.DragMove, QEvent.Type.Drop):
            self.assertFalse(presentation.eventFilter(self.detect, QEvent(kind)))

    def test_close_button_reaches_actual_editor_cleanup(self):
        presentation = self.gaming_show()
        recorder = Mock()
        self.window.active_macro_recorder = recorder
        presentation.close_button.click()
        self.assertFalse(self.window.isVisible())
        recorder.cancel_recording.assert_called_once()
        self.window.event_timer.stop.assert_called_once()
        self.window.driver_connection_timer.stop.assert_called_once()
        self.window.event_listener.close.assert_called_once()
        self.window.command_publisher.close.assert_called_once()

    def test_cancel_with_unsaved_profile_keeps_editor_open(self):
        presentation = self.gaming_show()
        self.window.profile_dirty = True

        editor_message_box = (
            type(self.window)
            .closeEvent
            .__globals__["QMessageBox"]
        )

        with patch.object(
            editor_message_box,
            "warning",
            return_value=(
                QMessageBox.StandardButton.Cancel
            ),
        ):
            presentation.close_button.click()

        self.assertTrue(
            self.window.isVisible()
        )

        self.window.event_listener.close.assert_not_called()

    def test_unsaved_joystick_discard_runs_normal_close(self):
        presentation = self.gaming_show()
        self.window.joystick_settings_dirty = True

        editor_message_box = (
            type(self.window)
            .closeEvent
            .__globals__["QMessageBox"]
        )

        with patch.object(
            editor_message_box,
            "warning",
            return_value=(
                QMessageBox.StandardButton.Discard
            ),
        ):
            presentation.close_button.click()

        self.assertFalse(
            self.window.isVisible()
        )

        self.window.command_publisher.close.assert_called_once()

    def test_configuration_is_not_applied_twice(self):
        presentation = self.gaming_show()
        self.gaming_show()
        self.assertIs(presentation, self.window._gaming_mode_presentation)
        self.assertIs(presentation.scroll.widget(), self.content)

    def test_window_size_is_bounded_by_virtual_screen(self):
        self.gaming_show()
        available = self.window.screen().availableGeometry()

        expected_width = min(
            1120,
            max(
                1,
                available.width() * 90 // 100,
            ),
        )

        expected_height = min(
            1120,
            max(
                1,
                available.height() * 90 // 100,
            ),
        )

        self.assertEqual(
            self.window.width(),
            expected_width,
        )

        self.assertEqual(
            self.window.height(),
            expected_height,
        )

    def test_outer_minimum_height_is_not_imposed_on_content(self):
        original_minimum = max(self.content.minimumHeight(), self.content.minimumSizeHint().height())
        self.assertLess(original_minimum, 900, "The fixture must be shorter than the old outer minimum.")
        presentation = self.gaming_show()
        self.assertLess(presentation.scroll.widget().minimumHeight(), 900)
        self.assertGreaterEqual(presentation.scroll.widget().minimumWidth(), 1080)

    def test_all_application_setup_buttons_are_disabled_in_gaming(self):
        self.gaming_show()
        for button, callback in zip(self.setup_buttons, self.setup_callbacks):
            self.assertFalse(button.isEnabled(), button.text())
            self.assertTrue(button.isVisible(), button.text())
            self.assertIn("Desktop Mode", button.toolTip())
            button.click()
            callback.assert_not_called()

    def test_automatic_and_lock_checkboxes_remain_unchanged(self):
        automatic = self.window.pa_automatic_switching_check
        lock = self.window.pa_lock_while_running_check
        automatic_changed = Mock()
        lock_changed = Mock()
        automatic.toggled.connect(automatic_changed)
        lock.toggled.connect(lock_changed)
        self.gaming_show()
        self.assertTrue(automatic.isEnabled())
        self.assertTrue(lock.isEnabled())
        self.assertTrue(automatic.isChecked())
        self.assertTrue(lock.isChecked())
        automatic_changed.assert_not_called()
        lock_changed.assert_not_called()

    def test_existing_disabled_lock_state_is_preserved(self):
        self.window.pa_automatic_switching_check.setChecked(False)
        self.window.pa_lock_while_running_check.setEnabled(False)
        self.gaming_show()
        self.assertFalse(self.window.pa_automatic_switching_check.isChecked())
        self.assertFalse(self.window.pa_lock_while_running_check.isEnabled())

    def test_rule_list_and_other_controls_are_untouched(self):
        original = [self.list.item(row).text() for row in range(self.list.count())]
        self.gaming_show()
        self.assertEqual([self.list.item(row).text() for row in range(self.list.count())], original)
        self.assertTrue(self.list.isEnabled())
        self.assertTrue(self.other_button.isEnabled())

    def test_desktop_keeps_application_setup_buttons_enabled(self):
        original_tooltips = [button.toolTip() for button in self.setup_buttons]
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "DESKTOP_SESSION": "plasma"}):
            gaming.show_editor(self.window)
        for button, callback, tooltip in zip(self.setup_buttons, self.setup_callbacks, original_tooltips):
            self.assertTrue(button.isEnabled())
            self.assertEqual(button.toolTip(), tooltip)
            button.click()
            callback.assert_called_once()

    def test_show_without_application_setup_attributes_is_supported(self):
        for attribute in ("pa_detect_app_button", "pa_scan_steam_button",
                          "pa_assign_app_button", "pa_remove_rule_button"):
            delattr(self.window, attribute)
        presentation = self.gaming_show()
        self.assertTrue(presentation.close_button.isVisible())

    def test_geometry_diagnostics_are_deduplicated(self):
        presentation = self.gaming_show()
        presentation._geometry_timer.stop()
        presentation._last_geometry = None
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            presentation._log_geometry()
            presentation._log_geometry()
        self.assertEqual(output.getvalue().count("[Editor display]"), 1)
        self.assertIn("window_DPR=", output.getvalue())
        self.assertIn("screen=", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
