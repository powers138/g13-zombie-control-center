from __future__ import annotations

import json
import os
from pathlib import Path
from string import Template

from app_paths import SETTINGS_FILE
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor


# =========================================================
# Editor themes
# =========================================================
#
# Text, warning, delete, disabled, live-input, and profile-
# status colors are intentionally shared by every theme.
# The Blue and Green themes change only structural editor
# chrome such as surfaces, controls, borders, tabs, menus,
# selections, and hover states.

EDITOR_THEME_OPTIONS: tuple[tuple[str, str], ...] = (
    ("classic", "Classic"),
    ("blue", "Blue"),
    ("green", "Green"),
)

EDITOR_THEME_LABELS = dict(
    EDITOR_THEME_OPTIONS
)

VALID_EDITOR_THEME_NAMES = frozenset(
    EDITOR_THEME_LABELS
)

SETTINGS_PATH = SETTINGS_FILE


CLASSIC_EDITOR_THEME: dict[str, str] = {
    # Main surfaces
    "BLACK": "#000000",
    "BG_WINDOW": "#202226",
    "BG_SHELL": "#071b20",
    "BG_PAGE": "#08171b",
    "BG_DEVICE_PANEL": "#09171b",
    "BG_TAB_PANE": "#0b1d22",
    "BG_GROUP": "#0d2228",
    "BG_ICON": "#091b20",
    "BG_MENU": "#1b2328",
    "BG_MENU_BUTTON": "#0b2026",
    "BG_COMMAND_MENU_BUTTON": "#25292f",
    "BG_COMMAND_MENU_HOVER": "#17343d",
    "BG_SCROLLBAR": "#25282e",
    "BG_GAMING_SHELL": "#101416",
    "BG_GAMING_LIST": "#071417",
    "BG_GAMING_OVERLAY": "rgba(0, 0, 0, 165)",

    # Standard controls and interaction states
    "BG_TEAL_CONTROL": "#102a31",
    "BG_TEAL_HOVER": "#17404a",
    "BG_ACCENT": "#0b5362",
    "BG_INPUT": "#2a2d33",
    "BG_BUTTON": "#343840",
    "BG_BUTTON_HOVER": "#414650",
    "BG_BUTTON_PRESSED": "#424750",
    "BG_CONTROL_SUBTLE": "#2f333a",
    "BG_CONTROL_HOVER": "#383d45",
    "BG_STEPPER": "#353941",
    "BG_STEPPER_HOVER": "#454a54",
    "BG_STEPPER_PRESSED": "#505661",
    "BG_TAB_DEFAULT": "#30343b",
    "BG_TAB_INACTIVE": "#202b30",

    # Disabled states
    "BG_DISABLED_GROUP": "#11191d",
    "BG_DISABLED_CONTROL": "#202329",
    "BG_CHECKBOX_DISABLED": "#252b2f",

    # Status and warning states
    "BG_LIVE": "#30a9de",
    "BG_LOCKED": "#9c66b2",
    "BG_WARNING": "#6a431c",
    "BG_WARNING_HOVER": "#7a4d20",
    "BG_DELETE": "#5a2028",

    # Borders and accents
    "BORDER_DEFAULT": "#494d55",
    "BORDER_CONTROL": "#555b66",
    "BORDER_PAGE": "#24434a",
    "BORDER_SHELL": "#145766",
    "BORDER_DEVICE": "#1d5966",
    "BORDER_TEAL": "#28505a",
    "BORDER_TEAL_STRONG": "#397480",
    "BORDER_TAB": "#46545a",
    "BORDER_NAV_BUTTON": "#244c56",
    "BORDER_NAV_HOVER": "#31a9c2",
    "BORDER_NEUTRAL_HOVER": "#707886",
    "BORDER_NEUTRAL_PRESSED": "#7d8694",
    "BORDER_DISABLED_GROUP": "#39474c",
    "BORDER_DISABLED_CONTROL": "#3b4048",
    "BORDER_CHECKBOX": "#858e95",
    "BORDER_CHECKBOX_HOVER": "#b4bdc3",
    "BORDER_CHECKBOX_DISABLED": "#4b5358",
    "BORDER_LIVE": "#8fddff",
    "BORDER_LOCKED": "#e6c84f",
    "BORDER_LOCKED_HOVER": "#f2dc6b",
    "BORDER_DELETE": "#ff5a6a",
    "ACCENT": "#39c9e5",
    "ACCENT_HOVER": "#70e4f7",
    "ACCENT_LIGHT": "#8eeeff",
    "WARNING": "#dd7a24",
    "WARNING_HOVER": "#f3a45e",

    # Text -- shared by every theme
    "TEXT_PRIMARY": "#e8e8e8",
    "TEXT_PRIMARY_COOL": "#e8eeee",
    "TEXT_BRIGHT": "#ffffff",
    "TEXT_PROFILE": "#f0f4f5",
    "TEXT_FLOATING": "#f2f4f5",
    "TEXT_HEADER_BUTTON": "#d5e4e7",
    "TEXT_CONTROL": "#d8dde5",
    "TEXT_ASSIGNMENT": "#d7e0e2",
    "TEXT_JOYSTICK_DIRECTION": "#c7cdd0",
    "TEXT_COMPACT_MENU": "#b8c7ca",
    "TEXT_NAV": "#b7c7ca",
    "TEXT_TAB": "#9aa8ad",
    "TEXT_ACCENT": "#5ec7dc",
    "TEXT_ACCENT_LIGHT": "#8fdce9",
    "TEXT_HELP": "#82aeb7",
    "TEXT_HELP_JOYSTICK": "#7f9ba2",
    "TEXT_STEPPER": "#8f9aa3",
    "TEXT_DISABLED_HEADER": "#60777d",
    "TEXT_DISABLED_MENU": "#626b70",
    "TEXT_DISABLED_STEPPER": "#666d76",
    "TEXT_DISABLED": "#68747a",
    "TEXT_DISABLED_BUTTON": "#747b84",
    "TEXT_ON_ACCENT": "#111111",
    "TEXT_ON_LIVE": "#101214",

    # Separators and overlays
    "BG_SEPARATOR": "#3b4a50",
    "OVERLAY_CONTROL_HOVER": "rgba(57, 201, 229, 35)",
    "OVERLAY_CONTROL_HOVER_BORDER": "rgba(57, 201, 229, 140)",
    "OVERLAY_CONTROL_SELECTED": "rgba(57, 201, 229, 65)",
    "OVERLAY_CONTROL_LIVE": "rgba(48, 169, 222, 120)",
    "FLOATING_BINDING_BG": "rgba(76, 80, 84, 205)",
    "FLOATING_BINDING_BORDER": "rgba(210, 218, 220, 150)",

    # Backlight editor chrome
    "BACKLIGHT_LENS_OUTLINE": "#65777c",
}


BLUE_EDITOR_THEME: dict[str, str] = {
    **CLASSIC_EDITOR_THEME,

    # Palette foundation:
    # dark   #0A1128
    # medium #001F54
    # light  #034078
    # bright #FEFAE0 (shared from the Green palette)
    "BG_WINDOW": "#0A1128",
    "BG_SHELL": "#0A1128",
    "BG_PAGE": "#0A1128",
    "BG_DEVICE_PANEL": "#09132D",
    "BG_TAB_PANE": "#081433",
    "BG_GROUP": "#071535",
    "BG_ICON": "#09132D",
    "BG_MENU": "#081430",
    "BG_MENU_BUTTON": "#071535",
    "BG_COMMAND_MENU_BUTTON": "#001F54",
    "BG_COMMAND_MENU_HOVER": "#034078",
    "BG_SCROLLBAR": "#071535",
    "BG_GAMING_SHELL": "#0A1128",
    "BG_GAMING_LIST": "#081430",

    "BG_TEAL_CONTROL": "#001F54",
    "BG_TEAL_HOVER": "#034078",
    "BG_ACCENT": "#034078",
    "BG_INPUT": "#05183E",
    "BG_BUTTON": "#001F54",
    "BG_BUTTON_HOVER": "#034078",
    "BG_BUTTON_PRESSED": "#023066",
    "BG_CONTROL_SUBTLE": "#01275D",
    "BG_CONTROL_HOVER": "#034078",
    "BG_STEPPER": "#001F54",
    "BG_STEPPER_HOVER": "#034078",
    "BG_STEPPER_PRESSED": "#023066",
    "BG_TAB_DEFAULT": "#001F54",
    "BG_TAB_INACTIVE": "#081433",

    "BORDER_DEFAULT": "#034078",
    "BORDER_CONTROL": "#034078",
    "BORDER_PAGE": "#034078",
    "BORDER_SHELL": "#034078",
    "BORDER_DEVICE": "#034078",
    "BORDER_TEAL": "#034078",
    "BORDER_TEAL_STRONG": "#034078",
    "BORDER_TAB": "#034078",
    "BORDER_NAV_BUTTON": "#034078",
    "BORDER_NAV_HOVER": "#FEFAE0",
    "BORDER_NEUTRAL_HOVER": "#FEFAE0",
    "BORDER_NEUTRAL_PRESSED": "#FEFAE0",
    "ACCENT": "#4D8CCF",
    "ACCENT_HOVER": "#6FA6DF",
    "ACCENT_LIGHT": "#91BCEB",

    "BG_SEPARATOR": "#034078",
    "OVERLAY_CONTROL_HOVER": "rgba(3, 64, 120, 35)",
    "OVERLAY_CONTROL_HOVER_BORDER": "rgba(254, 250, 224, 140)",
    "OVERLAY_CONTROL_SELECTED": "rgba(3, 64, 120, 65)",
    "BACKLIGHT_LENS_OUTLINE": "#034078",
}


GREEN_EDITOR_THEME: dict[str, str] = {
    **CLASSIC_EDITOR_THEME,

    # Palette foundation:
    # dark   #283618
    # medium #606C38
    # light  #FEFAE0
    "BG_WINDOW": "#283618",
    "BG_SHELL": "#283618",
    "BG_PAGE": "#283618",
    "BG_DEVICE_PANEL": "#2F3C1C",
    "BG_TAB_PANE": "#364420",
    "BG_GROUP": "#394622",
    "BG_ICON": "#2F3C1C",
    "BG_MENU": "#32401E",
    "BG_MENU_BUTTON": "#394622",
    "BG_COMMAND_MENU_BUTTON": "#606C38",
    "BG_COMMAND_MENU_HOVER": "#737D4C",
    "BG_SCROLLBAR": "#394622",
    "BG_GAMING_SHELL": "#283618",
    "BG_GAMING_LIST": "#32401E",

    "BG_TEAL_CONTROL": "#606C38",
    "BG_TEAL_HOVER": "#737D4C",
    "BG_ACCENT": "#606C38",
    "BG_INPUT": "#3E4C25",
    "BG_BUTTON": "#445128",
    "BG_BUTTON_HOVER": "#606C38",
    "BG_BUTTON_PRESSED": "#525E30",
    "BG_CONTROL_SUBTLE": "#4A562B",
    "BG_CONTROL_HOVER": "#606C38",
    "BG_STEPPER": "#445128",
    "BG_STEPPER_HOVER": "#606C38",
    "BG_STEPPER_PRESSED": "#525E30",
    "BG_TAB_DEFAULT": "#606C38",
    "BG_TAB_INACTIVE": "#364420",

    "BORDER_DEFAULT": "#606C38",
    "BORDER_CONTROL": "#606C38",
    "BORDER_PAGE": "#606C38",
    "BORDER_SHELL": "#606C38",
    "BORDER_DEVICE": "#606C38",
    "BORDER_TEAL": "#606C38",
    "BORDER_TEAL_STRONG": "#737D4C",
    "BORDER_TAB": "#606C38",
    "BORDER_NAV_BUTTON": "#606C38",
    "BORDER_NAV_HOVER": "#FEFAE0",
    "BORDER_NEUTRAL_HOVER": "#FEFAE0",
    "BORDER_NEUTRAL_PRESSED": "#FEFAE0",
    "ACCENT": "#A3B45E",
    "ACCENT_HOVER": "#B8C878",
    "ACCENT_LIGHT": "#CDD995",

    "BG_SEPARATOR": "#606C38",
    "OVERLAY_CONTROL_HOVER": "rgba(96, 108, 56, 35)",
    "OVERLAY_CONTROL_HOVER_BORDER": "rgba(254, 250, 224, 140)",
    "OVERLAY_CONTROL_SELECTED": "rgba(96, 108, 56, 65)",
    "BACKLIGHT_LENS_OUTLINE": "#606C38",
}


EDITOR_THEMES: dict[str, dict[str, str]] = {
    "classic": CLASSIC_EDITOR_THEME,
    "blue": BLUE_EDITOR_THEME,
    "green": GREEN_EDITOR_THEME,
}


def normalize_editor_theme_name(
    value,
) -> str:
    if (
        isinstance(value, str)
        and value in VALID_EDITOR_THEME_NAMES
    ):
        return value

    return "classic"


def load_editor_theme_name(
    settings_path: Path = SETTINGS_PATH,
) -> str:
    try:
        raw_settings = json.loads(
            settings_path.read_text(
                encoding="utf-8"
            )
        )

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        return "classic"

    if not isinstance(raw_settings, dict):
        return "classic"

    return normalize_editor_theme_name(
        raw_settings.get(
            "editor_theme",
            "classic",
        )
    )


def save_editor_theme_name(
    theme_name: str,
    settings_path: Path = SETTINGS_PATH,
) -> None:
    if theme_name not in VALID_EDITOR_THEME_NAMES:
        raise ValueError(
            "Editor theme must be classic, blue, or green."
        )

    try:
        settings = json.loads(
            settings_path.read_text(
                encoding="utf-8"
            )
        )

    except FileNotFoundError:
        settings = {}

    if not isinstance(settings, dict):
        raise ValueError(
            "settings.json must contain a JSON object."
        )

    settings["editor_theme"] = theme_name

    settings_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = settings_path.with_name(
        f".{settings_path.name}.theme.tmp"
    )

    try:
        temporary_path.write_text(
            json.dumps(
                settings,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        os.replace(
            temporary_path,
            settings_path,
        )

    finally:
        temporary_path.unlink(
            missing_ok=True
        )


ACTIVE_EDITOR_THEME_NAME = (
    load_editor_theme_name()
)

EDITOR_THEME = EDITOR_THEMES[
    ACTIVE_EDITOR_THEME_NAME
]


def theme_stylesheet(
    stylesheet: str,
) -> str:
    return Template(
        stylesheet
    ).substitute(
        EDITOR_THEME
    )


def theme_color(
    name: str,
) -> QColor:
    return QColor(
        EDITOR_THEME[name]
    )


def apply_gaming_mode_shell_theme(
    window,
) -> None:
    shell = window.centralWidget()

    if (
        shell is None
        or shell.objectName()
        != "gamingModeShell"
    ):
        return

    shell.setStyleSheet(
        theme_stylesheet(
            """
            QWidget#gamingModeShell {
                background-color: $BG_GAMING_SHELL;
            }

            QWidget#gamingModeBar {
                background-color: $BG_SHELL;
                border-bottom: 1px solid $BORDER_SHELL;
            }

            QLabel#gamingModeHelp {
                background-color: transparent;
                border: none;
                color: $TEXT_HEADER_BUTTON;
            }

            QPushButton#gamingModeClose {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_BRIGHT;
                padding: 5px 12px;
            }

            QPushButton#gamingModeClose:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
            }

            QScrollArea#gamingModeEditorScroll {
                border: none;
            }
            """
        )
    )


def _apply_gaming_mode_inline_overlay_theme(
    overlay,
) -> None:
    overlay.setStyleSheet(
        theme_stylesheet(
            """
            QFrame#gamingModeInlineOverlay {
                background-color: $BG_GAMING_OVERLAY;
                border: none;
            }

            QFrame#gamingModeInlinePanel {
                background-color: $BG_TAB_PANE;
                border: 2px solid $BORDER_TEAL;
                border-radius: 8px;
            }

            QLabel#gamingModeInlineTitle {
                background-color: transparent;
                border: none;
                color: $TEXT_ACCENT;
                font-size: 16px;
                font-weight: bold;
            }

            QLabel#gamingModeInlineText {
                background-color: transparent;
                border: none;
                color: $TEXT_PRIMARY_COOL;
                font-size: 12px;
            }

            QListWidget#gamingModeInlineList {
                background-color: $BG_GAMING_LIST;
                border: 1px solid $BORDER_TEAL;
                color: $TEXT_PRIMARY_COOL;
                outline: none;
                padding: 2px;
            }

            QListWidget#gamingModeInlineList::item {
                min-height: 30px;
                padding: 4px 8px;
            }

            QListWidget#gamingModeInlineList::item:selected {
                background-color: $BG_ACCENT;
                color: $TEXT_BRIGHT;
            }

            QListWidget#gamingModeInlineList QScrollBar:vertical {
                background-color: $BG_GAMING_LIST;
                width: 8px;
                margin: 0;
            }

            QListWidget#gamingModeInlineList QScrollBar::handle:vertical {
                background-color: $BORDER_TEAL;
                border-radius: 4px;
                min-height: 24px;
            }

            QListWidget#gamingModeInlineList QScrollBar::add-line:vertical,
            QListWidget#gamingModeInlineList QScrollBar::sub-line:vertical {
                height: 0;
            }

            QPushButton#gamingModeInlineButton {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_BRIGHT;
                min-width: 86px;
                padding: 7px 12px;
            }

            QPushButton#gamingModeInlineButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
            }

            QPushButton#gamingModeInlineButton:pressed {
                background-color: $BG_ACCENT;
            }
            """
        )
    )


def _apply_gaming_mode_combo_popup_theme(
    popup,
) -> None:
    popup.setStyleSheet(
        theme_stylesheet(
            """
            QFrame#gamingModeComboPopup {
                background-color: $BG_INPUT;
                border: 1px solid $BORDER_CONTROL;
                border-radius: 4px;
            }

            QListWidget#gamingModeComboList {
                background-color: $BG_INPUT;
                border: none;
                color: $TEXT_PRIMARY_COOL;
                outline: none;
                padding: 0px;
            }

            QListWidget#gamingModeComboList::item {
                padding: 5px;
            }

            QListWidget#gamingModeComboList::item:hover {
                background-color: $BG_BUTTON_HOVER;
                color: $TEXT_BRIGHT;
            }

            QListWidget#gamingModeComboList::item:selected {
                background-color: $BG_BUTTON_HOVER;
                color: $TEXT_BRIGHT;
            }

            QListWidget#gamingModeComboList QScrollBar:vertical {
                background-color: $BG_SCROLLBAR;
                width: 8px;
                margin: 0px;
            }

            QListWidget#gamingModeComboList QScrollBar::handle:vertical {
                background-color: $BORDER_CONTROL;
                border-radius: 3px;
                min-height: 20px;
            }

            QListWidget#gamingModeComboList QScrollBar::add-line:vertical,
            QListWidget#gamingModeComboList QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )
    )


class _EditorThemeEventFilter(QObject):
    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:
        if event.type() != QEvent.Type.Show:
            return False

        object_name = watched.objectName()

        if object_name == "gamingModeShell":
            apply_gaming_mode_shell_theme(
                watched.window()
            )

        elif object_name == "gamingModeInlineOverlay":
            _apply_gaming_mode_inline_overlay_theme(
                watched
            )

        elif object_name == "gamingModeComboPopup":
            _apply_gaming_mode_combo_popup_theme(
                watched
            )

        elif (
            watched.metaObject().className()
            == "QComboBoxPrivateContainer"
        ):
            watched.setStyleSheet(
                theme_stylesheet(
                    """
                    QComboBoxPrivateContainer {
                        background-color: $BG_INPUT;
                    }
                    """
                )
            )

        return False


def install_editor_theme_event_filter(
    application,
) -> None:
    if getattr(
        application,
        "_editor_theme_event_filter",
        None,
    ) is not None:
        return

    theme_filter = _EditorThemeEventFilter(
        application
    )

    application.installEventFilter(
        theme_filter
    )

    application._editor_theme_event_filter = (
        theme_filter
    )
