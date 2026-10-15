"""Optional Gamescope presentation for the existing editor.

Only UI presentation is changed. This module does not import the editor, access
profiles/hardware, or start/stop the driver. Desktop sessions use window.show().
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, qVersion
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gaming_mode_message_box import GamingModeMessageBox


def is_gamescope_session(environment: Mapping[str, str] | None = None) -> bool:
    """Identify Gaming Mode, not merely Steam running on a normal desktop."""
    if environment is None:
        environment = os.environ

    desktops = {
        part.strip().casefold()
        for part in environment.get("XDG_CURRENT_DESKTOP", "").split(":")
    }
    session = environment.get("DESKTOP_SESSION", "").strip().casefold()
    return "gamescope" in desktops or session in {
        "gamescope", "gamescope-session", "gamescope-session-plus",
    }


def _rect_text(rect) -> str:
    return f"{rect.width()}x{rect.height()} at {rect.x()},{rect.y()}"


class _GamingModePresentation(QObject):
    """Keep the same QMainWindow, closeEvent and working editor widgets."""

    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self.window = window
        self._help_source = None
        self._last_geometry = None
        self._default_help = "G13 Zombie Control Center — Gaming Mode"

        content = window.centralWidget()
        if content is None:
            raise RuntimeError("Build the editor interface before showing it.")

        # Keep the content's own layout requirements, but do not copy the
        # outer window's 900-pixel minimum height into the scroll area.
        content_minimum = content.minimumSize().expandedTo(
            content.minimumSizeHint()
        )
        content_minimum.setWidth(
            max(content_minimum.width(), window.minimumWidth())
        )
        content = window.takeCentralWidget()
        content.setMinimumSize(content_minimum)

        shell = QWidget(window)
        shell.setObjectName("gamingModeShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.bar = QWidget(shell)
        self.bar.setObjectName("gamingModeBar")
        bar_layout = QHBoxLayout(self.bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)
        bar_layout.setSpacing(12)

        self.help_label = QLabel(self._default_help, self.bar)
        self.help_label.setObjectName("gamingModeHelp")
        self.help_label.setTextFormat(Qt.TextFormat.PlainText)
        self.help_label.setWordWrap(True)
        # Tooltip text must not change the bar/window minimum width or height.
        self.help_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        bar_layout.addWidget(self.help_label, 1)

        self.close_button = QPushButton("Close Editor", self.bar)
        self.close_button.setObjectName("gamingModeClose")
        self.close_button.setMinimumHeight(34)
        self.close_button.clicked.connect(self._request_close)
        bar_layout.addWidget(self.close_button)
        self.bar.setFixedHeight(
            max(52, self.help_label.fontMetrics().lineSpacing() * 3 + 12)
        )
        shell_layout.addWidget(self.bar)

        self.scroll = QScrollArea(shell)
        self.scroll.setObjectName("gamingModeEditorScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizeAdjustPolicy(
            QScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setWidget(content)
        shell_layout.addWidget(self.scroll, 1)

        shell.setStyleSheet(
            """
            QWidget#gamingModeShell { background-color: #101416; }
            QWidget#gamingModeBar {
                background-color: #071b20;
                border-bottom: 1px solid #145766;
            }
            QLabel#gamingModeHelp {
                background-color: transparent;
                border: none;
                color: #d5e4e7;
            }
            QPushButton#gamingModeClose {
                background-color: #102a31;
                border: 1px solid #28505a;
                border-radius: 4px;
                color: #ffffff;
                padding: 5px 12px;
            }
            QPushButton#gamingModeClose:hover {
                background-color: #17404a;
                border-color: #39c9e5;
            }
            QScrollArea#gamingModeEditorScroll { border: none; }
            """
        )

        window.setCentralWidget(shell)
        window.setMinimumSize(QSize(0, 0))

        # Gaming Mode keeps existing rules operational. Only their setup
        # buttons are disabled here; no settings or profile data are changed.
        for attribute in (
            "pa_detect_app_button",
            "pa_scan_steam_button",
            "pa_assign_app_button",
            "pa_remove_rule_button",
        ):
            button = getattr(
                window,
                attribute,
                None,
            )

            if isinstance(
                button,
                QPushButton,
            ):
                button.setEnabled(
                    False
                )

                button.setStyleSheet(
                    button.styleSheet()
                    + """
                    QPushButton:disabled {
                        background-color: #1a1d20;
                        border: 1px solid #343a3d;
                        color: #687276;
                    }
                    """
                )

                button.setToolTip(
                    "Configure application assignments "
                    "in Desktop Mode."
                )

        commands_list = getattr(
            window,
            "commands_list",
            None,
        )

        if commands_list is not None:
            palette = commands_list.palette()

            for color_group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
            ):
                palette.setColor(
                    color_group,
                    QPalette.ColorRole.Highlight,
                    QColor("#569fcc"),
                )

                palette.setColor(
                    color_group,
                    QPalette.ColorRole.Base,
                    QColor("#151c23"),
                )

            commands_list.setPalette(
                palette
            )

            commands_list.viewport().setPalette(
                palette
            )

        # Move only the painted M1/M2/M3 tabs downward in
        # Gaming Mode. Keep the tab bar and pane geometry
        # unchanged.
        layer_tabs = getattr(
            window,
            "layer_tabs",
            None,
        )

        if layer_tabs is not None:
            tab_bar = layer_tabs.tabBar()
            tab_bar.setDrawBase(
                False
            )

            tab_bar.setStyleSheet(
                """
                QTabBar::tab {
                    margin-top: 5px;
                    margin-bottom: -5px;
                }
                """
            )

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(750)
        self._geometry_timer.timeout.connect(self._log_geometry)

        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

    def _owns(self, watched) -> bool:
        if not isinstance(watched, QWidget):
            return False

        # Follow ownership across child dialog/window boundaries too.
        widget = watched

        while widget is not None:
            if widget is self.window:
                return True

            widget = widget.parentWidget()

        return False

    def _tooltip_text(self, watched: QWidget, event) -> str:
        # Item-view tooltips can be stored in the model rather than the widget.
        parent = watched.parentWidget()
        if isinstance(parent, QAbstractItemView) and watched is parent.viewport():
            index = parent.indexAt(event.pos())
            if index.isValid():
                text = index.data(Qt.ItemDataRole.ToolTipRole)
                if isinstance(text, str) and text:
                    return text

        widget = watched
        while widget is not None:
            if widget.toolTip():
                return widget.toolTip()
            if widget.isWindow():
                break
            widget = widget.parentWidget()
        return ""

    def _clear_help(self) -> None:
        self._help_source = None
        self.help_label.setText(self._default_help)

    def _position_combo_popup(
        self,
        popup,
        combo,
    ) -> None:
        if not popup.isVisible():
            return

        position = combo.mapToGlobal(
            combo.rect().bottomLeft()
        )

        position.setY(
            position.y() + 1
        )

        popup.move(
            position
        )


    def eventFilter(self, watched, event) -> bool:
        event_type = event.type()

        # Gamescope renders the native QComboBox popup
        # container/scroller background as white even
        # though the actual list view uses the correct
        # dark application palette.
        if (
            event_type == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.metaObject().className()
            == "QComboBoxPrivateContainer"
        ):
            watched.setStyleSheet(
                """
                QComboBoxPrivateContainer {
                    background-color: #0b1d22;
                }

                QComboBoxPrivateScroller {
                    background-color: #0b1d22;
                    border: none;
                }
                """
            )

            combo = watched.parentWidget()

            if combo is not None:
                QTimer.singleShot(
                    0,
                    lambda popup=watched, source=combo:
                    self._position_combo_popup(
                        popup,
                        source,
                    ),
                )

        if not self._owns(watched):
            return False

        if event_type == QEvent.Type.ToolTip:
            # No QToolTip window is created: help stays in the fixed top bar.
            text = self._tooltip_text(watched, event)
            self._help_source = watched if text else None
            self.help_label.setText(text or self._default_help)

            # This help request was handled. Prevent parent propagation.
            event.accept()
            return True

        if event_type in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.Close,
        } or (
            watched is self._help_source
            and event_type in {QEvent.Type.Leave, QEvent.Type.Hide}
        ):
            self._clear_help()

        if watched is self.window:
            if event_type in {
                QEvent.Type.Show,
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.WindowStateChange,
            }:
                self._geometry_timer.start()
            elif event_type in {QEvent.Type.Hide, QEvent.Type.Close}:
                self._geometry_timer.stop()

        # Do not consume normal input, dialogs, close events or drag/drop.
        return False

    def _request_close(
        self,
        checked: bool = False,
    ) -> None:
        self.window.close()

    def _log_geometry(self) -> None:
        if not self.window.isVisible():
            return
        screen = self.window.screen()
        if screen is None:
            return
        handle = self.window.windowHandle()
        ratio = handle.devicePixelRatio() if handle else self.window.devicePixelRatioF()
        report = (
            f"Qt={qVersion()} platform={QApplication.platformName()}; "
            f"fullscreen={self.window.isFullScreen()}; "
            f"window={_rect_text(self.window.geometry())} logical; "
            f"screen={_rect_text(screen.geometry())} logical; "
            f"window_DPR={ratio:g}; screen_DPR={screen.devicePixelRatio():g}; "
            f"logical_DPI={screen.logicalDotsPerInch():g}; "
            f"QT_SCALE_FACTOR={os.environ.get('QT_SCALE_FACTOR', '<unset>')}; "
            f"QT_SCREEN_SCALE_FACTORS={os.environ.get('QT_SCREEN_SCALE_FACTORS', '<unset>')}; "
            f"QT_FONT_DPI={os.environ.get('QT_FONT_DPI', '<unset>')}"
        )
        if report != self._last_geometry:
            self._last_geometry = report
            print("[Editor display] " + report, flush=True)


def show_editor(window: QMainWindow) -> None:
    """Show the unmodified desktop UI or attach Gaming Mode presentation once."""
    if not is_gamescope_session():
        window.show()
        return

    if not hasattr(window, "_gaming_mode_presentation"):
        window._gaming_mode_presentation = _GamingModePresentation(window)

      # Request a regular, bounded window. Gamescope still controls how that
    # window is ultimately composited onto the physical display.
    screen = window.screen() or QApplication.primaryScreen()

    if screen is not None:
        available = screen.availableGeometry()

        # Keep Gaming Mode close to the normal desktop editor size.
        # Screen dimensions are only a maximum for smaller displays;
        # larger monitors do not make the editor grow.
        target_size = QSize(
            min(
                1120,
                max(
                    1,
                    available.width() * 90 // 100,
                ),
            ),
            min(
                1120,
                max(
                    1,
                    available.height() * 90 // 100,
                ),
            ),
        )

    else:
        # Fallback only when Qt cannot identify a screen.
        target_size = QSize(1120, 800)

    window.resize(target_size)

    if screen is not None:
        window.move(
            available.x() + (available.width() - window.width()) // 2,
            available.y() + (available.height() - window.height()) // 2,
        )

    print("[Editor display] Gamescope windowed presentation enabled.", flush=True)
    window.showNormal()
