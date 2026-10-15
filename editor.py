from __future__ import annotations

import sys
import copy
import errno
import fcntl
import os
import stat
from color_picker import HueWheel
from event_bus import (
    G13CommandPublisher,
    G13EventListener,
)
from macro_recorder import MacroRecorderDialog
from pathlib import Path
from typing import Any


from evdev import ecodes as e
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Property,
    QSize,
    Qt,
    QTimer,
    qVersion,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QIcon,
    QLinearGradient,
    QRadialGradient,
    QPainter,
    QPen,
    QPolygon,
    QRegion,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsOpacityEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProxyStyle,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from active_window import get_active_window
from app_version import APP_VERSION
from diagnostics import write_diagnostic_report
from editor_theme import (
    ACTIVE_EDITOR_THEME_NAME,
    EDITOR_THEME,
    EDITOR_THEME_LABELS,
    EDITOR_THEME_OPTIONS,
    apply_gaming_mode_shell_theme,
    install_editor_theme_event_filter,
    normalize_editor_theme_name,
    save_editor_theme_name,
    theme_color as _theme_color,
    theme_stylesheet as _theme_stylesheet,
)
from editor_gaming_mode import show_editor
from gaming_mode_message_box import (
    GamingModeInputDialog,
    GamingModeMessageBox,
)

QMessageBox = GamingModeMessageBox

from steam_games import (
    scan_steam_games,
)
from export_logitech_profile import export_profile_xml
from import_logitech_profile import (
    convert_profile,
    convert_profile_xml,
)

from profile_manager import ProfileManager
from profiles_to_go import (
    BANK_SIZE,
    add_logitech_onboard_profile,
    add_zombie_onboard_profile,
    convert_onboard_to_logitech,
    convert_onboard_to_zombie,
    get_cached_onboard_status,
    get_onboard_profile,
    get_onboard_profiles,
    get_onboard_status,
    remove_onboard_profile,
    write_onboard_cache,
)
from script_editor import ScriptEditorDialog


KEY_NAMES = sorted({
    name
    for name, code in vars(e).items()
    if (
        name.startswith("KEY_")
        and isinstance(code, int)
        and name not in {
            "KEY_MAX",
            "KEY_CNT",
        }
    )
})


MODIFIER_NAMES = (
    "KEY_LEFTCTRL",
    "KEY_LEFTSHIFT",
    "KEY_LEFTALT",
    "KEY_LEFTMETA",
    "KEY_RIGHTCTRL",
    "KEY_RIGHTSHIFT",
    "KEY_RIGHTALT",
    "KEY_RIGHTMETA",
)

KEY_CODE_TO_NAME: dict[int, str] = {}

for key_name in KEY_NAMES:
    key_code = getattr(
        e,
        key_name,
        None,
    )

    if isinstance(key_code, int):
        KEY_CODE_TO_NAME.setdefault(
            key_code,
            key_name,
        )


KEY_DISPLAY_NAMES = {
    "KEY_LEFTCTRL": "Ctrl",
    "KEY_RIGHTCTRL": "Right Ctrl",
    "KEY_LEFTSHIFT": "Shift",
    "KEY_RIGHTSHIFT": "Right Shift",
    "KEY_LEFTALT": "Alt",
    "KEY_RIGHTALT": "Right Alt",
    "KEY_LEFTMETA": "Meta",
    "KEY_RIGHTMETA": "Right Meta",
    "KEY_LEFT": "Left",
    "KEY_RIGHT": "Right",
    "KEY_UP": "Up",
    "KEY_DOWN": "Down",
    "KEY_ENTER": "Enter",
    "KEY_ESC": "Esc",
    "KEY_SPACE": "Space",
    "KEY_TAB": "Tab",
    "KEY_BACKSPACE": "Backspace",
    "KEY_DELETE": "Delete",
    "KEY_HOME": "Home",
    "KEY_END": "End",
    "KEY_PAGEUP": "Page Up",
    "KEY_PAGEDOWN": "Page Down",
}

_DESKTOP_ICON_CACHE: dict[
    str,
    str | None,
] = {}

_ICON_FILE_CACHE: dict[
    str,
    str | None,
] = {}

_STEAM_ICON_CACHE: dict[
    str,
    str | None,
] = {}


def _profile_search_icon_file() -> str | None:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").casefold()

    if "gnome" in desktop:
        themes = ("Adwaita", "AdwaitaLegacy")
    elif "kde" in desktop or "plasma" in desktop:
        themes = ("breeze", "breeze-dark")
    elif "xfce" in desktop:
        themes = ("Mint-Y", "Mint-X", "Adwaita")
    else:
        themes = ()

    roots = (
        Path("/run/host/usr/share/icons"),
        Path("/run/host/usr/local/share/icons"),
        Path("/usr/share/icons"),
        Path("/usr/local/share/icons"),
    )

    names = (
        "system-search-symbolic.svg",
        "edit-find-symbolic.svg",
        "system-search.svg",
        "edit-find.svg",
        "system-search.png",
        "edit-find.png",
    )

    for root in roots:
        if not root.is_dir():
            continue

        search_roots = tuple(root / theme for theme in themes) + (root,)

        for search_root in search_roots:
            if not search_root.is_dir():
                continue

            for name in names:
                try:
                    match = next(
                        (
                            candidate
                            for candidate in search_root.rglob(name)
                            if candidate.is_file()
                        ),
                        None,
                    )
                except OSError:
                    continue

                if match is not None:
                    return str(match)

    return None


def _profile_search_icon() -> QIcon:
    icon_path = _profile_search_icon_file()
    pixmap = QPixmap(icon_path) if icon_path else QPixmap()

    if pixmap.isNull():
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(_theme_color("TEXT_ACCENT"), 2))
        painter.drawEllipse(QRectF(2, 2, 8, 8))
        painter.drawLine(QPointF(9, 9), QPointF(14, 14))
        painter.end()

        return QIcon(pixmap)

    pixmap = pixmap.scaled(
        16,
        16,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    painter = QPainter(pixmap)
    painter.setCompositionMode(
        QPainter.CompositionMode.CompositionMode_SourceIn
    )
    painter.fillRect(pixmap.rect(), _theme_color("TEXT_ACCENT"))
    painter.end()

    return QIcon(pixmap)

def _desktop_application_directories(
) -> list[Path]:
    directories: list[Path] = []

    data_home = os.environ.get(
        "XDG_DATA_HOME"
    )

    if data_home:
        directories.append(
            Path(data_home)
            / "applications"
        )

    else:
        directories.append(
            Path.home()
            / ".local"
            / "share"
            / "applications"
        )

    data_dirs = os.environ.get(
        "XDG_DATA_DIRS",
        "/usr/local/share:/usr/share",
    )

    for data_dir in data_dirs.split(":"):
        data_dir = data_dir.strip()

        if not data_dir:
            continue

        directories.append(
            Path(data_dir)
            / "applications"
        )

    directories.extend(
        [
            Path(
                "/var/lib/flatpak/exports/"
                "share/applications"
            ),
            (
                Path.home()
                / ".local"
                / "share"
                / "flatpak"
                / "exports"
                / "share"
                / "applications"
            ),

            # Host application data when Zombie is running
            # inside a container, such as Bazzite's Podman
            # environment.
            Path(
                "/run/host/usr/local/"
                "share/applications"
            ),
            Path(
                "/run/host/usr/"
                "share/applications"
            ),
            Path(
                "/run/host/var/lib/flatpak/"
                "exports/share/applications"
            ),
        ]
    )

    unique_directories: list[Path] = []
    seen: set[str] = set()

    for directory in directories:
        directory_key = str(
            directory
        )

        if directory_key in seen:
            continue

        seen.add(
            directory_key
        )

        unique_directories.append(
            directory
        )

    return unique_directories


def _read_desktop_entry_fields(
    path: Path,
) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_desktop_entry = False

    try:
        with path.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as handle:
            for raw_line in handle:
                line = raw_line.strip()

                if not line:
                    continue

                if line.startswith("["):
                    if line == "[Desktop Entry]":
                        in_desktop_entry = True
                        continue

                    if in_desktop_entry:
                        break

                    continue

                if (
                    not in_desktop_entry
                    or "=" not in line
                ):
                    continue

                key, value = line.split(
                    "=",
                    1,
                )

                key = key.strip()
                value = value.strip()

                if key in {
                    "Icon",
                    "StartupWMClass",
                }:
                    fields[key] = value

    except OSError:
        return {}

    return fields


def _desktop_icon_value_for_window_class(
    window_class: str,
) -> str | None:
    normalized_class = (
        window_class.strip().casefold()
    )

    if not normalized_class:
        return None

    if normalized_class in _DESKTOP_ICON_CACHE:
        return _DESKTOP_ICON_CACHE[
            normalized_class
        ]

    directories = (
        _desktop_application_directories()
    )

    # First try the cleanest Linux mapping:
    #
    # org.kde.konsole
    # -> org.kde.konsole.desktop
    #
    # org.mozilla.firefox
    # -> org.mozilla.firefox.desktop
    exact_filename = (
        f"{window_class.strip()}.desktop"
    )

    for directory in directories:
        desktop_path = (
            directory
            / exact_filename
        )

        if not desktop_path.exists():
            continue

        fields = (
            _read_desktop_entry_fields(
                desktop_path
            )
        )

        icon_value = fields.get(
            "Icon"
        )

        if icon_value:
            _DESKTOP_ICON_CACHE[
                normalized_class
            ] = icon_value

            return icon_value

    # Some applications do not use their full window
    # class as the desktop-file name. Fall back to
    # StartupWMClass matching.
    short_class = (
        normalized_class.rsplit(
            ".",
            1,
        )[-1]
    )

    for directory in directories:
        if not directory.is_dir():
            continue

        try:
            desktop_files = list(
                directory.glob(
                    "*.desktop"
                )
            )

        except OSError:
            continue

        for desktop_path in desktop_files:
            fields = (
                _read_desktop_entry_fields(
                    desktop_path
                )
            )

            startup_class = fields.get(
                "StartupWMClass",
                "",
            ).strip().casefold()

            if startup_class not in {
                normalized_class,
                short_class,
            }:
                continue

            icon_value = fields.get(
                "Icon"
            )

            if not icon_value:
                continue

            _DESKTOP_ICON_CACHE[
                normalized_class
            ] = icon_value

            return icon_value

    _DESKTOP_ICON_CACHE[
        normalized_class
    ] = None

    return None

def _icon_file_for_name(
    icon_name: str,
) -> str | None:
    normalized_name = (
        icon_name.strip()
    )

    if not normalized_name:
        return None

    cached_path = _ICON_FILE_CACHE.get(
        normalized_name
    )

    if cached_path is not None:
        return cached_path

    icon_path = Path(
        normalized_name
    )

    if (
        icon_path.is_absolute()
        and icon_path.is_file()
    ):
        resolved_path = str(
            icon_path
        )

        _ICON_FILE_CACHE[
            normalized_name
        ] = resolved_path

        return resolved_path

    icon_directories = [
        (
            Path.home()
            / ".local"
            / "share"
            / "icons"
        ),
        (
            Path.home()
            / ".icons"
        ),
        Path(
            "/usr/local/share/icons"
        ),
        Path(
            "/usr/share/icons"
        ),
        Path(
            "/var/lib/flatpak/exports/"
            "share/icons"
        ),
        (
            Path.home()
            / ".local"
            / "share"
            / "flatpak"
            / "exports"
            / "share"
            / "icons"
        ),

        # Host icon directories when Zombie is running
        # inside a container.
        Path(
            "/run/host/usr/local/share/icons"
        ),
        Path(
            "/run/host/usr/share/icons"
        ),
        Path(
            "/run/host/var/lib/flatpak/"
            "exports/share/icons"
        ),
    ]

    search_locations = [
        (
            "hicolor",
            "scalable",
            "apps",
        ),
        (
            "hicolor",
            "512x512",
            "apps",
        ),
        (
            "hicolor",
            "256x256",
            "apps",
        ),
        (
            "hicolor",
            "128x128",
            "apps",
        ),
        (
            "hicolor",
            "96x96",
            "apps",
        ),
        (
            "hicolor",
            "64x64",
            "apps",
        ),
        (
            "hicolor",
            "48x48",
            "apps",
        ),
        (
            "hicolor",
            "32x32",
            "apps",
        ),
        (
            "hicolor",
            "24x24",
            "apps",
        ),
        (
            "hicolor",
            "22x22",
            "apps",
        ),
        (
            "hicolor",
            "16x16",
            "apps",
        ),
    ]

    suffixes = (
        ".svg",
        ".svgz",
        ".png",
        ".xpm",
    )

    supplied_suffix = (
        Path(normalized_name).suffix
    )

    if supplied_suffix:
        filenames = [
            normalized_name
        ]

    else:
        filenames = [
            f"{normalized_name}{suffix}"
            for suffix in suffixes
        ]

    for icon_directory in icon_directories:
        if not icon_directory.is_dir():
            continue

        for location in search_locations:
            location_path = (
                icon_directory.joinpath(
                    *location
                )
            )

            for filename in filenames:
                candidate = (
                    location_path
                    / filename
                )

                if not candidate.is_file():
                    continue

                resolved_path = str(
                    candidate
                )

                _ICON_FILE_CACHE[
                    normalized_name
                ] = resolved_path

    # Flatpak's exported icon paths can be symlinks whose
    # targets are not usable from a container filesystem
    # namespace. If the icon name is also a Flatpak app ID,
    # look directly inside the installed Flatpak app data.
    flatpak_app_roots = [
        Path(
            "/var/lib/flatpak/app"
        ),
        Path(
            "/run/host/var/lib/flatpak/app"
        ),
        (
            Path.home()
            / ".local"
            / "share"
            / "flatpak"
            / "app"
        ),
    ]

    for flatpak_root in flatpak_app_roots:
        application_root = (
            flatpak_root
            / normalized_name
        )

        if not application_root.is_dir():
            continue

        patterns = [
            (
                "*/*/*/export/share/icons/"
                "hicolor/*/apps/"
                f"{normalized_name}.png"
            ),
            (
                "*/*/*/export/share/icons/"
                "hicolor/*/apps/"
                f"{normalized_name}.svg"
            ),
            (
                "*/*/*/files/share/icons/"
                "hicolor/*/apps/"
                f"{normalized_name}.png"
            ),
            (
                "*/*/*/files/share/icons/"
                "hicolor/*/apps/"
                f"{normalized_name}.svg"
            ),
        ]

        candidates: list[Path] = []

        for pattern in patterns:
            try:
                candidates.extend(
                    application_root.glob(
                        pattern
                    )
                )

            except OSError:
                continue

        if not candidates:
            continue

        # Prefer a larger raster icon when several sizes
        # are available. Qt will scale it down for the card.
        candidates.sort(
            key=lambda path: (
                "128x128" not in str(path),
                "64x64" not in str(path),
                str(path),
            )
        )

        for candidate in candidates:
            if not candidate.is_file():
                continue

            resolved_path = str(
                candidate
            )

            _ICON_FILE_CACHE[
                normalized_name
            ] = resolved_path

            return resolved_path

    return None

def _steam_icon_file_for_window_class(
    window_class: str,
) -> str | None:
    normalized_class = (
        window_class.strip().casefold()
    )

    steam_prefix = "steam_app_"

    if not normalized_class.startswith(
        steam_prefix
    ):
        return None

    app_id = normalized_class[
        len(steam_prefix):
    ]

    if not app_id.isdigit():
        return None

    if normalized_class in _STEAM_ICON_CACHE:
        return _STEAM_ICON_CACHE[
            normalized_class
        ]

    steam_cache_roots = [
        (
            Path.home()
            / ".local"
            / "share"
            / "Steam"
            / "appcache"
            / "librarycache"
        ),
        (
            Path.home()
            / ".steam"
            / "root"
            / "appcache"
            / "librarycache"
        ),
    ]

    image_suffixes = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }

    hexadecimal = set(
        "0123456789abcdef"
    )

    for cache_root in steam_cache_roots:
        application_cache = (
            cache_root
            / app_id
        )

        if not application_cache.is_dir():
            continue

        try:
            candidates = list(
                application_cache.iterdir()
            )

        except OSError:
            continue

        for candidate in candidates:
            if not candidate.is_file():
                continue

            if (
                candidate.suffix.casefold()
                not in image_suffixes
            ):
                continue

            stem = candidate.stem.casefold()

            # Steam's small application icon is normally
            # stored using its 40-character SHA-1-style
            # hash rather than a descriptive filename.
            if (
                len(stem) != 40
                or not set(stem).issubset(
                    hexadecimal
                )
            ):
                continue

            resolved_path = str(
                candidate
            )

            _STEAM_ICON_CACHE[
                normalized_class
            ] = resolved_path

            return resolved_path

    _STEAM_ICON_CACHE[
        normalized_class
    ] = None

    return None

def _display_key_name(
    key_name: str,
) -> str:
    display_name = KEY_DISPLAY_NAMES.get(
        key_name
    )

    if display_name is not None:
        return display_name

    return key_name.removeprefix(
        "KEY_"
    )


def _qt_event_key_name(
    event,
) -> str | None:
    scan_code = int(
        event.nativeScanCode()
    )

    # Qt/XKB keycodes on our Linux desktop are
    # Linux evdev codes plus 8.
    evdev_code = scan_code - 8

    if evdev_code < 0:
        return None

    return KEY_CODE_TO_NAME.get(
        evdev_code
    )


class KeyCaptureEdit(QLineEdit):
    def __init__(
        self,
        capture_callback,
        parent=None,
    ):
        super().__init__(parent)

        self.setObjectName(
            "keyCaptureEdit"
        )

        self._capture_callback = (
            capture_callback
        )

        self._key_name = ""
        self._modifiers: list[str] = []

        self._held_modifiers: set[str] = (
            set()
        )

        self._sequence_committed = False

        self.setReadOnly(True)

        self.setPlaceholderText(
            "Click here, then press a key or chord"
        )

        self.setToolTip(
            "Click this field and press the "
            "physical key or key combination."
        )

        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
        )

    def currentText(
        self,
    ) -> str:
        return self._key_name

    def setCurrentText(
        self,
        key_name: str,
    ) -> None:
        self._key_name = str(
            key_name
        ).strip()

        self._update_display()

    def setCurrentIndex(
        self,
        index: int,
    ) -> None:
        # Compatibility with the old QComboBox.
        # Resetting the editor should leave the
        # capture field empty.
        self.clear_capture()

    def setModifiers(
        self,
        modifiers: list[str],
    ) -> None:
        self._modifiers = [
            modifier
            for modifier in MODIFIER_NAMES
            if modifier in modifiers
        ]

        self._update_display()

    def clear_capture(
        self,
    ) -> None:
        self._key_name = ""
        self._modifiers = []

        self._held_modifiers.clear()
        self._sequence_committed = False

        self.clear()

    def _update_display(
        self,
    ) -> None:
        if not self._key_name:
            self.clear()
            return

        parts = [
            _display_key_name(modifier)
            for modifier in self._modifiers
        ]

        parts.append(
            _display_key_name(
                self._key_name
            )
        )

        self.setText(
            " + ".join(parts)
        )

    def _show_pending_modifiers(
        self,
    ) -> None:
        modifiers = [
            modifier
            for modifier in MODIFIER_NAMES
            if modifier
            in self._held_modifiers
        ]

        if not modifiers:
            return

        parts = [
            _display_key_name(modifier)
            for modifier in modifiers
        ]

        parts.append("…")

        self.setText(
            " + ".join(parts)
        )

    def _commit_capture(
        self,
        key_name: str,
        modifiers: list[str],
    ) -> None:
        self._key_name = key_name

        self._modifiers = [
            modifier
            for modifier in MODIFIER_NAMES
            if modifier in modifiers
        ]

        self._sequence_committed = True

        self._update_display()

        self._capture_callback(
            self._key_name,
            list(self._modifiers),
        )

    def _handle_key_press(
        self,
        event,
    ) -> bool:
        if event.isAutoRepeat():
            event.accept()
            return True

        key_name = _qt_event_key_name(
            event
        )

        if key_name is None:
            event.accept()
            return True

        if key_name in MODIFIER_NAMES:
            if not self._held_modifiers:
                self._sequence_committed = (
                    False
                )

            self._held_modifiers.add(
                key_name
            )

            self._show_pending_modifiers()

            event.accept()
            return True

        modifiers = [
            modifier
            for modifier in MODIFIER_NAMES
            if modifier
            in self._held_modifiers
        ]

        self._commit_capture(
            key_name,
            modifiers,
        )

        event.accept()
        return True

    def _handle_key_release(
        self,
        event,
    ) -> bool:
        if event.isAutoRepeat():
            event.accept()
            return True

        key_name = _qt_event_key_name(
            event
        )

        if (
            key_name is not None
            and key_name in MODIFIER_NAMES
            and key_name
            in self._held_modifiers
        ):
            if not self._sequence_committed:
                modifiers = [
                    modifier
                    for modifier in MODIFIER_NAMES
                    if (
                        modifier
                        in self._held_modifiers
                        and modifier != key_name
                    )
                ]

                # This also allows a modifier by
                # itself to be used as the key.
                self._commit_capture(
                    key_name,
                    modifiers,
                )

            self._held_modifiers.discard(
                key_name
            )

            if not self._held_modifiers:
                self._sequence_committed = (
                    False
                )

        event.accept()
        return True

    def event(
        self,
        event,
    ) -> bool:
        # QWidget normally consumes Tab for focus
        # traversal. Handling key events here lets
        # Tab itself be captured as a binding.
        if (
            event.type()
            == QEvent.Type.KeyPress
        ):
            return self._handle_key_press(
                event
            )

        if (
            event.type()
            == QEvent.Type.KeyRelease
        ):
            return self._handle_key_release(
                event
            )

        return super().event(event)

def _compact_assignment_name(
    name: str,
    maximum_length: int = 12,
) -> str:
    cleaned_name = " ".join(
        name.split()
    )

    if len(cleaned_name) <= maximum_length:
        return cleaned_name

    return (
        cleaned_name[
            :maximum_length - 1
        ]
        + "…"
    )

class BacklightLensButton(QPushButton):
    BASE_WIDTH = 180
    BASE_HEIGHT = 150
    DISPLAY_SCALE = 0.75

    def __init__(
        self,
        layer: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        self.layer = layer

        self._color = QColor(
            0,
            0,
            0,
        )

        self._selected = False

        self.setFixedSize(
            int(
                self.BASE_WIDTH
                * self.DISPLAY_SCALE
            ),
            int(
                self.BASE_HEIGHT
                * self.DISPLAY_SCALE
            ),
        )

        self.setCursor(
            Qt.CursorShape.PointingHandCursor
        )

        self.setToolTip(
            f"Select M{layer} backlight color"
        )

        self.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: none;
            }
            """
        )

        scale_x = (
            self.width()
            / self.BASE_WIDTH
        )

        scale_y = (
            self.height()
            / self.BASE_HEIGHT
        )

        # Keep the clickable region round, but make the
        # hard-edged QRegion slightly larger than the
        # painted lens. This prevents the mask from
        # clipping QPainter's anti-aliased outer edge.
        self.setMask(
            QRegion(
                round(
                    8
                    * scale_x
                ),
                0,
                round(
                    144
                    * scale_x
                ),
                round(
                    144
                    * scale_y
                ),
                QRegion.RegionType.Ellipse,
            )
        )

    @staticmethod
    def _blend_color(
        source: QColor,
        target: QColor,
        amount: float,
    ) -> QColor:
        amount = max(
            0.0,
            min(
                1.0,
                amount,
            ),
        )

        return QColor(
            round(
                source.red()
                * (1.0 - amount)
                + target.red()
                * amount
            ),
            round(
                source.green()
                * (1.0 - amount)
                + target.green()
                * amount
            ),
            round(
                source.blue()
                * (1.0 - amount)
                + target.blue()
                * amount
            ),
        )

    def set_color(
        self,
        color: QColor,
    ) -> None:
        self._color = QColor(
            color
        )

        self.update()

    def set_selected(
        self,
        selected: bool,
    ) -> None:
        if self._selected == selected:
            return

        self._selected = selected

        self.update()

    def enterEvent(
        self,
        event,
    ) -> None:
        self.update()

        super().enterEvent(
            event
        )

    def leaveEvent(
        self,
        event,
    ) -> None:
        self.update()

        super().leaveEvent(
            event
        )

    def paintEvent(
        self,
        event,
    ) -> None:
        del event

        painter = QPainter(
            self
        )

        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        painter.scale(
            (
                self.width()
                / self.BASE_WIDTH
            ),
            (
                self.height()
                / self.BASE_HEIGHT
            ),
        )

        white = QColor(
            "#ffffff"
        )

        black = QColor(
            "#000000"
        )

        # -------------------------------------------------
        # Shadow beneath the lens
        # -------------------------------------------------

        shadow_rect = QRectF(
            24,
            118,
            112,
            20,
        )

        shadow_gradient = QRadialGradient(
            shadow_rect.center(),
            shadow_rect.width() / 2.0,
            shadow_rect.center(),
        )

        shadow_gradient.setColorAt(
            0.0,
            QColor(
                0,
                0,
                0,
                80,
            ),
        )

        shadow_gradient.setColorAt(
            1.0,
            QColor(
                0,
                0,
                0,
                0,
            ),
        )

        painter.setPen(
            Qt.PenStyle.NoPen
        )

        painter.setBrush(
            shadow_gradient
        )

        painter.drawEllipse(
            shadow_rect
        )

        # -------------------------------------------------
        # Outer bezel
        # -------------------------------------------------

        bezel_rect = QRectF(
            18,
            10,
            124,
            124,
        )

        bezel_gradient = QLinearGradient(
            bezel_rect.topLeft(),
            bezel_rect.bottomLeft(),
        )

        bezel_gradient.setColorAt(
            0.0,
            QColor(
                95,
                108,
                114,
            ),
        )

        bezel_gradient.setColorAt(
            0.22,
            QColor(
                58,
                67,
                72,
            ),
        )

        bezel_gradient.setColorAt(
            1.0,
            QColor(
                24,
                30,
                34,
            ),
        )

        painter.setPen(
            QPen(
                QColor(
                    18,
                    22,
                    25,
                    220,
                ),
                1.4,
            )
        )

        painter.setBrush(
            bezel_gradient
        )

        painter.drawEllipse(
            bezel_rect
        )

        # -------------------------------------------------
        # Illuminated lens
        # -------------------------------------------------

        inner_rect = QRectF(
            29,
            21,
            102,
            102,
        )

        lens_center = QPointF(
            inner_rect.center().x()
            - 14,
            inner_rect.center().y()
            - 16,
        )

        lens_gradient = QRadialGradient(
            lens_center,
            inner_rect.width()
            / 2.0,
            lens_center,
        )

        lens_gradient.setColorAt(
            0.0,
            self._blend_color(
                self._color,
                white,
                0.60,
            ),
        )

        lens_gradient.setColorAt(
            0.32,
            self._blend_color(
                self._color,
                white,
                0.24,
            ),
        )

        lens_gradient.setColorAt(
            0.72,
            self._color,
        )

        lens_gradient.setColorAt(
            1.0,
            self._blend_color(
                self._color,
                black,
                0.38,
            ),
        )

        painter.setPen(
            QPen(
                self._blend_color(
                    self._color,
                    black,
                    0.42,
                ),
                1.0,
            )
        )

        painter.setBrush(
            lens_gradient
        )

        painter.drawEllipse(
            inner_rect
        )

        # -------------------------------------------------
        # Rim highlight
        # -------------------------------------------------

        rim_highlight_rect = QRectF(
            30,
            22,
            100,
            100,
        )

        painter.setBrush(
            Qt.BrushStyle.NoBrush
        )

        painter.setPen(
            QPen(
                QColor(
                    255,
                    255,
                    255,
                    80,
                ),
                1.2,
            )
        )

        painter.drawArc(
            rim_highlight_rect.toRect(),
            35 * 16,
            110 * 16,
        )

        # -------------------------------------------------
        # Upper glare
        # -------------------------------------------------

        glare_rect = QRectF(
            48,
            34,
            30,
            20,
        )

        glare_gradient = QRadialGradient(
            glare_rect.center(),
            glare_rect.width() / 2.0,
            glare_rect.center(),
        )

        glare_gradient.setColorAt(
            0.0,
            QColor(
                255,
                255,
                255,
                130,
            ),
        )

        glare_gradient.setColorAt(
            1.0,
            QColor(
                255,
                255,
                255,
                0,
            ),
        )

        painter.setPen(
            Qt.PenStyle.NoPen
        )

        painter.setBrush(
            glare_gradient
        )

        painter.drawEllipse(
            glare_rect
        )

        # -------------------------------------------------
        # Lower internal glow
        # -------------------------------------------------

        lower_glow_rect = QRectF(
            53,
            76,
            46,
            26,
        )

        lower_glow = QRadialGradient(
            lower_glow_rect.center(),
            lower_glow_rect.width()
            / 2.0,
            lower_glow_rect.center(),
        )

        lower_glow.setColorAt(
            0.0,
            QColor(
                255,
                255,
                255,
                28,
            ),
        )

        lower_glow.setColorAt(
            1.0,
            QColor(
                255,
                255,
                255,
                0,
            ),
        )

        painter.setBrush(
            lower_glow
        )

        painter.drawEllipse(
            lower_glow_rect
        )

        # -------------------------------------------------
        # Selection / hover ring
        # -------------------------------------------------

        if self._selected:
            outline_color = _theme_color(
                "ACCENT"
            )

            outline_width = 4

        elif self.underMouse():
            outline_color = _theme_color(
                "TEXT_BRIGHT"
            )

            outline_width = 3

        else:
            outline_color = _theme_color(
                "BACKLIGHT_LENS_OUTLINE"
            )

            outline_width = 2

        painter.setBrush(
            Qt.BrushStyle.NoBrush
        )

        painter.setPen(
            QPen(
                outline_color,
                outline_width,
            )
        )

        painter.drawEllipse(
            QRectF(
                14,
                6,
                132,
                132,
            )
        )

        # -------------------------------------------------
        # M-state / color text
        # -------------------------------------------------

        red = self._color.red()
        green = self._color.green()
        blue = self._color.blue()

        brightness = (
            red * 299
            + green * 587
            + blue * 114
        ) / 1000

        text_color = (
            QColor(
                "#111111"
            )
            if brightness >= 140
            else QColor(
                "#ffffff"
            )
        )

        painter.setPen(
            text_color
        )

        title_font = painter.font()

        title_font.setBold(
            True
        )

        title_font.setPointSize(
            14
        )

        painter.setFont(
            title_font
        )

        painter.drawText(
            38,
            48,
            84,
            28,
            Qt.AlignmentFlag.AlignCenter,
            f"M{self.layer}",
        )

        value_font = painter.font()

        value_font.setBold(
            True
        )

        value_font.setPointSize(
            9
        )

        painter.setFont(
            value_font
        )

        hex_color = (
            f"#{red:02X}{green:02X}{blue:02X}"
        )

        painter.drawText(
            32,
            74,
            96,
            24,
            Qt.AlignmentFlag.AlignCenter,
            hex_color,
        )


class ProfileCommandList(QListWidget):
    CURSOR_SIZE = 48
    POINT_CURSOR_HOTSPOT = (21, 12)
    CLOSED_CURSOR_HOTSPOT = (24, 25)
    JOYSTICK_DROP_PADDING = 10
    def __init__(
        self,
        deselect_callback=None,
        parent=None,
    ):
        super().__init__(parent)

        self._deselect_callback = deselect_callback
        self._drag_source_item = None
        self._drag_start_position = None

        self._command_drag_active = False
        self._command_drag_id: str | None = None
        self._command_drag_target: QWidget | None = None

        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        self._point_cursor = self._load_command_cursor(
            "point.png",
            self.POINT_CURSOR_HOTSPOT,
        )

        self._closed_cursor = self._load_command_cursor(
            "closed.png",
            self.CLOSED_CURSOR_HOTSPOT,
        )

    def _item_is_command(
        self,
        item,
    ) -> bool:
        if item is None:
            return False

        command_id = item.data(
            Qt.ItemDataRole.UserRole
        )

        return (
            isinstance(command_id, str)
            and bool(command_id)
        )

    def _load_command_cursor(
        self,
        filename: str,
        hotspot: tuple[int, int],
    ) -> QCursor:
        path = (
            Path(__file__).resolve().parent
            / "assets"
            / "cursors"
            / filename
        )

        pixmap = QPixmap(str(path))

        if pixmap.isNull():
            raise RuntimeError(
                f"Could not load cursor image: {path}"
            )

        pixmap = pixmap.scaled(
            self.CURSOR_SIZE,
            self.CURSOR_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        return QCursor(
            pixmap,
            *hotspot,
        )

    def _command_hover_cursor(
        self,
    ) -> QCursor:
        return self._point_cursor

    def _command_drag_cursor(
        self,
    ) -> QCursor:
        return self._closed_cursor

    def _command_drop_target_at(
        self,
        global_position: QPoint,
    ) -> QWidget | None:
        window = self.window()

        local_position = window.mapFromGlobal(
            global_position
        )

        if not window.rect().contains(
            local_position
        ):
            return None

        widget = window.childAt(
            local_position
        )

        while widget is not None:
            if isinstance(
                widget,
                (
                    G13CommandDropButton,
                    ProfileCommandDeleteDropButton,
                ),
            ):
                return widget

            if widget is window:
                break

            widget = widget.parentWidget()

        nearest_candidate = None
        nearest_distance = None

        for candidate in window.findChildren(
            G13CommandDropButton
        ):
            if candidate._drop_button_name not in {
                "G26",
                "G27",
                "G28",
                "G29",
            }:
                continue

            if not candidate.isVisibleTo(window):
                continue

            candidate_position = candidate.mapFromGlobal(
                global_position
            )

            expanded_rect = candidate.rect().adjusted(
                -self.JOYSTICK_DROP_PADDING,
                -self.JOYSTICK_DROP_PADDING,
                self.JOYSTICK_DROP_PADDING,
                self.JOYSTICK_DROP_PADDING,
            )

            if not expanded_rect.contains(
                candidate_position
            ):
                continue

            offset = (
                candidate_position
                - candidate.rect().center()
            )

            distance = (
                offset.x() ** 2
                + offset.y() ** 2
            )

            if (
                nearest_distance is None
                or distance < nearest_distance
            ):
                nearest_candidate = candidate
                nearest_distance = distance

        return nearest_candidate

    def _set_command_drag_target(
        self,
        target: QWidget | None,
    ) -> None:
        if target is self._command_drag_target:
            return

        previous_target = self._command_drag_target
        self._command_drag_target = target

        if previous_target is not None:
            previous_target.set_command_drag_over(
                False
            )

        if target is not None:
            target.set_command_drag_over(
                True
            )

    def _begin_command_drag(
        self,
        global_position: QPoint,
    ) -> None:
        if self._command_drag_active:
            return

        item = self._drag_source_item

        if not self._item_is_command(
            item
        ):
            return

        command_id = item.data(
            Qt.ItemDataRole.UserRole
        )

        if (
            not isinstance(command_id, str)
            or not command_id
        ):
            return

        self._command_drag_active = True
        self._command_drag_id = command_id

        self._clear_command_hover()

        QApplication.setOverrideCursor(
            self._command_drag_cursor()
        )

        self._set_command_drag_target(
            self._command_drop_target_at(
                global_position
            )
        )

    def _restore_command_hover(
        self,
        global_position: QPoint,
    ) -> None:
        local_position = (
            self.viewport().mapFromGlobal(
                global_position
            )
        )

        if not self.viewport().rect().contains(
            local_position
        ):
            self._clear_command_hover()
            return

        item = self.itemAt(
            local_position
        )

        if self._item_is_command(
            item
        ):
            self.viewport().setCursor(
                self._command_hover_cursor()
            )

        else:
            self._clear_command_hover()

    def _finish_command_drag(
        self,
        global_position: QPoint,
        *,
        drop: bool,
    ) -> None:
        target = (
            self._command_drag_target
            if drop
            else None
        )

        command_id = self._command_drag_id

        self._set_command_drag_target(
            None
        )

        self._command_drag_active = False
        self._command_drag_id = None
        self._drag_source_item = None
        self._drag_start_position = None

        QApplication.restoreOverrideCursor()

        self._restore_command_hover(
            global_position
        )

        if (
            target is not None
            and command_id is not None
        ):
            target.drop_command(
                command_id
            )

    def _clear_command_hover(
        self,
    ) -> None:
        self.viewport().setCursor(
            Qt.CursorShape.ArrowCursor
        )

    def mousePressEvent(
        self,
        event,
    ) -> None:
        item = self.itemAt(
            event.position().toPoint()
        )

        if (
            event.button()
            == Qt.MouseButton.LeftButton
            and self._item_is_command(
                item
            )
        ):
            self._drag_source_item = item
            self._drag_start_position = (
                event.position().toPoint()
            )

            self.viewport().setCursor(
                self._command_drag_cursor()
            )

        else:
            self._drag_source_item = None
            self._drag_start_position = None

        super().mousePressEvent(
            event
        )

        if (
            event.button()
            == Qt.MouseButton.LeftButton
            and item is None
        ):
            self.clearSelection()
            self.setCurrentItem(
                None
            )

            self._clear_command_hover()

            if (
                self._deselect_callback
                is not None
            ):
                self._deselect_callback()

    def mouseReleaseEvent(
        self,
        event,
    ) -> None:
        if (
            self._command_drag_active
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            self._finish_command_drag(
                event.globalPosition().toPoint(),
                drop=True,
            )

            event.accept()
            return

        self._drag_source_item = None
        self._drag_start_position = None

        super().mouseReleaseEvent(
            event
        )

        item = self.itemAt(
            event.position().toPoint()
        )

        if self._item_is_command(
            item
        ):
            self.viewport().setCursor(
                self._command_hover_cursor()
            )

        else:
            self._clear_command_hover()

    def mouseMoveEvent(
        self,
        event,
    ) -> None:
        if (
            event.buttons()
            & Qt.MouseButton.LeftButton
            and self._item_is_command(
                self._drag_source_item
            )
            and self._drag_start_position
            is not None
        ):
            distance = (
                event.position().toPoint()
                - self._drag_start_position
            ).manhattanLength()

            global_position = (
                event.globalPosition().toPoint()
            )

            if (
                distance
                >= QApplication.styleHints()
                .startDragDistance()
            ):
                if not self._command_drag_active:
                    self.setCurrentItem(
                        self._drag_source_item
                    )

                    self._begin_command_drag(
                        global_position
                    )

                else:
                    self._set_command_drag_target(
                        self._command_drop_target_at(
                            global_position
                        )
                    )

                event.accept()
                return

            self.viewport().setCursor(
                self._command_drag_cursor()
            )

            return

        super().mouseMoveEvent(
            event
        )

        item = self.itemAt(
            event.position().toPoint()
        )

        if self._item_is_command(
            item
        ):
            self.viewport().setCursor(
                self._command_hover_cursor()
            )

        else:
            self._clear_command_hover()

    def leaveEvent(
        self,
        event,
    ) -> None:
        self._clear_command_hover()

        super().leaveEvent(
            event
        )

    def clear(
        self,
    ) -> None:
        self._clear_command_hover()

        super().clear()


class G13CommandDropButton(QPushButton):
    def __init__(
        self,
        layer: int,
        button_name: str,
        drop_callback,
        parent=None,
    ):
        super().__init__(parent)

        self._drop_layer = layer
        self._drop_button_name = button_name
        self._drop_callback = drop_callback

    def set_command_drag_over(
        self,
        active: bool,
    ) -> None:
        self.setDown(
            bool(active)
        )

    def drop_command(
        self,
        command_id: str,
    ) -> None:
        self.set_command_drag_over(
            False
        )

        self._drop_callback(
            self._drop_layer,
            self._drop_button_name,
            command_id,
        )


class G13CompactControl(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        self._hover_menu_button: (
            QPushButton | None
        ) = None

        self._hover_check_timer = QTimer(
            self
        )

        self._hover_check_timer.setInterval(
            50
        )

        self._hover_check_timer.timeout.connect(
            self._sync_hover_menu
        )

    def configure_hover_menu(
        self,
        main_button: QPushButton,
        menu_button: QPushButton,
    ) -> None:
        self._hover_main_button = (
            main_button
        )

        self._hover_menu_button = (
            menu_button
        )

        main_button.installEventFilter(
            self
        )

        menu_button.installEventFilter(
            self
        )

        menu_button.hide()

    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:
        if (
            event.type()
            == QEvent.Type.Enter
        ):
            self._show_hover_menu()

        elif (
            event.type()
            == QEvent.Type.Leave
        ):
            QTimer.singleShot(
                0,
                self._sync_hover_menu,
            )

        return super().eventFilter(
            watched,
            event,
        )

    def _set_hover_active(
        self,
        active: bool,
    ) -> None:
        if self._hover_main_button is None:
            return

        if (
            self._hover_main_button.property(
                "overlayHover"
            )
            == active
        ):
            return

        self._hover_main_button.setProperty(
            "overlayHover",
            active,
        )

        self._hover_main_button.style().unpolish(
            self._hover_main_button
        )

        self._hover_main_button.style().polish(
            self._hover_main_button
        )

        self._hover_main_button.update()

    def _show_hover_menu(
        self,
    ) -> None:
        if self._hover_menu_button is None:
            return

        self._hover_menu_button.show()
        self._hover_menu_button.raise_()

        self._set_hover_active(
            True
        )

        if not self._hover_check_timer.isActive():
            self._hover_check_timer.start()

    def _sync_hover_menu(
        self,
    ) -> None:
        if (
            self._hover_menu_button is None
            or self._hover_main_button is None
        ):
            return

        cursor_global = QCursor.pos()

        main_position = (
            self._hover_main_button
            .mapFromGlobal(
                cursor_global
            )
        )

        main_region = (
            self._hover_main_button.mask()
        )

        if main_region.isEmpty():
            over_main = (
                self._hover_main_button.rect()
                .contains(main_position)
            )
        else:
            over_main = (
                main_region.contains(
                    main_position
                )
            )

        menu_position = (
            self._hover_menu_button
            .mapFromGlobal(
                cursor_global
            )
        )

        over_menu = (
            self._hover_menu_button.rect()
            .contains(menu_position)
        )

        if over_main or over_menu:
            self._hover_menu_button.show()
            self._hover_menu_button.raise_()

            self._set_hover_active(
                True
            )

            return

        self._hover_menu_button.hide()

        self._set_hover_active(
            False
        )

        if self._hover_check_timer.isActive():
            self._hover_check_timer.stop()

class G13DeviceSurface(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        self.setObjectName("g13DeviceSurface")

        image_path = (
            Path(__file__).resolve().parent
            / "assets"
            / "g13_device.png"
        )

        self.device_pixmap = QPixmap(
            str(image_path)
        )

        if self.device_pixmap.isNull():
            raise FileNotFoundError(
                f"Could not load device image: {image_path}"
            )

        self.setFixedSize(
            680,
            508,
        )

        # Keep the interactive portion of the G13 large
        # enough to edit comfortably while fitting the
        # Assignments page at the default window size.
        #
        # All G-key, thumb-button, joystick, and assignment
        # overlays are positioned from this scaled pixmap,
        # so they remain aligned with the device image.
        self.display_pixmap = (
            self.device_pixmap.scaled(
                520,
                780,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

        self.device_image_x = (
            self.width()
            - self.display_pixmap.width()
        ) // 2

        self.device_image_y = -100

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )

    def paintEvent(
        self,
        event,
    ) -> None:
        super().paintEvent(event)

        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )

        painter.drawPixmap(
            self.device_image_x,
            self.device_image_y,
            self.display_pixmap,
        )

        g25_callout = getattr(
            self,
            "g25_callout_label",
            None,
        )

        g25_button = getattr(
            self,
            "g25_hotspot_button",
            None,
        )

        if (
            g25_callout is not None
            and g25_button is not None
            and g25_callout.isVisible()
        ):
            start_point = (
                g25_callout
                .mapTo(
                    self,
                    g25_callout.rect().bottomLeft(),
                )
            )

            target_point = (
                g25_button
                .mapTo(
                    self,
                    g25_button.rect().center(),
                )
            )

            painter.setRenderHint(
                QPainter.RenderHint.Antialiasing,
                True,
            )

            painter.setPen(
                QPen(
                    QColor(
                        190,
                        198,
                        201,
                        210,
                    ),
                    1,
                )
            )

            painter.drawLine(
                start_point,
                target_point,
            )

class ProfileCommandDeleteDropButton(
    QPushButton
):
    def __init__(
        self,
        delete_callback,
        parent=None,
    ):
        super().__init__(
            "",
            parent,
        )

        self._delete_callback = (
            delete_callback
        )
        self._drag_over = False
        self._mouse_over = False

        self.setToolTip(
            "Drag a command here to delete it"
        )
        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.setFixedSize(
            26,
            26,
        )

        trash_icon = self.style().standardIcon(
            QStyle.StandardPixmap.SP_TrashIcon
        )

        self.setIcon(
            trash_icon
        )

        self.setIconSize(
            QSize(
                15,
                15,
            )
        )

        self._update_style()

    def _update_style(
        self,
    ) -> None:
        if self._drag_over:
            background = EDITOR_THEME[
                "BG_DELETE"
            ]
            border = EDITOR_THEME[
                "BORDER_DELETE"
            ]

        elif self._mouse_over:
            background = EDITOR_THEME[
                "BG_CONTROL_HOVER"
            ]
            border = EDITOR_THEME[
                "BORDER_NEUTRAL_HOVER"
            ]

        else:
            background = EDITOR_THEME[
                "BG_CONTROL_SUBTLE"
            ]
            border = EDITOR_THEME[
                "BORDER_CONTROL"
            ]

        self.setStyleSheet(
            _theme_stylesheet(f"""
            QPushButton {{
                background-color: {background};
                border: 2px solid {border};
                border-radius: 4px;
                padding: 2px;
            }}

            QPushButton:pressed {{
                background-color: $BG_BUTTON_PRESSED;
                border: 2px solid $BORDER_NEUTRAL_PRESSED;
            }}
            """)
        )

    def set_command_drag_over(
        self,
        active: bool,
    ) -> None:
        active = bool(
            active
        )

        if self._drag_over == active:
            return

        self._drag_over = active

        if not active:
            local_position = (
                self.mapFromGlobal(
                    QCursor.pos()
                )
            )

            self._mouse_over = (
                self.rect().contains(
                    local_position
                )
            )

        self._update_style()

    def drop_command(
        self,
        command_id: str,
    ) -> None:
        self.set_command_drag_over(
            False
        )

        self._delete_callback(
            command_id
        )

    def enterEvent(
        self,
        event,
    ) -> None:
        self._mouse_over = True
        self._update_style()

        super().enterEvent(
            event
        )

    def leaveEvent(
        self,
        event,
    ) -> None:
        self._mouse_over = False
        self._update_style()

        super().leaveEvent(
            event
        )


class ProfileCardButton(QPushButton):
    def __init__(
        self,
        filename: str,
        menu_callback,
        parent=None,
    ):
        super().__init__(parent)

        self._filename = filename
        self._menu_callback = menu_callback

        self._menu_button = QPushButton(
            self,
        )

        arrow_pixmap = QPixmap(10, 10)
        arrow_pixmap.fill(Qt.GlobalColor.transparent)

        arrow_painter = QPainter(arrow_pixmap)
        arrow_painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )
        arrow_painter.setPen(Qt.PenStyle.NoPen)
        arrow_painter.setBrush(
            QColor("#909090")
        )
        arrow_painter.drawPolygon(
            QPolygon([
                QPoint(1, 3),
                QPoint(9, 3),
                QPoint(5, 8),
            ])
        )
        arrow_painter.end()

        self._menu_button.setIcon(
            QIcon(arrow_pixmap)
        )
        self._menu_button.setIconSize(
            QSize(10, 10)
        )

        self._menu_button.setObjectName(
            "profileCardMenuButton"
        )

        self._menu_button.setFixedSize(
            20,
            18,
        )

        self._menu_button.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        self._menu_button.setCursor(
            Qt.CursorShape.ArrowCursor
        )

        self._menu_button.setToolTip(
            "Profile actions"
        )

        self._menu_button.clicked.connect(
            self._show_menu
        )

        self._menu_button.hide()

    def _show_menu(
        self,
        checked: bool = False,
    ) -> None:
        if self._menu_callback is None:
            return

        self._menu_callback(
            self._filename,
            self._menu_button,
        )

        self._menu_button.setDown(
            False
        )

        if self.underMouse():
            self._menu_button.raise_()
            self._menu_button.show()

        else:
            self._menu_button.hide()

        self.repaint()

    def enterEvent(
        self,
        event,
    ) -> None:
        if self._menu_callback is not None:
            self._menu_button.raise_()
            self._menu_button.show()

        super().enterEvent(
            event
        )

    def leaveEvent(
        self,
        event,
    ) -> None:
        self._menu_button.hide()

        super().leaveEvent(
            event
        )

    def resizeEvent(
        self,
        event,
    ) -> None:
        super().resizeEvent(
            event
        )

        self._menu_button.move(
            self.width()
            - self._menu_button.width()
            - 4,
            4,
        )


class OnboardProfileCard(
    ProfileCardButton
):
    def __init__(
        self,
        slot_number: int,
        parent=None,
    ):
        super().__init__(
            f"onboard-{slot_number}",
            None,
            parent,
        )

        self.slot_number = slot_number

        self.setObjectName(
            "profileCard"
        )

        self.setFixedSize(
            88,
            88,
        )

        self.setCursor(
            Qt.CursorShape.ArrowCursor
        )

        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        self.setProperty(
            "editing",
            False,
        )

        self.setProperty(
            "locked",
            False,
        )

        card_layout = QVBoxLayout(
            self
        )

        card_layout.setContentsMargins(
            5,
            4,
            5,
            4,
        )

        card_layout.setSpacing(
            2
        )

        self._icon_label = QLabel(
            "?"
        )

        self._icon_label.setObjectName(
            "profileCardFallbackIcon"
        )

        self._icon_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self._icon_label.setFixedSize(
            32,
            32,
        )

        self._name_label = QLabel(
            "Empty"
        )

        self._name_label.setObjectName(
            "profileCardName"
        )

        self._name_label.setAlignment(
            (
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
            )
        )

        self._name_label.setWordWrap(
            True
        )

        self._name_label.setFixedHeight(
            24
        )

        card_layout.addWidget(
            self._icon_label,
            0,
            (
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
            ),
        )

        card_layout.addStretch()

        card_layout.addWidget(
            self._name_label
        )

        self.setToolTip(
            f"Onboard profile slot "
            f"{slot_number}: Empty"
        )

    def _set_menu_callback(
        self,
        callback,
    ) -> None:
        self._menu_callback = callback

        if callback is None:
            self._menu_button.hide()

    def set_loading_state(
        self,
    ) -> None:
        self._icon_label.setText(
            "…"
        )

        self._name_label.setText(
            "Loading..."
        )

        self._set_menu_callback(
            None
        )

    def set_reading_state(
        self,
    ) -> None:
        self._icon_label.setText(
            "…"
        )

        self._name_label.setText(
            "Reading..."
        )

        self.setToolTip(
            ""
        )

        self._set_menu_callback(
            None
        )

    def set_unavailable_state(
        self,
        error,
    ) -> None:
        self._icon_label.setText(
            "!"
        )

        self._name_label.setText(
            "Unavailable"
        )

        self.setToolTip(
            f"Slot {self.slot_number}: "
            f"{error}"
        )

        self._set_menu_callback(
            None
        )

    def set_profile_state(
        self,
        profile_name: str,
        menu_callback,
    ) -> None:
        self._icon_label.setText(
            (
                profile_name[:1].upper()
                if profile_name
                else "?"
            )
        )

        self._name_label.setText(
            profile_name
        )

        self.setToolTip(
            f"Slot {self.slot_number}: "
            f"{profile_name}"
        )

        self._set_menu_callback(
            menu_callback
        )

    def set_empty_state(
        self,
    ) -> None:
        self._icon_label.setText(
            "—"
        )

        self._name_label.setText(
            "Empty"
        )

        self.setToolTip(
            f"Slot {self.slot_number}: Empty"
        )

        self._set_menu_callback(
            None
        )


class ProfileStripCardContainer(QWidget):
    CARD_WIDTH = 88
    CARD_HEIGHT = 88

    # These are the two visual tuning values.
    FILTER_LIFT = 16
    FILTER_DURATION_MS = 240

    def __init__(
        self,
        card: ProfileCardButton,
        search_name: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        self.card = card

        self.search_name = (
            search_name
            .strip()
            .casefold()
        )

        self._animated_width = (
            self.CARD_WIDTH
        )

        self._filter_visible = True

        self._animation_group: (
            QParallelAnimationGroup | None
        ) = None

        self.setObjectName(
            "profileStripCardContainer"
        )

        # This container exists only for clipping and
        # animation. It must not draw a panel or border.
        self.setStyleSheet(
            """
            QWidget#profileStripCardContainer {
                background-color: transparent;
                border: none;
            }
            """
        )

        self.setFixedSize(
            self.CARD_WIDTH,
            self.CARD_HEIGHT,
        )

        self.card.setParent(
            self
        )

        self.card.move(
            0,
            0,
        )

        self.card.show()

        self._opacity_effect = (
            QGraphicsOpacityEffect(
                self.card
            )
        )

        self._opacity_effect.setOpacity(
            1.0
        )

        self.card.setGraphicsEffect(
            self._opacity_effect
        )

    def _get_animated_width(
        self,
    ) -> int:
        return self._animated_width

    def _set_animated_width(
        self,
        value: int,
    ) -> None:
        width = max(
            0,
            min(
                self.CARD_WIDTH,
                int(value),
            ),
        )

        if width == self._animated_width:
            return

        self._animated_width = width

        self.setFixedWidth(
            width
        )

        self.updateGeometry()

    animatedWidth = Property(
        int,
        _get_animated_width,
        _set_animated_width,
    )

    def stop_filter_animation(
        self,
    ) -> None:
        if self._animation_group is None:
            return

        self._animation_group.stop()
        self._animation_group.deleteLater()

        self._animation_group = None

    def _apply_filter_state(
        self,
        visible: bool,
    ) -> None:
        if visible:
            self.show()

        self._set_animated_width(
            self.CARD_WIDTH
            if visible
            else 0
        )

        self.card.move(
            0,
            (
                0
                if visible
                else -self.FILTER_LIFT
            ),
        )

        self._opacity_effect.setOpacity(
            1.0
            if visible
            else 0.0
        )

        if not visible:
            self.hide()

    def _finish_filter_animation(
        self,
        group: QParallelAnimationGroup,
        visible: bool,
    ) -> None:
        if self._animation_group is not group:
            return

        self._animation_group = None

        self._apply_filter_state(
            visible
        )

        group.deleteLater()

    def set_filter_visible(
        self,
        visible: bool,
        animate: bool = True,
    ) -> None:
        visible = bool(
            visible
        )

        if self._animation_group is not None:
            # The card is already moving toward the same
            # state. Let that animation finish.
            if visible == self._filter_visible:
                return

            # The search changed direction before the
            # previous animation finished. Continue back
            # from the card's current position and opacity.
            self.stop_filter_animation()

        target_width = (
            self.CARD_WIDTH
            if visible
            else 0
        )

        target_position = QPoint(
            0,
            (
                0
                if visible
                else -self.FILTER_LIFT
            ),
        )

        target_opacity = (
            1.0
            if visible
            else 0.0
        )

        already_at_target = (
            visible == self._filter_visible
            and self._animated_width
            == target_width
            and self.card.pos()
            == target_position
            and abs(
                self._opacity_effect.opacity()
                - target_opacity
            )
            < 0.001
        )

        if already_at_target:
            return

        self._filter_visible = visible

        # A hidden zero-width container must be shown
        # before its return animation can be painted.
        self.show()

        if not animate:
            self._apply_filter_state(
                visible
            )
            return

        group = QParallelAnimationGroup(
            self
        )

        easing = QEasingCurve(
            QEasingCurve.Type.InOutCubic
        )

        width_animation = QPropertyAnimation(
            self,
            b"animatedWidth",
        )

        width_animation.setDuration(
            self.FILTER_DURATION_MS
        )

        width_animation.setStartValue(
            self._animated_width
        )

        width_animation.setEndValue(
            target_width
        )

        width_animation.setEasingCurve(
            easing
        )

        position_animation = QPropertyAnimation(
            self.card,
            b"pos",
        )

        position_animation.setDuration(
            self.FILTER_DURATION_MS
        )

        position_animation.setStartValue(
            self.card.pos()
        )

        position_animation.setEndValue(
            target_position
        )

        position_animation.setEasingCurve(
            easing
        )

        opacity_animation = QPropertyAnimation(
            self._opacity_effect,
            b"opacity",
        )

        opacity_animation.setDuration(
            self.FILTER_DURATION_MS
        )

        opacity_animation.setStartValue(
            self._opacity_effect.opacity()
        )

        opacity_animation.setEndValue(
            target_opacity
        )

        opacity_animation.setEasingCurve(
            easing
        )

        group.addAnimation(
            width_animation
        )

        group.addAnimation(
            position_animation
        )

        group.addAnimation(
            opacity_animation
        )

        def finish_animation() -> None:
            self._finish_filter_animation(
                group,
                visible,
            )

        group.finished.connect(
            finish_animation
        )

        self._animation_group = group

        group.start()

class G13Editor(QMainWindow):
    def __init__(self):
        super().__init__()

        self.manager = ProfileManager()

        self._active_editor_theme_name = (
            ACTIVE_EDITOR_THEME_NAME
        )

        self._restart_after_close = False

        saved_editor_theme = (
            normalize_editor_theme_name(
                self.manager.settings.get(
                    "editor_theme",
                    self._active_editor_theme_name,
                )
            )
        )

        # Keep the manager's in-memory settings synchronized
        # with the theme setting read before the editor was
        # constructed. This prevents a later settings save from
        # dropping the key when an older settings file did not
        # contain it yet.
        self.manager.settings[
            "editor_theme"
        ] = saved_editor_theme

        self.detected_window_class: str | None = None
        self.runtime_active_profile: str | None = None
        self.runtime_locked_profile: str | None = None
        self.runtime_profile_source: str | None = None
        self.runtime_window_class = ""

        # On startup, open the profile that is actually
        # active on the G13 rather than always opening the
        # saved fallback. This synchronization happens only
        # once so later runtime profile changes do not pull
        # the editor away from whatever the user is editing.
        self._startup_runtime_profile_synced = False

        self.driver_connected = False

        self.selected_layer = 1
        self.device_layer: int | None = None
        self.selected_button: str | None = None
        self.macro_steps: list[
            dict[str, Any]
        ] = []
        self.active_macro_recorder: (
            MacroRecorderDialog | None
        ) = None

        self.live_pressed_buttons: set[
            tuple[int, str]
        ] = set()

        self.event_listener = G13EventListener()

        self.command_publisher = (
            G13CommandPublisher()
        )

        self.event_timer = QTimer(self)

        self.event_timer.setInterval(30)

        self.event_timer.timeout.connect(
            self._poll_driver_events
        )

        self.event_timer.start()

        self.driver_connection_timer = QTimer(
            self
        )

        self.driver_connection_timer.setSingleShot(
            True
        )

        self.driver_connection_timer.setInterval(
            2000
        )

        self.driver_connection_timer.timeout.connect(
            self._driver_connection_timed_out
        )

        self.button_widgets: dict[
            tuple[int, str],
            QPushButton,
        ] = {}

        self.button_assignment_labels: dict[
            tuple[int, str],
            QLabel,
        ] = {}

        self.setWindowTitle(
            "G13 Zombie Control Center"
        )
        screen = QApplication.primaryScreen()

        if screen is not None:
            available = screen.availableGeometry()

            startup_width = min(
                1120,
                int(available.width() * 0.88),
            )

            startup_height = min(
                980,
                available.height() - 40,
            )

        else:
            startup_width = 1080
            startup_height = 650

        self.setMinimumSize(
            1080,
            600,
        )

        self._build_interface()

        self.resize(
            startup_width,
            startup_height,
        )

        application = QApplication.instance()

        if application is not None:
            application.installEventFilter(
                self
            )

        self._refresh_profile_list()
        self._refresh_all_buttons()
        self._reset_binding_editor()
        self._load_joystick_settings()
        self._set_profile_dirty(
            False
        )

        self.statusBar().showMessage(
            f"Loaded {self.manager.active_profile_name}"
        )

        self.statusBar().hide()

    def _is_command_editor_click(
        self,
        watched,
    ) -> bool:
        if not isinstance(
            watched,
            QWidget,
        ):
            return False

        editor_roots = (
            getattr(
                self,
                "action_type_combo",
                None,
            ),
            getattr(
                self,
                "action_name_edit",
                None,
            ),
            getattr(
                self,
                "action_stack",
                None,
            ),
            getattr(
                self,
                "apply_binding_button",
                None,
            ),
            getattr(
                self,
                "clear_binding_button",
                None,
            ),
        )

        widget = watched

        while widget is not None:
            if widget in editor_roots:
                return True

            widget = widget.parentWidget()

        return False

    def _is_g13_control_click(
        self,
        watched,
    ) -> bool:
        if not isinstance(
            watched,
            QWidget,
        ):
            return False

        widget = watched

        while widget is not None:
            if isinstance(
                widget,
                G13CommandDropButton,
            ):
                return True

            if (
                isinstance(
                    widget,
                    QPushButton,
                )
                and widget.objectName()
                in {
                    "g13ControlMenuButton",
                    "g13CompactMenuButton",
                }
            ):
                return True

            widget = widget.parentWidget()

        return False

    def _is_device_panel_click(
        self,
        watched,
    ) -> bool:
        if not isinstance(
            watched,
            QWidget,
        ):
            return False

        widget = watched

        while widget is not None:
            if (
                widget.objectName()
                == "g13DevicePanel"
            ):
                return True

            widget = widget.parentWidget()

        return False


    def _is_command_list_click(
        self,
        watched,
        event,
    ) -> bool:
        if (
            not isinstance(
                watched,
                QWidget,
            )
            or not hasattr(
                self,
                "commands_list",
            )
        ):
            return False

        try:
            global_position = (
                watched.mapToGlobal(
                    event.position().toPoint()
                )
            )

        except AttributeError:
            return False

        viewport = (
            self.commands_list.viewport()
        )

        local_position = (
            viewport.mapFromGlobal(
                global_position
            )
        )

        if not viewport.rect().contains(
            local_position
        ):
            return False

        item = self.commands_list.itemAt(
            local_position
        )

        return self.commands_list._item_is_command(
            item
        )

    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:
        event_type = event.type()

        if (
            event_type
            == QEvent.Type.MouseButtonPress
            and isinstance(
                watched,
                QWidget,
            )
            and watched.window() is self
            and getattr(
                self,
                "selected_command_id",
                None,
            )
            is not None
            and not self._is_command_list_click(
                watched,
                event,
            )
            and not self._is_command_editor_click(
                watched
            )
        ):
            self._deselect_profile_command()

        if (
            event_type
            == QEvent.Type.MouseButtonPress
            and isinstance(
                watched,
                QWidget,
            )
            and watched.window() is self
            and self.selected_button is not None
            and self._is_device_panel_click(
                watched
            )
            and not self._is_g13_control_click(
                watched
            )
            and not self._is_command_editor_click(
                watched
            )
        ):
            self._reset_binding_editor()

        return super().eventFilter(
            watched,
            event,
        )

# ---------------------------------------------------------
# Interface construction
# ---------------------------------------------------------

    def _build_interface(self):
        central = QWidget()
        central.setObjectName("mainShell")

        root = QVBoxLayout(central)
        root.setContentsMargins(
            10,
            10,
            10,
            8,
        )
        root.setSpacing(8)

        root.addWidget(
            self._build_shell_header()
        )

        self.main_pages = QStackedWidget()

        # -----------------------------------------------------
        # Assignments page
        #
        # The entire existing working editor remains here.
        # -----------------------------------------------------
        assignments_page = QWidget()

        assignments_page.setObjectName(
            "assignmentsPage"
        )

        assignments_layout = QVBoxLayout(
            assignments_page
        )

        assignments_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        assignments_layout.setSpacing(0)

        assignments_content = QHBoxLayout()

        assignments_content.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        assignments_content.setSpacing(
            0
        )

        binding_panel = (
            self._build_binding_panel()
        )

        binding_scroll = QScrollArea()

        binding_scroll.setObjectName(
            "bindingPanelScroll"
        )

        binding_scroll.setWidgetResizable(
            True
        )

        binding_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        binding_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        binding_scroll.setFixedWidth(
            300
        )

        binding_scroll.setWidget(
            binding_panel
        )

        binding_scroll.setStyleSheet(
            """
            QScrollArea#bindingPanelScroll {
                background-color: transparent;
                border: none;
            }

            QScrollArea#bindingPanelScroll
            QScrollBar:vertical {
                width: 7px;
            }
            """
        )

        assignments_content.addWidget(
            binding_scroll,
            0,
        )

        assignments_content.addWidget(
            self._build_device_panel(),
            1,
        )

        assignments_layout.addLayout(
            assignments_content
        )

        assignments_scroll = QScrollArea()

        assignments_scroll.setObjectName(
            "assignmentsScroll"
        )

        assignments_scroll.setWidgetResizable(
            True
        )

        assignments_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        assignments_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        assignments_scroll.setWidget(
            assignments_page
        )

        assignments_scroll.setStyleSheet(
            """
            QScrollArea#assignmentsScroll {
                background-color: transparent;
                border: none;
            }
            """
        )

        self.main_pages.addWidget(
            assignments_scroll
        )

# Empty pages are intentional during the first shell
# milestone. Working controls will be moved only after
# each destination page has been designed and tested.

        self.main_pages.addWidget(
            self._build_backlight_page()
        )

        self.main_pages.addWidget(
            self._build_joystick_page()
        )

        self.main_pages.addWidget(
            self._build_profiles_applications_page()
        )

        self.main_pages.addWidget(
            self._build_settings_page()
        )

        root.addWidget(
            self.main_pages,
            1,
        )

        root.addWidget(
            self._build_bottom_navigation()
        )

        self.setCentralWidget(central)

    def _build_shell_header(
        self,
    ) -> QWidget:
        header = QWidget()
        header.setObjectName("mainHeader")

        layout = QHBoxLayout(header)

        layout.setContentsMargins(
            16,
            10,
            20,
            10,
        )

        layout.setSpacing(
            10
        )

        layout.addWidget(
            self._build_profile_strip(),
            1,
        )

        header_reload_button = QPushButton(
            "Reload"
        )

        header_reload_button.setObjectName(
            "headerActionButton"
        )

        header_reload_button.clicked.connect(
            self._reload_profile
        )

        self.header_save_button = QPushButton(
            "Save"
        )

        self.header_save_button.setObjectName(
            "headerSaveButton"
        )

        self.header_save_button.setEnabled(
            False
        )

        self.header_save_button.clicked.connect(
            self._save_profile
        )

        header_actions = QWidget()

        header_actions.setObjectName(
            "headerProfileActions"
        )

        header_actions.setFixedSize(
            82,
            92,
        )

        header_actions_layout = QVBoxLayout(
            header_actions
        )

        header_actions_layout.setContentsMargins(
            0,
            4,
            0,
            0,
        )

        header_actions_layout.setSpacing(
            0
        )

        header_actions_layout.addWidget(
            header_reload_button
        )

        header_actions_layout.addStretch()

        header_actions_layout.addWidget(
            self.header_save_button
        )

        layout.addWidget(
            header_actions,
            0,
            Qt.AlignmentFlag.AlignTop,
        )

        header.setStyleSheet(
            _theme_stylesheet("""
            QWidget#mainHeader {
                background-color: $BG_SHELL;
                border: 1px solid $BORDER_SHELL;
                border-radius: 5px;
            }

            QWidget#headerProfileActions {
                background-color: transparent;
                border: none;
            }

            QPushButton#headerActionButton,
            QPushButton#headerSaveButton {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_HEADER_BUTTON;
                min-width: 58px;
                padding: 8px 10px;
            }

            QPushButton#headerActionButton:hover,
            QPushButton#headerSaveButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QPushButton#headerActionButton:pressed,
            QPushButton#headerSaveButton:pressed {
                background-color: $BG_ACCENT;
            }

            QPushButton#headerSaveButton {
                background-color: $BG_WARNING;
                border: 1px solid $WARNING;
                color: $TEXT_BRIGHT;
                font-weight: bold;
            }

            QPushButton#headerSaveButton:disabled {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                color: $TEXT_DISABLED_HEADER;
                font-weight: normal;
            }
            """)
        )

        return header

    def _build_profile_strip(
        self,
    ) -> QWidget:
        strip = QWidget()
        strip.setObjectName("profileStrip")

        layout = QHBoxLayout(strip)

        layout.setContentsMargins(
            4,
            0,
            0,
            0,
        )

        layout.setSpacing(
            6
        )

        profile_controls = QWidget()

        profile_controls.setObjectName(
            "profileControls"
        )

        profile_controls.setFixedSize(
            72,
            102,
        )

        profile_controls_layout = QVBoxLayout(
            profile_controls
        )

        profile_controls_layout.setContentsMargins(
            0,
            4,
            0,
            10,
        )

        profile_controls_layout.setSpacing(
            0
        )

        label = QLabel(
            "Profiles"
        )

        label.setObjectName(
            "profileStripLabel"
        )

        label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        label.setFixedSize(
            72,
            24,
        )

        profile_controls_layout.addWidget(
            label,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        profile_controls_layout.addStretch()

        self.profile_search_edit = QLineEdit()

        self.profile_search_edit.setObjectName(
            "profileSearchEdit"
        )

        self.profile_search_edit.setPlaceholderText(
            "Search"
        )

        self.profile_search_edit.setToolTip(
            "Filter profiles by name"
        )

        self.profile_search_edit.setFixedSize(
            72,
            24,
        )

        self.profile_search_edit.textChanged.connect(
            self._filter_profile_strip_cards
        )

        self.profile_search_edit.hide()

        profile_controls_layout.addWidget(
            self.profile_search_edit,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        profile_controls_layout.addSpacing(
            7
        )

        profile_buttons_layout = QHBoxLayout()

        profile_buttons_layout.setContentsMargins(
            7,
            0,
            7,
            0,
        )

        profile_buttons_layout.setSpacing(
            6
        )

        self.profile_search_button = QPushButton()

        self.profile_search_button.setIcon(
            _profile_search_icon()
        )

        self.profile_search_button.setIconSize(
            QSize(
                16,
                16,
            )
        )

        self.profile_search_button.setObjectName(
            "profileSearchButton"
        )

        self.profile_search_button.setToolTip(
            "Search profiles"
        )

        self.profile_search_button.setFixedSize(
            26,
            26,
        )

        self.profile_search_button.clicked.connect(
            self._toggle_profile_search
        )

        self.profile_add_button = QPushButton(
            "+"
        )

        self.profile_add_button.setObjectName(
            "profileAddButton"
        )

        self.profile_add_button.setToolTip(
            "Create or import a profile"
        )

        self.profile_add_button.setFixedSize(
            26,
            26,
        )

        self.profile_add_button.clicked.connect(
            self._show_profile_actions_menu
        )

        profile_buttons_layout.addWidget(
            self.profile_search_button
        )

        profile_buttons_layout.addWidget(
            self.profile_add_button
        )

        profile_controls_layout.addLayout(
            profile_buttons_layout
        )

        layout.addWidget(
            profile_controls,
            0,
            Qt.AlignmentFlag.AlignTop,
        )

        scroll = QScrollArea()

        scroll.setObjectName(
            "profileStripScroll"
        )

        scroll.setWidgetResizable(
            True
        )

        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        scroll.setFixedHeight(
            112
        )

        self.profile_strip_content = QWidget()

        self.profile_strip_content.setObjectName(
            "profileStripContent"
        )

        self.profile_strip_layout = QHBoxLayout(
            self.profile_strip_content
        )

        self.profile_strip_layout.setContentsMargins(
            4,
            4,
            4,
            4,
        )

        self.profile_strip_layout.setSpacing(
            6
        )

        scroll.setWidget(
            self.profile_strip_content
        )

        layout.addWidget(
            scroll,
            1,
        )

        strip.setStyleSheet(
            _theme_stylesheet("""
            QWidget#profileStrip {
                background-color: transparent;
            }

            QWidget#profileControls {
                background-color: transparent;
                border: none;
            }

            QLabel#profileStripLabel {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_ACCENT;
                font-size: 12px;
                font-weight: bold;
                padding: 0;
            }

            QPushButton#profileAddButton,
            QPushButton#profileSearchButton {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_ACCENT;
                font-size: 18px;
                font-weight: bold;
                padding: 0;
            }

            QPushButton#profileAddButton:hover,
            QPushButton#profileSearchButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QPushButton#profileAddButton:pressed,
            QPushButton#profileSearchButton:pressed {
                background-color: $BG_ACCENT;
            }

            QLineEdit#profileSearchEdit {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_PRIMARY_COOL;
                font-size: 10px;
                padding: 2px 4px;
            }

            QLineEdit#profileSearchEdit:focus {
                border-color: $ACCENT;
            }

            QScrollArea#profileStripScroll {
                background-color: transparent;
                border: none;
            }

            QScrollArea#profileStripScroll QScrollBar:horizontal {
                background-color: $BG_SHELL;
                height: 7px;
                margin: 0px;
            }

            QScrollArea#profileStripScroll QScrollBar::handle:horizontal {
                background-color: $BORDER_TEAL;
                border-radius: 3px;
                min-width: 28px;
            }

            QScrollArea#profileStripScroll QScrollBar::handle:horizontal:hover {
                background-color: $ACCENT;
            }

            QScrollArea#profileStripScroll QScrollBar::add-line:horizontal,
            QScrollArea#profileStripScroll QScrollBar::sub-line:horizontal {
                width: 0px;
                height: 0px;
            }

            QScrollArea#profileStripScroll QScrollBar::add-page:horizontal,
            QScrollArea#profileStripScroll QScrollBar::sub-page:horizontal {
                background-color: transparent;
            }

            QWidget#profileStripContent {
                background-color: transparent;
            }

            QPushButton#profileCard {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 5px;
                padding: 0;
                text-align: center;
            }

            QPushButton#profileCard:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
            }

            QPushButton#profileCard[locked="true"][editing="false"] {
                border: 2px solid $BORDER_LOCKED;
            }

            QPushButton#profileCard[locked="true"][editing="false"]:hover {
                border: 2px solid $BORDER_LOCKED_HOVER;
            }

            QPushButton#profileCard[editing="true"] {
                background-color: $BG_ACCENT;
                border: 2px solid $ACCENT;
            }

            QPushButton#profileCardMenuButton {
                background-color: $BG_MENU_BUTTON;
                border: 1px solid $BORDER_TEAL_STRONG;
                border-radius: 3px;
                color: $TEXT_ACCENT_LIGHT;
                font-size: 11px;
                font-weight: bold;
                padding: 0;
            }

            QPushButton#profileCardMenuButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QPushButton#profileCardMenuButton:pressed {
                background-color: $BG_ACCENT;
            }

            QLabel#profileCardAppIcon {
                background-color: transparent;
                border: none;
                padding: 0;
            }

            QLabel#profileCardFallbackIcon {
                background-color: $BG_ICON;
                border: 1px solid $BORDER_TEAL_STRONG;
                border-radius: 16px;
                color: $TEXT_ACCENT;
                font-size: 16px;
                font-weight: bold;
            }

            QPushButton#profileCard:hover
            QLabel#profileCardFallbackIcon {
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QPushButton#profileCard[editing="true"]
            QLabel#profileCardFallbackIcon {
                background-color: $ACCENT;
                border-color: $ACCENT_LIGHT;
                color: $BG_SHELL;
            }

            QLabel#profileCardName {
                color: $TEXT_PROFILE;
                font-size: 9px;
                font-weight: bold;
            }

            QLabel#profileMarkerEditing {
                background-color: $ACCENT;
                color: $BG_SHELL;
            }

            QLabel#profileMarkerActive {
                background-color: $WARNING;
                color: $TEXT_ON_ACCENT;
            }

            QLabel#profileMarkerLocked {
                background-color: $BG_LOCKED;
                color: $TEXT_BRIGHT;
            }
            """)
        )

        return strip

    def _filter_profile_strip_cards(
        self,
        text: str = "",
        animate: bool = True,
    ) -> None:
        containers = getattr(
            self,
            "_profile_strip_card_containers",
            (),
        )

        if hasattr(
            self,
            "profile_search_edit",
        ):
            search_query = (
                self.profile_search_edit
                .text()
                .strip()
                .casefold()
            )

        else:
            search_query = (
                str(text)
                .strip()
                .casefold()
            )

        for container in containers:
            matches = (
                not search_query
                or container.search_name
                .startswith(
                    search_query
                )
            )

            container.set_filter_visible(
                matches,
                animate=animate,
            )



    def _toggle_profile_search(
        self,
    ) -> None:
        if self.profile_search_edit.isVisible():
            self.profile_search_edit.hide()
            self.profile_search_edit.clear()
            return

        self.profile_search_edit.show()
        self.profile_search_edit.setFocus()
        self.profile_search_edit.selectAll()


    def _show_profile_actions_menu(
        self,
    ) -> None:
        menu = QMenu(self)

        new_action = menu.addAction(
            "New Profile"
        )

        import_action = menu.addAction(
            "Import Logitech XML"
        )

        selected_action = menu.exec(
            self.profile_add_button.mapToGlobal(
                self.profile_add_button.rect()
                .bottomLeft()
            )
        )

        if selected_action == new_action:
            self._create_profile()

        elif selected_action == import_action:
            self._import_logitech_profile()

    def _show_profile_card_actions_menu(
        self,
        filename: str,
        menu_button: QPushButton,
    ) -> None:
        menu = QMenu(self)

        menu.setStyleSheet(
            _theme_stylesheet("""
            QMenu {
                background-color: $BG_MENU;
                border: 1px solid $BORDER_TEAL;
                padding: 4px;
            }

            QMenu::item {
                color: $TEXT_PRIMARY_COOL;
                padding: 7px 24px 7px 10px;
                border-radius: 3px;
            }

            QMenu::item:selected {
                background-color: $BG_ACCENT;
                color: $TEXT_BRIGHT;
            }

            QMenu::item:disabled {
                background-color: transparent;
                color: $TEXT_DISABLED_MENU;
            }

            QMenu::separator {
                height: 1px;
                background-color: $BG_SEPARATOR;
                margin: 4px 6px;
            }
            """)
        )

        rename_action = menu.addAction(
            "Rename Profile"
        )

        duplicate_action = menu.addAction(
            "Duplicate Profile"
        )

        delete_action = menu.addAction(
            "Delete Profile"
        )

        menu.addSeparator()

        saved_fallback = (
            self.manager.get_fallback_profile()
        )

        is_fallback = (
            saved_fallback == filename
        )

        fallback_action = menu.addAction(
            (
                "Fallback Profile"
                if is_fallback
                else "Set as Fallback Profile"
            )
        )

        fallback_action.setEnabled(
            not is_fallback
        )

        menu.addSeparator()

        choose_icon_action = menu.addAction(
            "Choose Icon..."
        )

        reset_icon_action = menu.addAction(
            "Reset Icon"
        )

        try:
            manual_icon_path = (
                self.manager
                .get_profile_icon_path(
                    filename
                )
            )

        except Exception:
            manual_icon_path = None

        reset_icon_action.setEnabled(
            bool(manual_icon_path)
        )

        menu.addSeparator()

        scripting_action = menu.addAction(
            "Scripting..."
        )

        menu.addSeparator()

        copy_to_g13_action = menu.addAction(
            "Copy to G13"
        )

        selected_action = menu.exec(
            menu_button.mapToGlobal(
                menu_button.rect().bottomLeft()
            )
        )

        if selected_action is None:
            return

        if selected_action == fallback_action:
            self._set_fallback_profile(
                filename
            )
            return

        if (
            filename
            != self.manager.active_profile_name
        ):
            self._profile_changed(
                filename
            )

            if (
                self.manager.active_profile_name
                != filename
            ):
                return

        if selected_action == rename_action:
            self._rename_profile()

        elif selected_action == duplicate_action:
            self._duplicate_profile()

        elif selected_action == delete_action:
            self._delete_profile()

        elif selected_action == choose_icon_action:
            self._choose_profile_icon()

        elif selected_action == reset_icon_action:
            self._reset_profile_icon()

        elif selected_action == scripting_action:
            self._edit_profile_script()

        elif selected_action == copy_to_g13_action:
            self._copy_local_profile_to_g13(
                filename
            )

    def _set_fallback_profile(
        self,
        filename: str,
    ) -> None:
        try:
            display_name = (
                self.manager
                .get_profile_display_name(
                    filename
                )
            )

        except Exception:
            display_name = filename

        answer = QMessageBox.question(
            self,
            "Change Fallback Profile?",
            (
                "Set this profile as the fallback "
                "profile?\n\n"
                f"{display_name}\n\n"
                "The G13 will return to this profile "
                "when no application profile, temporary "
                "profile, or profile lock is overriding it."
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )

        if (
            answer
            != QMessageBox.StandardButton.Yes
        ):
            return

        try:
            self.manager.set_fallback_profile(
                filename
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Fallback Profile",
                str(error),
            )
            return

        self._refresh_profiles_applications_status()

        self.statusBar().showMessage(
            f"Fallback profile set to "
            f"{display_name}."
        )


    def _copy_local_profile_to_g13(
        self,
        filename: str,
    ) -> None:
        status = getattr(
            self,
            "_onboard_status_cache",
            None,
        )

        if status is None:
            QMessageBox.critical(
                self,
                "Copy to G13",
                (
                    "Onboard profile data is not "
                    "loaded. Open Settings > "
                    "Developer and click Refresh."
                ),
            )
            return

        current_bank = (
            status.current_bank
        )

        verify_readback = (
            self.manager.get_verify_onboard_writes()
        )

        if (
            len(current_bank.profile_names)
            >= 5
        ):
            QMessageBox.information(
                self,
                "Copy to G13",
                (
                    "The G13 already contains "
                    "five onboard profiles.\n\n"
                    "Remove an onboard profile "
                    "before copying another one."
                ),
            )
            return

        try:
            if (
                self.manager.active_profile_name
                != filename
            ):
                raise RuntimeError(
                    "The selected local profile "
                    "is not currently loaded."
                )

            profile_data = (
                self.manager.get_profile_copy()
            )

            profile_name = str(
                profile_data.get(
                    "name",
                    filename,
                )
            )

            if (
                current_bank.storage_format
                == "zombie"
            ):
                result = (
                    add_zombie_onboard_profile(
                        filename,
                        profile_data,
                        before_memory=(
                            status.memory_image
                        ),
                        verify_readback=(
                            verify_readback
                        ),
                    )
                )

            elif (
                current_bank.storage_format
                == "logitech"
            ):
                xml_data = export_profile_xml(
                    profile_data,
                    source_name=filename,
                )

                result = (
                    add_logitech_onboard_profile(
                        xml_data,
                        before_memory=(
                            status.memory_image
                        ),
                        verify_readback=(
                            verify_readback
                        ),
                    )
                )

            else:
                raise RuntimeError(
                    "Unsupported onboard storage "
                    f"format: "
                    f"{current_bank.storage_format!r}."
                )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Copy to G13",
                str(error),
            )
            return

        write_onboard_cache(
            result.memory_image
        )

        self._apply_onboard_status_to_ui(
            result
        )

        self.command_publisher.publish_reload_onboard_profiles_cache()

        copied_bank = (
            result.current_bank
        )

        self.statusBar().showMessage(
            f"Copied {profile_name} to G13."
        )

        QMessageBox.information(
            self,
            "Copy Complete",
            (
                f"'{profile_name}' was copied "
                "to the G13.\n\n"
                f"Slot: "
                f"{len(copied_bank.profile_names)}"
            ),
        )

    def _choose_profile_icon(
        self,
    ) -> None:
        filename = (
            self.manager.active_profile_name
        )

        if not filename:
            return

        start_location = str(
            Path.home()
        )

        try:
            current_icon_path = (
                self.manager
                .get_profile_icon_path()
            )

        except Exception:
            current_icon_path = None

        if current_icon_path:
            current_path = Path(
                current_icon_path
            )

            if current_path.parent.is_dir():
                start_location = str(
                    current_path.parent
                )

        selected_file, _ = (
            QFileDialog.getOpenFileName(
                self,
                "Choose Profile Icon",
                start_location,
                (
                    "Images "
                    "(*.png *.jpg *.jpeg *.bmp "
                    "*.svg *.svgz *.webp *.ico);;"
                    "All Files (*)"
                ),
            )
        )

        if not selected_file:
            return

        selected_icon = QIcon(
            selected_file
        )

        if selected_icon.isNull():
            QMessageBox.critical(
                self,
                "Choose Profile Icon",
                (
                    "The selected file could not "
                    "be loaded as an icon."
                ),
            )
            return

        try:
            icon_path = str(
                Path(
                    selected_file
                ).resolve()
            )

            self.manager.set_profile_icon_path(
                icon_path
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Choose Profile Icon",
                str(error),
            )
            return

        self._set_profile_dirty(
            True
        )

        self._refresh_profile_strip()

        self.statusBar().showMessage(
            "Profile icon changed."
        )

    def _reset_profile_icon(
        self,
    ) -> None:
        if not self.manager.active_profile_name:
            return

        try:
            self.manager.set_profile_icon_path(
                None
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Reset Profile Icon",
                str(error),
            )
            return

        self._set_profile_dirty(
            True
        )

        self._refresh_profile_strip()

        self.statusBar().showMessage(
            "Profile icon reset to automatic."
        )

    def _edit_profile_script(
        self,
    ) -> None:
        filename = (
            self.manager
            .active_profile_name
        )

        if not filename:
            QMessageBox.information(
                self,
                "Lua Scripting",
                "No profile is open for editing.",
            )

            return

        try:
            profile_name = (
                self.manager
                .get_profile_display_name(
                    filename
                )
            )

            script = (
                self.manager
                .get_profile_script()
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Lua Scripting",
                str(error),
            )

            return

        dialog = ScriptEditorDialog(
            profile_name,
            script,
            self,
        )

        if not dialog.exec():
            return

        try:
            self.manager.save_profile_script(
                dialog.script_data()
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Lua Scripting",
                str(error),
            )

            return

        self.statusBar().showMessage(
            f"Lua script saved for {profile_name}."
        )

    def _profile_application_icon(
        self,
        filename: str,
    ) -> QIcon | None:
        try:
            manual_icon_path = (
                self.manager
                .get_profile_icon_path(
                    filename
                )
            )

        except Exception:
            manual_icon_path = None

        if manual_icon_path:
            manual_icon = QIcon(
                manual_icon_path
            )

            if not manual_icon.isNull():
                return manual_icon

        try:
            rules = (
                self.manager
                .get_application_rules()
            )

        except Exception:
            return None

        window_class = None

        for rule in rules:
            if (
                rule.get("profile")
                != filename
            ):
                continue

            candidate_class = rule.get(
                "window_class"
            )

            if (
                isinstance(
                    candidate_class,
                    str,
                )
                and candidate_class.strip()
            ):
                window_class = (
                    candidate_class.strip()
                )
                break

        if window_class is None:
            return None

        steam_icon_path = (
            _steam_icon_file_for_window_class(
                window_class
            )
        )

        if steam_icon_path:
            steam_icon = QIcon(
                steam_icon_path
            )

            if not steam_icon.isNull():
                return steam_icon

        icon_value = (
            _desktop_icon_value_for_window_class(
                window_class
            )
        )

        if not icon_value:
            return None

        icon_path = Path(
            icon_value
        )

        if icon_path.is_absolute():
            icon = QIcon(
                str(icon_path)
            )

        else:
            icon = QIcon.fromTheme(
                icon_value
            )

            if icon.isNull():
                resolved_icon_path = (
                    _icon_file_for_name(
                        icon_value
                    )
                )

                if resolved_icon_path:
                    icon = QIcon(
                        resolved_icon_path
                    )

        if icon.isNull():
            return None

        return icon

    def _create_profile_strip_card(
        self,
        display_name: str,
        filename: str | None,
        onboard_slot: int | None,
        editing: bool = False,
        runtime_active: bool = False,
        locked: bool = False,
    ) -> ProfileCardButton:
        is_local = (
            filename is not None
        )

        if is_local:
            card_id = filename
            menu_callback = (
                self._show_profile_card_actions_menu
            )

        else:
            if onboard_slot is None:
                raise ValueError(
                    "Onboard-only profile card "
                    "requires an onboard slot."
                )

            card_id = (
                f"onboard-{onboard_slot}"
            )

            menu_callback = (
                self._show_onboard_profile_card_actions_menu
            )

        card = ProfileCardButton(
            card_id,
            menu_callback,
        )

        card.setObjectName(
            "profileCard"
        )

        card.setFixedSize(
            88,
            88,
        )

        if is_local:
            card.setCursor(
                Qt.CursorShape.ArrowCursor
            )

            card.clicked.connect(
                lambda checked=False,
                selected_filename=filename:
                self._profile_card_clicked(
                    selected_filename
                )
            )

        else:
            card.setCursor(
                Qt.CursorShape.ArrowCursor
            )

            card.setFocusPolicy(
                Qt.FocusPolicy.NoFocus
            )

        card.setProperty(
            "editing",
            editing,
        )

        card.setProperty(
            "locked",
            locked,
        )

        tooltip_lines = []

        if is_local:
            tooltip_lines.append(
                filename
            )

            if editing:
                tooltip_lines.append(
                    "Open for editing"
                )

            if runtime_active:
                tooltip_lines.append(
                    "Runtime active profile"
                )

            if locked:
                tooltip_lines.append(
                    "Locked profile"
                )

            if onboard_slot is not None:
                tooltip_lines.append(
                    f"G13 onboard slot "
                    f"{onboard_slot}"
                )

        else:
            tooltip_lines.extend(
                (
                    display_name,
                    (
                        f"G13 onboard slot "
                        f"{onboard_slot}"
                    ),
                    "Copy from G13 to edit locally",
                )
            )

        card.setToolTip(
            "\n".join(
                tooltip_lines
            )
        )

        card_layout = QVBoxLayout(
            card
        )

        card_layout.setContentsMargins(
            5,
            4,
            5,
            4,
        )

        card_layout.setSpacing(
            2
        )

        icon_source = (
            display_name.strip()
        )

        profile_icon = None

        if is_local:
            profile_icon = (
                self._profile_application_icon(
                    filename
                )
            )

        icon_label = QLabel()

        icon_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        if profile_icon is not None:
            icon_label.setObjectName(
                "profileCardAppIcon"
            )

            icon_label.setFixedSize(
                44,
                44,
            )

            icon_label.setPixmap(
                profile_icon.pixmap(
                    QSize(
                        40,
                        40,
                    )
                )
            )

        else:
            icon_label.setObjectName(
                "profileCardFallbackIcon"
            )

            icon_label.setFixedSize(
                32,
                32,
            )

            icon_label.setText(
                icon_source[:1].upper()
                if icon_source
                else "?"
            )

        name_label = QLabel(
            display_name
        )

        name_label.setObjectName(
            "profileCardName"
        )

        name_label.setAlignment(
            (
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
            )
        )

        name_label.setWordWrap(
            True
        )

        name_label.setFixedHeight(
            24
        )

        card_layout.addWidget(
            icon_label,
            0,
            (
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
            ),
        )

        card_layout.addStretch()

        card_layout.addWidget(
            name_label
        )

        if onboard_slot is not None:
            g13_badge = QLabel(
                "G13",
                card,
            )

            g13_badge.setObjectName(
                "profileCardG13Badge"
            )

            g13_badge.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )

            g13_badge.setFixedSize(
                28,
                10,
            )

            g13_badge.move(
                (
                    card.width()
                    - g13_badge.width()
                )
                // 2,
                43,
            )

            g13_badge.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )

            g13_badge.raise_()

        return card

    def _refresh_profile_strip(
        self,
    ) -> None:
        if not hasattr(
            self,
            "profile_strip_layout",
        ):
            return

        for container in getattr(
            self,
            "_profile_strip_card_containers",
            (),
        ):
            container.stop_filter_animation()

        self._profile_strip_card_containers = []

        while (
            self.profile_strip_layout.count()
            > 0
        ):
            item = (
                self.profile_strip_layout
                .takeAt(0)
            )

            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        editing_profile = (
            self.manager.active_profile_name
        )

        runtime_active_profile = (
            self.runtime_active_profile
        )

        locked_profile = (
            self.runtime_locked_profile
        )

        local_filenames = (
            self.manager.list_profiles()
        )

        local_display_names = {}

        for filename in local_filenames:
            try:
                local_display_names[
                    filename
                ] = (
                    self.manager
                    .get_profile_display_name(
                        filename
                    )
                )

            except Exception:
                local_display_names[
                    filename
                ] = filename

        onboard_profiles = ()

        status = getattr(
            self,
            "_onboard_status_cache",
            None,
        )

        if status is not None:
            try:
                onboard_profiles = (
                    get_onboard_profiles(
                        status.current_bank
                    )
                )

            except Exception:
                onboard_profiles = ()

        onboard_slot_for_local = {}
        matched_onboard_slots = set()

        for onboard_profile in onboard_profiles:
            matched_filename = None

            if (
                onboard_profile.storage_format
                == "zombie"
                and onboard_profile.filename
                in local_filenames
            ):
                matched_filename = (
                    onboard_profile.filename
                )

            else:
                onboard_name = (
                    onboard_profile.name
                    .strip()
                    .casefold()
                )

                name_matches = [
                    filename
                    for filename
                    in local_filenames
                    if (
                        local_display_names[
                            filename
                        ]
                        .strip()
                        .casefold()
                        == onboard_name
                    )
                ]

                if len(name_matches) == 1:
                    matched_filename = (
                        name_matches[0]
                    )

            if matched_filename is None:
                continue

            if (
                matched_filename
                in onboard_slot_for_local
            ):
                continue

            onboard_slot_for_local[
                matched_filename
            ] = onboard_profile.slot

            matched_onboard_slots.add(
                onboard_profile.slot
            )

        self._local_onboard_slot_map = (
            onboard_slot_for_local
        )

        for filename in local_filenames:

            card = self._create_profile_strip_card(
                display_name=(
                    local_display_names[
                        filename
                    ]
                ),
                filename=filename,
            onboard_slot=None,
                editing=(
                    filename
                    == editing_profile
                ),
                runtime_active=(
                    filename
                    == runtime_active_profile
                ),
                locked=(
                    filename
                    == locked_profile
                ),
            )

            container = ProfileStripCardContainer(
                card,
                search_name=(
                    local_display_names[
                        filename
                    ]
                ),
            )

            self._profile_strip_card_containers.append(
                container
            )

            self.profile_strip_layout.addWidget(
                container,
                0,
                Qt.AlignmentFlag.AlignTop,
            )

        for onboard_profile in onboard_profiles:
            onboard_identifier = (
                f"onboard:{onboard_profile.slot}"
            )

            card = self._create_profile_strip_card(
                display_name=(
                    onboard_profile.name
                ),
                filename=None,
                onboard_slot=(
                    onboard_profile.slot
                ),
                runtime_active=(
                    onboard_identifier
                    == runtime_active_profile
                ),
                locked=(
                    onboard_identifier
                    == locked_profile
                ),
            )

            container = ProfileStripCardContainer(
                card,
                search_name=(
                    onboard_profile.name
                ),
            )

            self._profile_strip_card_containers.append(
                container
            )

            self.profile_strip_layout.addWidget(
                container,
                0,
                Qt.AlignmentFlag.AlignTop,
            )

        self.profile_strip_layout.addStretch()

        self._filter_profile_strip_cards(
            animate=False
        )

    def _profile_card_clicked(
        self,
        filename: str,
    ) -> None:
        self._profile_changed(
            filename
        )

    def _build_settings_page(
        self,
    ) -> QWidget:
        page = QWidget()

        page.setObjectName(
            "settingsPage"
        )

        root = QVBoxLayout(
            page
        )

        root.setContentsMargins(
            20,
            20,
            20,
            20,
        )

        root.setSpacing(
            16
        )

        self.settings_tabs = QTabWidget()

        self.settings_tabs.setObjectName(
            "settingsTabs"
        )

        self.settings_tabs.setDocumentMode(
            True
        )

        self.settings_tabs.tabBar().setDrawBase(
            False
        )

        self.settings_tabs.setFixedHeight(
            400
        )

        # =====================================================
        # General
        # =====================================================

        general_page = QWidget()

        general_page.setObjectName(
            "settingsTabPage"
        )

        general_layout = QVBoxLayout(
            general_page
        )

        general_layout.setContentsMargins(
            20,
            20,
            20,
            20,
        )

        general_layout.setSpacing(
            16
        )

        driver_group = QGroupBox(
            "Driver"
        )

        driver_layout = QFormLayout(
            driver_group
        )

        driver_layout.setSpacing(
            10
        )

        self.settings_device_status_value = QLabel(
            "Disconnected"
        )

        driver_layout.addRow(
            "Status:",
            self.settings_device_status_value,
        )

        driver_group.setFixedHeight(
            70
        )

        general_layout.addWidget(
            driver_group
        )

        self._refresh_device_settings_status()

        macro_group = QGroupBox(
            "Quick Macros"
        )

        macro_layout = QVBoxLayout(
            macro_group
        )

        self.settings_record_macro_delays_check = QCheckBox(
            "Record delays during quick macro recording"
        )

        self.settings_record_macro_delays_check.setChecked(
            self.manager.get_record_macro_delays()
        )

        self.settings_record_macro_delays_check.toggled.connect(
            self._record_macro_delays_toggled
        )

        self.settings_macro_lcd_check = QCheckBox(
            "Show macro recording status on the G13 LCD"
        )

        self.settings_macro_lcd_check.setChecked(
            self.manager
            .get_show_macro_recording_on_lcd()
        )

        self.settings_macro_lcd_check.toggled.connect(
            self._macro_lcd_setting_toggled
        )

        macro_layout.addWidget(
            self.settings_record_macro_delays_check
        )

        macro_layout.addWidget(
            self.settings_macro_lcd_check
        )

        general_layout.addWidget(
            macro_group
        )

        general_layout.addStretch()

        self.settings_tabs.addTab(
            general_page,
            "General",
        )

        # =====================================================
        # Appearance
        # =====================================================

        appearance_page = QWidget()

        appearance_page.setObjectName(
            "settingsTabPage"
        )

        appearance_layout = QVBoxLayout(
            appearance_page
        )

        appearance_layout.setContentsMargins(
            20,
            20,
            20,
            20,
        )

        appearance_layout.setSpacing(
            16
        )

        editor_theme_group = QGroupBox(
            "Editor Theme"
        )

        editor_theme_layout = QFormLayout(
            editor_theme_group
        )

        editor_theme_layout.setSpacing(
            10
        )

        self.settings_editor_theme_combo = (
            QComboBox()
        )

        for theme_name, display_name in (
            EDITOR_THEME_OPTIONS
        ):
            self.settings_editor_theme_combo.addItem(
                display_name,
                theme_name,
            )

        saved_editor_theme = (
            normalize_editor_theme_name(
                self.manager.settings.get(
                    "editor_theme",
                    self._active_editor_theme_name,
                )
            )
        )

        saved_theme_index = (
            self.settings_editor_theme_combo
            .findData(
                saved_editor_theme
            )
        )

        if saved_theme_index >= 0:
            self.settings_editor_theme_combo.setCurrentIndex(
                saved_theme_index
            )

        self.settings_editor_theme_combo.currentIndexChanged.connect(
            self._editor_theme_changed
        )

        editor_theme_layout.addRow(
            "Theme:",
            self.settings_editor_theme_combo,
        )

        editor_theme_note = QLabel(
            "Theme changes are saved immediately and "
            "take effect after the editor restarts."
        )

        editor_theme_note.setObjectName(
            "settingsHelperText"
        )

        editor_theme_note.setWordWrap(
            True
        )

        editor_theme_layout.addRow(
            editor_theme_note
        )

        appearance_layout.addWidget(
            editor_theme_group
        )

        appearance_layout.addStretch()

        self.settings_tabs.addTab(
            appearance_page,
            "Appearance",
        )

        # =====================================================
        # Notifications
        # =====================================================

        notifications_page = QWidget()

        notifications_page.setObjectName(
            "settingsTabPage"
        )

        notifications_layout = QVBoxLayout(
            notifications_page
        )

        notifications_layout.setContentsMargins(
            20,
            20,
            20,
            20,
        )

        notifications_layout.setSpacing(
            16
        )

        lcd_notifications_group = QGroupBox(
            "LCD Notifications"
        )

        lcd_notifications_layout = QVBoxLayout(
            lcd_notifications_group
        )

        self.settings_profile_lcd_check = QCheckBox(
            "Show profile activation on the G13 LCD"
        )

        self.settings_profile_lcd_check.setChecked(
            self.manager
            .get_show_profile_activation_on_lcd()
        )

        self.settings_profile_lcd_check.toggled.connect(
            self._profile_lcd_setting_toggled
        )

        lcd_notifications_layout.addWidget(
            self.settings_profile_lcd_check
        )

        notifications_layout.addWidget(
            lcd_notifications_group
        )

        notifications_layout.addStretch()

        self.settings_tabs.addTab(
            notifications_page,
            "Notifications",
        )

        # =====================================================
        # Developer
        # =====================================================

        developer_page = QWidget()

        developer_page.setObjectName(
            "settingsTabPage"
        )

        developer_layout = QVBoxLayout(
            developer_page
        )

        developer_layout.setContentsMargins(
            20,
            20,
            20,
            20,
        )

        developer_layout.setSpacing(
            16
        )

        diagnostics_group = QGroupBox(
            "Diagnostics"
        )

        diagnostics_layout = QVBoxLayout(
            diagnostics_group
        )

        diagnostics_description = QLabel(
            "Hardware tests and support diagnostics."
        )

        diagnostics_description.setObjectName(
            "settingsHelperText"
        )

        diagnostics_layout.addWidget(
            diagnostics_description
        )

        diagnostic_buttons = QHBoxLayout()

        self.settings_test_lcd_button = QPushButton(
            "Test LCD"
        )

        self.settings_test_lcd_button.clicked.connect(
            self._test_lcd
        )

        self.settings_test_leds_button = QPushButton(
            "Test LEDs"
        )

        self.settings_test_leds_button.clicked.connect(
            self._test_leds
        )

        self.settings_generate_diagnostics_button = (
            QPushButton(
                "Generate Diagnostic Report"
            )
        )

        self.settings_generate_diagnostics_button.clicked.connect(
            self._generate_diagnostic_report
        )

        diagnostic_buttons.addWidget(
            self.settings_test_lcd_button
        )

        diagnostic_buttons.addWidget(
            self.settings_test_leds_button
        )

        diagnostic_buttons.addWidget(
            self.settings_generate_diagnostics_button
        )

        diagnostic_buttons.addStretch()

        diagnostics_layout.addLayout(
            diagnostic_buttons
        )

        self.settings_verify_onboard_writes_checkbox = QCheckBox(
            "Verify onboard writes"
        )

        self.settings_verify_onboard_writes_checkbox.setChecked(
            self.manager.get_verify_onboard_writes()
        )

        self.settings_verify_onboard_writes_checkbox.toggled.connect(
            self.manager.save_verify_onboard_writes
        )

        self.settings_verify_onboard_writes_checkbox.setToolTip(
            (
                "Read the entire G13 onboard memory "
                "after each profile write and verify "
                "both banks. This is slower and is "
                "primarily intended for diagnostics."
            )
        )

        diagnostics_layout.addWidget(
            self.settings_verify_onboard_writes_checkbox
        )

        verify_description = QLabel(
            (
                "Performs a full readback after onboard "
                "profile changes. Slower; useful for "
                "diagnostics and hardware verification."
            )
        )

        verify_description.setObjectName(
            "settingsHelperText"
        )

        diagnostics_layout.addWidget(
            verify_description
        )

        developer_layout.addWidget(
            diagnostics_group
        )

        about_group = QGroupBox(
            "About"
        )

        about_group.setFixedHeight(
            175
        )

        about_layout = QVBoxLayout(
            about_group
        )

        about_title = QLabel(
            f"G13 Zombie Control Center — Version {APP_VERSION}"
        )

        about_title.setObjectName(
            "settingsAboutTitle"
        )

        about_layout.addWidget(
            about_title
        )

        about_description = QLabel(
            (
                "A Linux control center for the Logitech "
                "G13 gameboard. Configure profiles, key "
                "assignments, macros, backlighting, LCD "
                "behavior, joystick settings, application "
                "switching, and onboard profiles."
            )
        )

        about_description.setWordWrap(
            True
        )

        about_description.setMinimumHeight(
            about_description.fontMetrics().lineSpacing()
            * 3
            + 8
        )

        about_layout.addWidget(
            about_description
        )

        about_platform = QLabel(
            (
                "Built for modern Linux desktop and "
                "gaming environments."
            )
        )

        about_platform.setObjectName(
            "settingsHelperText"
        )

        about_layout.addWidget(
            about_platform
        )

        about_disclaimer = QLabel(
            (
                "G13 and Logitech are trademarks or product "
                "names of Logitech. G13 Zombie Control Center "
                "is an independent project and is not "
                "affiliated with or endorsed by Logitech."
            )
        )

        about_disclaimer.setObjectName(
            "settingsHelperText"
        )

        about_disclaimer.setWordWrap(
            True
        )

        about_disclaimer.setMinimumHeight(
            about_disclaimer.fontMetrics().lineSpacing()
            * 3
            + 8
        )

        about_layout.addWidget(
            about_disclaimer
        )

        about_layout.addStretch()

        developer_layout.addWidget(
            about_group
        )

        developer_layout.addStretch()

        self.settings_tabs.addTab(
            developer_page,
            "Developer",
        )

        root.addWidget(
            self.settings_tabs
        )

        root.addStretch()

        page.setStyleSheet(
            _theme_stylesheet("""
            QWidget#settingsPage {
                background-color: $BG_PAGE;
                border: none;
            }

            QWidget#settingsTabPage {
                background-color: $BG_TAB_PANE;
                border: none;
            }

            QLabel#settingsHelperText {
                background-color: transparent;
                border: none;
                color: $TEXT_HELP;
            }

            QLabel#settingsAboutTitle {
                background-color: transparent;
                border: none;
                color: $TEXT_PRIMARY;
                font-weight: bold;
            }

            QPushButton#profileCard {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 5px;
                padding: 0;
                text-align: center;
            }

            QPushButton#profileCard:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
            }

            QPushButton#profileCardMenuButton {
                background-color: $BG_MENU_BUTTON;
                border: 1px solid $BORDER_TEAL_STRONG;
                border-radius: 3px;
                color: $TEXT_ACCENT_LIGHT;
                padding: 0;
            }

            QPushButton#profileCardMenuButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QPushButton#profileCardMenuButton:pressed {
                background-color: $BG_ACCENT;
            }

            QLabel#profileCardFallbackIcon {
                background-color: $BG_ICON;
                border: 1px solid $BORDER_TEAL_STRONG;
                border-radius: 16px;
                color: $TEXT_ACCENT;
                font-size: 16px;
                font-weight: bold;
            }

            QPushButton#profileCard:hover
            QLabel#profileCardFallbackIcon {
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QLabel#profileCardName {
                background-color: transparent;
                border: none;
                color: $TEXT_PROFILE;
                font-size: 9px;
                font-weight: bold;
            }

            QLabel#profileCardG13Badge {
                background-color: $BLACK;
                border: 1px solid $BORDER_TEAL;
                border-radius: 2px;
                color: $TEXT_ACCENT_LIGHT;
                font-size: 8px;
                font-weight: bold;
            }

            QTabWidget#settingsTabs::pane {
                background-color: $BG_TAB_PANE;
                border: 1px solid $BORDER_TEAL;
                border-radius: 6px;
                top: -1px;
            }

            QTabWidget#settingsTabs QTabBar {
                background-color: $BG_TAB_PANE;
                border: none;
                margin-right: 1px;
            }

            QTabWidget#settingsTabs QTabBar::tab {
                background-color: $BG_TAB_INACTIVE;
                border: 1px solid $BORDER_TAB;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                color: $TEXT_TAB;
                min-width: 130px;
                padding: 8px 18px;
                margin-right: 4px;
                font-weight: bold;
            }

            QTabWidget#settingsTabs QTabBar::tab:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QTabWidget#settingsTabs QTabBar::tab:selected {
                background-color: $BG_ACCENT;
                border: 2px solid $ACCENT;
                border-bottom: none;
                color: $TEXT_BRIGHT;
            }

            QTabWidget#settingsTabs QGroupBox {
                background-color: $BG_GROUP;
                border: 1px solid $BORDER_TEAL;
                border-radius: 5px;
                margin-top: 12px;
                padding-top: 10px;
                font-weight: bold;
            }

            QTabWidget#settingsTabs QGroupBox::title {
                color: $TEXT_ACCENT;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }

            QTabWidget#settingsTabs QGroupBox QLabel,
            QTabWidget#settingsTabs QGroupBox QCheckBox {
                background-color: transparent;
                border: none;
            }

            QTabWidget#settingsTabs
            QGroupBox QLabel#profileCardFallbackIcon {
                background-color: $BG_ICON;
                border: 1px solid $BORDER_TEAL_STRONG;
                border-radius: 16px;
                color: $TEXT_ACCENT;
                font-size: 16px;
                font-weight: bold;
            }

            QTabWidget#settingsTabs
            QGroupBox QPushButton#profileCard:hover
            QLabel#profileCardFallbackIcon {
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QTabWidget#settingsTabs
            QGroupBox QLabel#profileCardName {
                background-color: $BG_WINDOW;
                border: none;
                color: $TEXT_PROFILE;
                font-size: 9px;
                font-weight: bold;
            }
            """)
        )

        page.setMinimumHeight(
            page.sizeHint().height()
        )

        scroll = QScrollArea()

        scroll.setObjectName(
            "settingsScroll"
        )

        scroll.setWidgetResizable(
            True
        )

        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        scroll.setWidget(
            page
        )

        scroll.setStyleSheet(
            _theme_stylesheet("""
            QScrollArea#settingsScroll {
                background-color: $BG_PAGE;
                border: none;
            }

            QScrollArea#settingsScroll QScrollBar:vertical {
                width: 7px;
            }
            """)
        )

        frame = QWidget()

        frame.setObjectName(
            "settingsFrame"
        )

        frame_layout = QVBoxLayout(
            frame
        )

        frame_layout.setContentsMargins(
            5,
            5,
            5,
            5,
        )

        frame_layout.setSpacing(
            0
        )

        frame_layout.addWidget(
            scroll
        )

        frame.setStyleSheet(
            _theme_stylesheet("""
            QWidget#settingsFrame {
                background-color: $BG_PAGE;
                border: 1px solid $BORDER_PAGE;
                border-radius: 5px;
            }
            """)
        )

        return frame

    def _show_onboard_profile_card_actions_menu(
        self,
        onboard_id: str,
        menu_button: QPushButton,
    ) -> None:
        prefix = "onboard-"

        if not onboard_id.startswith(
            prefix
        ):
            return

        try:
            slot = int(
                onboard_id[
                    len(prefix):
                ]
            )

        except ValueError:
            return

        menu = QMenu(self)

        menu.setStyleSheet(
            _theme_stylesheet("""
            QMenu {
                background-color: $BG_MENU;
                border: 1px solid $BORDER_TEAL;
                padding: 4px;
            }

            QMenu::item {
                color: $TEXT_PRIMARY_COOL;
                padding: 7px 24px 7px 10px;
                border-radius: 3px;
            }

            QMenu::item:selected {
                background-color: $BG_ACCENT;
                color: $TEXT_BRIGHT;
            }

            QMenu::item:disabled {
                background-color: transparent;
                color: $TEXT_DISABLED_MENU;
            }

            QMenu::separator {
                height: 1px;
                background-color: $BG_SEPARATOR;
                margin: 4px 6px;
            }
            """)
        )

        copy_action = menu.addAction(
            "Copy from G13"
        )

        menu.addSeparator()

        remove_action = menu.addAction(
            "Remove from G13"
        )

        selected_action = menu.exec(
            menu_button.mapToGlobal(
                menu_button.rect().bottomLeft()
            )
        )

        if selected_action == copy_action:
            self._copy_onboard_profile_to_local(
                slot
            )

        elif selected_action == remove_action:
            self._remove_onboard_profile_from_device(
                slot
            )


    def _copy_onboard_profile_to_local(
        self,
        slot: int,
    ) -> None:
        try:
            status = getattr(
                self,
                "_onboard_status_cache",
                None,
            )

            if status is None:
                raise RuntimeError(
                    "Onboard profile data is not "
                    "loaded. Click Refresh and "
                    "try again."
                )

            onboard_profile = get_onboard_profile(
                status.current_bank,
                slot,
            )

            if (
                onboard_profile.storage_format
                == "zombie"
            ):
                profile_data = (
                    onboard_profile.profile_data
                )

                if profile_data is None:
                    raise RuntimeError(
                        f"Onboard slot {slot} "
                        "has no Zombie profile data."
                    )

                suggested_filename = (
                    onboard_profile.filename
                    or (
                        f"{onboard_profile.name}"
                        ".json"
                    )
                )

            elif (
                onboard_profile.storage_format
                == "logitech"
            ):
                if (
                    onboard_profile.xml_data
                    is None
                ):
                    raise RuntimeError(
                        f"Onboard slot {slot} "
                        "has no Logitech XML data."
                    )

                profile_data = (
                    convert_profile_xml(
                        onboard_profile.xml_data,
                        source_name=(
                            f"onboard-slot-"
                            f"{slot}.xml"
                        ),
                    )
                )

                suggested_filename = (
                    f"{onboard_profile.name}.json"
                )

            else:
                raise RuntimeError(
                    "Unsupported onboard storage "
                    f"format: "
                    f"{onboard_profile.storage_format!r}."
                )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Copy from G13",
                str(error),
            )
            return

        if not suggested_filename.lower().endswith(
            ".json"
        ):
            suggested_filename += ".json"

        filename, accepted = QInputDialog.getText(
            self,
            "Copy from G13",
            "Profile filename:",
            QLineEdit.EchoMode.Normal,
            suggested_filename,
        )

        if not accepted:
            return

        filename = filename.strip()

        if not filename:
            return

        if not filename.lower().endswith(
            ".json"
        ):
            filename += ".json"

        if Path(filename).name != filename:
            QMessageBox.critical(
                self,
                "Copy from G13",
                (
                    "The profile filename cannot "
                    "contain directories."
                ),
            )
            return

        output_path = (
            self.manager.profiles_dir
            / filename
        )

        replace_existing = (
            output_path.exists()
        )

        if replace_existing:
            answer = QMessageBox.question(
                self,
                "Replace Existing Profile?",
                (
                    f"A profile named '{filename}' "
                    "already exists.\n\n"
                    "Replace it?"
                ),
                (
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                ),
                QMessageBox.StandardButton.No,
            )

            if (
                answer
                != QMessageBox.StandardButton.Yes
            ):
                return

        try:
            self.manager.store_profile_data(
                profile_data,
                filename,
                replace=replace_existing,
            )

            self.manager.set_active_profile(
                filename,
                persist=False,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Copy from G13",
                str(error),
            )
            return

        self.selected_layer = 1
        self.selected_button = None

        self.layer_tabs.setCurrentIndex(
            0
        )

        self._refresh_profile_list()
        self._refresh_all_buttons()
        self._reset_binding_editor()

        self.statusBar().showMessage(
            f"Copied {onboard_profile.name} "
            "from G13."
        )

        QMessageBox.information(
            self,
            "Copy Complete",
            (
                f"'{onboard_profile.name}' "
                "was copied from the G13 as:\n\n"
                f"{filename}"
            ),
        )


    def _remove_onboard_profile_from_device(
        self,
        slot: int,
    ) -> None:
        try:
            status = getattr(
                self,
                "_onboard_status_cache",
                None,
            )

            if status is None:
                raise RuntimeError(
                    "Onboard profile data is not "
                    "loaded. Click Refresh and "
                    "try again."
                )

            onboard_profile = get_onboard_profile(
                status.current_bank,
                slot,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Remove from G13",
                str(error),
            )
            return

        verify_readback = (
            self.manager.get_verify_onboard_writes()
        )

        answer = QMessageBox.question(
            self,
            "Remove from G13?",
            (
                f"Remove '{onboard_profile.name}' "
                f"from G13 slot {slot}?\n\n"
                "Any local profile file will "
                "remain unchanged."
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )

        if (
            answer
            != QMessageBox.StandardButton.Yes
        ):
            return

        try:
            result = remove_onboard_profile(
                slot,
                before_memory=(
                    status.memory_image
                ),
                verify_readback=(
                    verify_readback
                ),
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Remove from G13",
                str(error),
            )
            return

        write_onboard_cache(
            result.memory_image
        )

        self._apply_onboard_status_to_ui(
            result
        )

        self.command_publisher.publish_reload_onboard_profiles_cache()

        self.statusBar().showMessage(
            f"Removed {onboard_profile.name} "
            "from the G13."
        )

        QMessageBox.information(
            self,
            "Profile Removed",
            (
                f"'{onboard_profile.name}' "
                "was removed from the G13."
            ),
        )

    def _load_cached_onboard_profiles(
        self,
    ) -> None:
        if not hasattr(
            self,
            "settings_onboard_profile_cards",
        ):
            return

        try:
            status = get_cached_onboard_status()

        except Exception as error:
            self.settings_onboard_device_value.setText(
                "Unavailable"
            )

            self.settings_onboard_memory_value.setText(
                "—"
            )

            for card in (
                self.settings_onboard_profile_cards
            ):
                card.set_unavailable_state(
                    error
                )

            return

        if status is None:
            return

        self._apply_onboard_status_to_ui(
            status
        )

    def _apply_onboard_status_to_ui(
        self,
        status,
    ) -> None:
        self._onboard_status_cache = status

        bank = status.current_bank

        self.settings_onboard_device_value.setText(
            bank.device_name
            or "G13"
        )

        self.settings_onboard_memory_value.setText(
            f"{bank.used_bytes:,} / "
            f"{BANK_SIZE:,} bytes"
        )

        if bank.storage_format == "zombie":
            format_text = "Zombie Native"
            convert_text = (
                "Convert to Logitech Compatible"
            )

        elif bank.storage_format == "logitech":
            format_text = "Logitech Compatible"
            convert_text = (
                "Convert to Zombie Native"
            )

        else:
            format_text = bank.storage_format
            convert_text = "Convert Format"

        self.settings_onboard_format_value.setText(
            format_text
        )

        self.settings_onboard_convert_button.setText(
            convert_text
        )

        self.settings_onboard_convert_button.setEnabled(
            bank.storage_format
            in {
                "zombie",
                "logitech",
            }
        )

        for index, card in enumerate(
            self.settings_onboard_profile_cards
        ):
            if index < len(
                bank.profile_names
            ):
                profile_name = (
                    bank.profile_names[index]
                )

                card.set_profile_state(
                    profile_name,
                    self._show_onboard_profile_card_actions_menu,
                )

            else:
                card.set_empty_state()

        self._refresh_profile_strip()

    def _convert_onboard_format(
        self,
    ) -> None:
        status = getattr(
            self,
            "_onboard_status_cache",
            None,
        )

        if status is None:
            QMessageBox.critical(
                self,
                "Convert Onboard Format",
                (
                    "Onboard profile data is not "
                    "currently loaded."
                ),
            )
            return

        bank = status.current_bank

        if bank.storage_format == "zombie":
            target_format = (
                "Logitech Compatible"
            )

            confirmation_text = (
                "Convert all onboard profiles to "
                "Logitech Compatible format?\n\n"
                "All profiles currently stored on "
                "the G13 will be converted together.\n\n"
                "This allows Logitech Gaming Software "
                "in Windows to recognize them.\n\n"
                "Local profile files will not be changed."
            )

            conversion_function = (
                convert_onboard_to_logitech
            )

        elif bank.storage_format == "logitech":
            target_format = (
                "Zombie Native"
            )

            confirmation_text = (
                "Convert all onboard profiles to "
                "Zombie Native format?\n\n"
                "All profiles currently stored on "
                "the G13 will be converted together.\n\n"
                "Zombie Native uses less onboard "
                "memory, but Logitech Gaming Software "
                "in Windows cannot read this format.\n\n"
                "Local profile files will not be changed."
            )

            conversion_function = (
                convert_onboard_to_zombie
            )

        else:
            QMessageBox.critical(
                self,
                "Convert Onboard Format",
                (
                    "Unsupported onboard format: "
                    f"{bank.storage_format!r}"
                ),
            )
            return

        answer = QMessageBox.question(
            self,
            "Convert Onboard Format?",
            confirmation_text,
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )

        if (
            answer
            != QMessageBox.StandardButton.Yes
        ):
            return

        verify_readback = (
            self.manager.get_verify_onboard_writes()
        )

        old_button_text = (
            self.settings_onboard_convert_button.text()
        )

        self.settings_onboard_convert_button.setEnabled(
            False
        )

        self.settings_onboard_convert_button.setText(
            "Converting..."
        )

        self.settings_onboard_refresh_button.setEnabled(
            False
        )

        QApplication.processEvents()

        try:
            result = conversion_function(
                before_memory=(
                    status.memory_image
                ),
                verify_readback=(
                    verify_readback
                ),
            )

            write_onboard_cache(
                result.memory_image
            )

        except Exception as error:
            self.settings_onboard_convert_button.setText(
                old_button_text
            )

            self.settings_onboard_convert_button.setEnabled(
                True
            )

            QMessageBox.critical(
                self,
                "Convert Onboard Format",
                str(error),
            )
            return

        finally:
            self.settings_onboard_refresh_button.setEnabled(
                True
            )

        self._apply_onboard_status_to_ui(
            result
        )

        self.command_publisher.publish_reload_onboard_profiles_cache()

        self.statusBar().showMessage(
            "Converted all G13 onboard profiles "
            f"to {target_format}."
        )

        QMessageBox.information(
            self,
            "Conversion Complete",
            (
                "All onboard profiles were converted "
                f"to {target_format}."
            ),
        )

    def _refresh_onboard_profiles(
        self,
    ) -> None:
        if not hasattr(
            self,
            "settings_onboard_profile_cards",
        ):
            return

        self._onboard_status_cache = None

        for card in (
            self.settings_onboard_profile_cards
        ):
            card.set_reading_state()

        self.settings_onboard_device_value.setText(
            "Reading..."
        )

        self.settings_onboard_memory_value.setText(
            "—"
        )

        self.settings_onboard_refresh_button.setEnabled(
            False
        )

        QApplication.processEvents()

        try:
            status = get_onboard_status()

            write_onboard_cache(
                status.memory_image
            )

        except Exception as error:
            self.settings_onboard_device_value.setText(
                "Unavailable"
            )

            for card in (
                self.settings_onboard_profile_cards
            ):
                card.set_unavailable_state(
                    error
                )

            return

        finally:
            self.settings_onboard_refresh_button.setEnabled(
                True
            )

        self._apply_onboard_status_to_ui(
            status
        )

        self.command_publisher.publish_reload_onboard_profiles_cache()

    def _driver_connection_timed_out(
        self,
    ) -> None:
        self.driver_connected = False

        self._refresh_device_settings_status()

    def _refresh_device_settings_status(
        self,
    ) -> None:
        if not hasattr(
            self,
            "settings_device_status_value",
        ):
            return

        self.settings_device_status_value.setText(
            (
                "Connected"
                if self.driver_connected
                else "Disconnected"
            )
        )

    def _test_lcd(
        self,
    ) -> None:
        self.command_publisher.publish_lcd_test()

        self.statusBar().showMessage(
            "LCD test requested."
        )

    def _generate_diagnostic_report(
        self,
    ) -> None:
        try:
            report_path = write_diagnostic_report(
                qt_version=qVersion(),
                qt_platform=(
                    QApplication.platformName()
                ),
                editor_path=(
                    Path(__file__).resolve()
                ),
                driver_connected=(
                    self.driver_connected
                ),
                active_profile=(
                    self.runtime_active_profile
                ),
                profile_source=(
                    self.runtime_profile_source
                ),
                window_class=(
                    self.runtime_window_class
                ),
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Diagnostic Report",
                (
                    "The diagnostic report could "
                    "not be created.\n\n"
                    f"{error}"
                ),
            )

            return

        QMessageBox.information(
            self,
            "Diagnostic Report",
            (
                "Diagnostic report saved to:\n\n"
                f"{report_path}\n\n"
                "No profile contents or settings "
                "values were included."
            ),
        )

        self.statusBar().showMessage(
            "Diagnostic report saved."
        )


    def _test_leds(
        self,
    ) -> None:
        self.command_publisher.publish_led_test()

        self.statusBar().showMessage(
            "LED test requested."
        )

    def _record_macro_delays_toggled(
        self,
        enabled: bool,
    ) -> None:
        try:
            self.manager.save_record_macro_delays(
                enabled
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Quick Macro Settings",
                str(error),
            )

            return

        state_text = (
            "enabled"
            if enabled
            else "disabled"
        )

        self.statusBar().showMessage(
            f"Quick macro delay recording {state_text}."
        )

    def _macro_lcd_setting_toggled(
        self,
        enabled: bool,
    ) -> None:
        try:
            self.manager.save_show_macro_recording_on_lcd(
                enabled
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Quick Macro Settings",
                str(error),
            )

            return

        if self.active_macro_recorder is not None:
            self.command_publisher.publish_macro_recording(
                enabled
            )

        state_text = (
            "enabled"
            if enabled
            else "disabled"
        )

        self.statusBar().showMessage(
            "Macro recording LCD status "
            f"{state_text}."
        )

    def _editor_theme_changed(
        self,
        _index: int,
    ) -> None:
        selected_theme = (
            normalize_editor_theme_name(
                self.settings_editor_theme_combo
                .currentData()
            )
        )

        saved_theme = (
            normalize_editor_theme_name(
                self.manager.settings.get(
                    "editor_theme",
                    self._active_editor_theme_name,
                )
            )
        )

        if selected_theme == saved_theme:
            return

        try:
            save_editor_theme_name(
                selected_theme
            )

            self.manager.settings[
                "editor_theme"
            ] = selected_theme

        except Exception as error:
            saved_theme_index = (
                self.settings_editor_theme_combo
                .findData(
                    saved_theme
                )
            )

            self.settings_editor_theme_combo.blockSignals(
                True
            )

            if saved_theme_index >= 0:
                self.settings_editor_theme_combo.setCurrentIndex(
                    saved_theme_index
                )

            self.settings_editor_theme_combo.blockSignals(
                False
            )

            QMessageBox.critical(
                self,
                "Editor Theme",
                str(error),
            )

            return

        selected_label = (
            EDITOR_THEME_LABELS[
                selected_theme
            ]
        )

        # Selecting the theme already painted in this process
        # cancels any previously pending change. The saved value
        # is updated above, but no restart prompt is necessary.
        if (
            selected_theme
            == self._active_editor_theme_name
        ):
            self.statusBar().showMessage(
                f"{selected_label} theme is active."
            )

            return

        theme_box = QMessageBox(
            self
        )

        theme_box.setWindowTitle(
            "Theme Change"
        )

        theme_box.setIcon(
            QMessageBox.Icon.Information
        )

        theme_box.setText(
            "The editor needs to restart to apply "
            f"the {selected_label} theme.\n\n"
            "Restart now?\n\n"
            "If you choose Later, the new theme "
            "will be applied the next time the "
            "editor is started."
        )

        later_button = theme_box.addButton(
            "Later",
            QMessageBox.ButtonRole.RejectRole,
        )

        restart_button = theme_box.addButton(
            "Restart Now",
            QMessageBox.ButtonRole.AcceptRole,
        )

        theme_box.setDefaultButton(
            later_button
        )

        theme_box.setEscapeButton(
            later_button
        )

        theme_box.exec()

        if (
            theme_box.clickedButton()
            is restart_button
        ):
            self._restart_after_close = True
            self.close()
            return

        self.statusBar().showMessage(
            f"{selected_label} theme will be "
            "applied on the next restart."
        )

    def _profile_lcd_setting_toggled(
        self,
        enabled: bool,
    ) -> None:
        try:
            self.manager.save_show_profile_activation_on_lcd(
                enabled
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "LCD Notification Settings",
                str(error),
            )

            return

        state_text = (
            "enabled"
            if enabled
            else "disabled"
        )

        self.statusBar().showMessage(
            "Profile activation LCD notification "
            f"{state_text}."
        )

    def _build_profiles_applications_page(
        self,
    ) -> QWidget:
        page = QWidget()

        page.setObjectName(
            "profilesApplicationsPage"
        )

        root = QVBoxLayout(page)

        root.setContentsMargins(
            20,
            20,
            20,
            20,
        )

        root.setSpacing(16)

        status_row = QHBoxLayout()

        status_row.setSpacing(
            20
        )

        profile_group = QGroupBox(
            "Profile Status"
        )

        profile_form = QFormLayout(
            profile_group
        )

        self.pa_editing_profile_value = QLabel()
        self.pa_runtime_profile_value = QLabel()
        self.pa_runtime_source_value = QLabel()
        self.pa_fallback_profile_value = QLabel()
        self.pa_locked_profile_value = QLabel()

        profile_form.addRow(
            "Open for editing:",
            self.pa_editing_profile_value,
        )

        profile_form.addRow(
            "Runtime active:",
            self.pa_runtime_profile_value,
        )

        profile_form.addRow(
            "Runtime source:",
            self.pa_runtime_source_value,
        )

        profile_form.addRow(
            "Saved fallback:",
            self.pa_fallback_profile_value,
        )

        profile_form.addRow(
            "Locked profile:",
            self.pa_locked_profile_value,
        )

        application_group = QGroupBox(
            "Application Switching"
        )

        application_form = QFormLayout(
            application_group
        )

        application_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        self.pa_window_class_value = QLabel()

        application_form.addRow(
            "Window class:",
            self.pa_window_class_value,
        )

        self.pa_automatic_switching_check = (
            QCheckBox()
        )

        self.pa_automatic_switching_check.toggled.connect(
            self._automatic_switching_toggled
        )

        automatic_switching_field = QHBoxLayout()

        automatic_switching_field.setContentsMargins(
            0,
            2,    # Vertical adjustment
            0,
            0,
        )

        automatic_switching_field.addWidget(
            self.pa_automatic_switching_check
        )

        automatic_switching_field.addStretch()

        application_form.addRow(
            "Automatic switching:",
            automatic_switching_field,
        )

        self.pa_lock_while_running_label = QLabel(
            "Lock while running:"
        )

        self.pa_lock_while_running_check = (
            QCheckBox()
        )

        self.pa_lock_while_running_check.toggled.connect(
            self._profile_lock_while_running_toggled
        )

        lock_while_running_field = QHBoxLayout()

        lock_while_running_field.setContentsMargins(
            0,
            2,
            0,
            0,
        )

        lock_while_running_field.addWidget(
            self.pa_lock_while_running_check
        )

        lock_while_running_field.addStretch()

        application_form.addRow(
            self.pa_lock_while_running_label,
            lock_while_running_field,
        )

        self.pa_detect_app_button = QPushButton(
            "Detect Active App"
        )

        self.pa_detect_app_button.clicked.connect(
            self._start_active_window_detection
        )

        self.pa_detect_app_button.setToolTip(
            (
                "Click, then focus the application "
                "you want to detect. Zombie will "
                "check the active window after "
                "3 seconds."
            )
        )

        self.pa_scan_steam_button = QPushButton(
            "Scan Steam Games"
        )

        self.pa_scan_steam_button.clicked.connect(
            self._scan_steam_games
        )

        application_button_row = QHBoxLayout()

        application_button_row.setSpacing(
            8
        )

        application_button_row.addWidget(
            self.pa_detect_app_button,
            1,
        )

        application_button_row.addWidget(
            self.pa_scan_steam_button,
            1,
        )

        application_form.addRow(
            application_button_row
        )

        self.pa_assign_app_button = QPushButton(
            "Assign Detected App to Editing Profile"
        )

        self.pa_assign_app_button.clicked.connect(
            self._assign_detected_application
        )

        application_form.addRow(
            self.pa_assign_app_button
        )

        status_row.addWidget(
            profile_group,
            1,
        )

        status_row.addWidget(
            application_group,
            1,
        )

        root.addLayout(status_row)

        onboard_profiles_group = QGroupBox(
            "Onboard Profiles"
        )

        onboard_profiles_group.setFixedHeight(
            195
        )

        onboard_profiles_layout = QVBoxLayout(
            onboard_profiles_group
        )

        onboard_profiles_layout.setSpacing(
            12
        )

        onboard_content_row = QHBoxLayout()

        onboard_content_row.setSpacing(
            24
        )

        onboard_summary_column = QVBoxLayout()

        onboard_summary_column.setSpacing(
            8
        )

        onboard_summary_column.setContentsMargins(
            6,
            -6,
            0,
            2,
        )

        onboard_summary_layout = QFormLayout()

        onboard_summary_layout.setSpacing(
            8
        )

        self.settings_onboard_device_value = QLabel(
            "Not loaded"
        )

        self.settings_onboard_memory_value = QLabel(
            "—"
        )

        self.settings_onboard_format_value = QLabel(
            "—"
        )

        self.settings_onboard_format_value.setMinimumHeight(
            22
        )

        self.settings_onboard_format_value.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        onboard_device_label = QLabel(
            "Device:"
        )

        onboard_device_label.setMinimumHeight(
            22
        )

        onboard_device_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        self.settings_onboard_device_value.setMinimumHeight(
            22
        )

        self.settings_onboard_device_value.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        onboard_summary_layout.addRow(
            onboard_device_label,
            self.settings_onboard_device_value,
        )

        onboard_memory_label = QLabel(
            "Memory used:"
        )

        onboard_memory_label.setMinimumHeight(
            22
        )

        onboard_memory_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        self.settings_onboard_memory_value.setMinimumHeight(
            22
        )

        self.settings_onboard_memory_value.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        onboard_summary_layout.addRow(
            onboard_memory_label,
            self.settings_onboard_memory_value,
        )

        onboard_format_label = QLabel(
            "Format:"
        )

        onboard_format_label.setMinimumHeight(
            22
        )

        onboard_format_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        onboard_summary_layout.addRow(
            onboard_format_label,
            self.settings_onboard_format_value,
        )

        onboard_summary_column.addLayout(
            onboard_summary_layout
        )

        onboard_summary_column.addSpacing(
            16
        )

        self.settings_onboard_refresh_button = QPushButton(
            "Refresh"
        )

        self.settings_onboard_refresh_button.setMinimumWidth(
            110
        )

        self.settings_onboard_refresh_button.setFixedHeight(
            34
        )

        self.settings_onboard_refresh_button.clicked.connect(
            self._refresh_onboard_profiles
        )

        self.settings_onboard_convert_button = QPushButton(
            "Convert Format"
        )

        self.settings_onboard_convert_button.setFixedHeight(
            34
        )

        self.settings_onboard_convert_button.clicked.connect(
            self._convert_onboard_format
        )

        onboard_summary_column.addWidget(
            self.settings_onboard_refresh_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

        onboard_summary_column.addWidget(
            self.settings_onboard_convert_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

        onboard_summary_column.addStretch()

        onboard_content_row.addLayout(
            onboard_summary_column
        )

        onboard_cards_row = QHBoxLayout()

        onboard_cards_row.setSpacing(
            0
        )

        onboard_cards_row.setContentsMargins(
            0,
            -10,
            0,
            8,
        )

        onboard_cards_row.addStretch()

        self.settings_onboard_profile_cards = []

        for slot_number in range(
            1,
            6,
        ):
            card = OnboardProfileCard(
                slot_number
            )

            self.settings_onboard_profile_cards.append(
                card
            )

            onboard_cards_row.addWidget(
                card,
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )

            onboard_cards_row.addStretch()

        onboard_content_row.addLayout(
            onboard_cards_row,
            1,
        )

        onboard_profiles_layout.addLayout(
            onboard_content_row
        )

        root.addWidget(
            onboard_profiles_group
        )

        self.settings_onboard_device_value.setText(
            "Loading..."
        )

        self.settings_onboard_memory_value.setText(
            "—"
        )

        self.settings_onboard_format_value.setText(
            "—"
        )

        self.settings_onboard_convert_button.setEnabled(
            False
        )

        for card in (
            self.settings_onboard_profile_cards
        ):
            card.set_loading_state()

        self.command_publisher.publish_request_onboard_profiles()

        rules_group = QGroupBox(
            "Application Rules"
        )

        rules_layout = QVBoxLayout(
            rules_group
        )

        self.pa_rules_list = QListWidget()

        self.pa_rules_list.setObjectName(
            "applicationRulesList"
        )

        self.pa_rules_list.setMinimumHeight(
            140
        )

        rules_layout.addWidget(
            self.pa_rules_list
        )

        self.pa_remove_rule_button = QPushButton(
            "Remove Selected Rule"
        )

        self.pa_remove_rule_button.clicked.connect(
            self._remove_selected_application_rule
        )

        self.pa_remove_rule_button.setMinimumWidth(
            190
        )

        remove_rule_row = QHBoxLayout()

        remove_rule_row.setContentsMargins(
            0,
            3,
            4,
            0,
        )

        remove_rule_row.addStretch()

        remove_rule_row.addWidget(
            self.pa_remove_rule_button
        )

        rules_layout.addLayout(
            remove_rule_row
        )

        root.addWidget(
            rules_group,
            1,
        )

        page.setStyleSheet(
            _theme_stylesheet("""
            QWidget#profilesApplicationsPage {
                background-color: $BG_PAGE;
                border: 1px solid $BORDER_PAGE;
                border-radius: 5px;
            }

            QGroupBox {
                background-color: $BG_GROUP;
                border: 1px solid $BORDER_TEAL;
                border-radius: 5px;
                margin-top: 12px;
                padding-top: 10px;
                font-weight: bold;
            }

            QGroupBox::title {
                color: $TEXT_ACCENT;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }

            QGroupBox QLabel,
            QGroupBox QCheckBox {
                background-color: transparent;
                border: none;
            }

            QPushButton#profileCard {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 5px;
                padding: 0;
                text-align: center;
            }

            QPushButton#profileCard:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
            }

            QPushButton#profileCardMenuButton {
                background-color: $BG_MENU_BUTTON;
                border: 1px solid $BORDER_TEAL_STRONG;
                border-radius: 3px;
                color: $TEXT_ACCENT_LIGHT;
                padding: 0;
            }

            QPushButton#profileCardMenuButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QPushButton#profileCardMenuButton:pressed {
                background-color: $BG_ACCENT;
            }

            QLabel#profileCardFallbackIcon {
                background-color: $BG_ICON;
                border: 1px solid $BORDER_TEAL_STRONG;
                border-radius: 16px;
                color: $TEXT_ACCENT;
                font-size: 16px;
                font-weight: bold;
            }

            QPushButton#profileCard:hover
            QLabel#profileCardFallbackIcon {
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QLabel#profileCardName {
                background-color: $BG_WINDOW;
                border: none;
                color: $TEXT_PROFILE;
                font-size: 9px;
                font-weight: bold;
            }

            QListWidget#applicationRulesList {
                background-color: $BLACK;
                border: 1px solid $BORDER_TEAL;
                color: $TEXT_PRIMARY_COOL;
            }

            QListWidget#applicationRulesList::item {
                padding: 4px 6px;
            }

            QListWidget#applicationRulesList::item:selected {
                background-color: $BG_ACCENT;
                color: $TEXT_BRIGHT;
            }

            QLabel:disabled,
            QCheckBox:disabled {
                color: $TEXT_DISABLED;
            }
            """)
        )

        self._refresh_profiles_applications_status()
        self._refresh_profiles_applications_rules()

        page.setMinimumHeight(
            page.sizeHint().height()
        )

        scroll = QScrollArea()

        scroll.setObjectName(
            "profilesApplicationsScroll"
        )

        scroll.setWidgetResizable(
            True
        )

        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        scroll.setWidget(
            page
        )

        scroll.setStyleSheet(
            """
            QScrollArea#profilesApplicationsScroll {
                background-color: transparent;
                border: none;
            }

            QScrollArea#profilesApplicationsScroll
            QScrollBar:vertical {
                width: 7px;
            }
            """
        )

        return scroll

    def _refresh_profiles_applications_rules(
        self,
    ) -> None:
        if not hasattr(
            self,
            "pa_rules_list",
        ):
            return

        self.pa_rules_list.clear()

        try:
            rules = (
                self.manager
                .get_application_rules()
            )

        except Exception as error:
            self.pa_rules_list.addItem(
                f"Unable to load rules: {error}"
            )
            return

        if not rules:
            self.pa_rules_list.addItem(
                "No application rules configured."
            )
            return

        for rule in rules:
            profile_filename = rule["profile"]

            try:
                display_name = (
                    self.manager
                    .get_profile_display_name(
                        profile_filename
                    )
                )

            except Exception:
                display_name = profile_filename

            if display_name == profile_filename:
                profile_text = profile_filename
            else:
                profile_text = (
                    f"{display_name} "
                    f"({profile_filename})"
                )

            self.pa_rules_list.addItem(
                f"{rule['window_class']}  →  "
                f"{profile_text}"
            )

    def _remove_selected_application_rule(
        self,
    ) -> None:
        selected_row = (
            self.pa_rules_list.currentRow()
        )

        try:
            rules = (
                self.manager
                .get_application_rules()
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Remove Application Rule",
                str(error),
            )
            return

        if not (
            0 <= selected_row < len(rules)
        ):
            QMessageBox.information(
                self,
                "Remove Application Rule",
                "Select a rule to remove.",
            )
            return

        selected_rule = rules[
            selected_row
        ]

        answer = QMessageBox.question(
            self,
            "Remove Application Rule?",
            (
                "Remove this application rule?\n\n"
                f"{selected_rule['window_class']}\n"
                f"→ {selected_rule['profile']}"
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )

        if (
            answer
            != QMessageBox.StandardButton.Yes
        ):
            return

        updated_rules = [
            rule
            for index, rule in enumerate(rules)
            if index != selected_row
        ]

        try:
            self.manager.set_application_rules(
                updated_rules
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Remove Application Rule",
                str(error),
            )
            return

        self._refresh_profiles_applications_rules()
        self._refresh_profile_strip()

        self.statusBar().showMessage(
            "Removed application rule for "
            f"{selected_rule['window_class']}."
        )

    def _refresh_profiles_applications_status(
        self,
    ) -> None:
        if not hasattr(
            self,
            "pa_editing_profile_value",
        ):
            return

        def profile_text(
            filename: str | None,
        ) -> str:
            if not filename:
                return "None"

            try:
                display_name = (
                    self.manager
                    .get_profile_display_name(
                        filename
                    )
                )

            except Exception:
                return filename

            if display_name == filename:
                return filename

            return (
                f"{display_name} "
                f"({filename})"
            )

        editing_profile = (
            self.manager.active_profile_name
        )

        saved_fallback = (
            self.manager.get_fallback_profile()
        )

        locked_profile = (
            self.runtime_locked_profile
        )

        if not self._startup_runtime_profile_synced:
            try:
                locked_profile = (
                    self.manager
                    .get_locked_profile()
                )
            except Exception:
                locked_profile = None

        source_names = {
            "automatic": "Automatic",
            "manual": "Fallback",
            "locked": "Locked",
            "temporary": "Temporary",
            "application_locked": "Application Lock",
        }

        source_text = source_names.get(
            self.runtime_profile_source,
            "Waiting for driver",
        )

        try:
            automatic_enabled = (
                self.manager
                .get_automatic_profile_switching()
            )
        except Exception:
            automatic_enabled = False

        self.pa_editing_profile_value.setText(
            profile_text(editing_profile)
        )

        self.pa_runtime_profile_value.setText(
            profile_text(
                self.runtime_active_profile
            )
            if self.runtime_active_profile
            else "Waiting for driver"
        )

        self.pa_runtime_source_value.setText(
            source_text
        )

        self.pa_fallback_profile_value.setText(
            profile_text(saved_fallback)
        )

        self.pa_locked_profile_value.setText(
            profile_text(locked_profile)
        )

        self.pa_automatic_switching_check.blockSignals(
            True
        )

        self.pa_automatic_switching_check.setChecked(
            automatic_enabled
        )

        self.pa_automatic_switching_check.blockSignals(
            False
        )

        self.pa_lock_while_running_label.setEnabled(
            automatic_enabled
        )

        self.pa_lock_while_running_check.setEnabled(
            automatic_enabled
        )

        try:
            lock_while_running = (
                self.manager
                .get_profile_lock_while_running()
            )

        except Exception:
            lock_while_running = False

        self.pa_lock_while_running_check.blockSignals(
            True
        )

        self.pa_lock_while_running_check.setChecked(
            lock_while_running
        )

        self.pa_lock_while_running_check.blockSignals(
            False
        )

        window_class_display = (
            self.runtime_window_class
        )

        if (
            window_class_display
            == "zombie-control-center"
        ):
            window_class_display = (
                "G13 Zombie Control Center"
            )

        self.pa_window_class_value.setText(
            window_class_display
            or (
                "Waiting for active window."
                if automatic_enabled
                else "Automatic Switching is disabled."
            )
        )

    def _build_bottom_navigation(
        self,
    ) -> QWidget:
        navigation = QWidget()

        navigation.setObjectName(
            "bottomNavigation"
        )

        layout = QHBoxLayout(navigation)

        layout.setContentsMargins(
            5,
            4,
            5,
            4,
        )

        layout.setSpacing(5)

        sections = (
            "Assignments",
            "Backlight && LCD",
            "Joystick",
            "Profiles && Applications",
            "Settings",
        )

        self.section_navigation_buttons: list[
            QPushButton
        ] = []

        for page_index, section_name in enumerate(
            sections
        ):
            button = QPushButton(
                section_name
            )

            button.setCheckable(True)
            button.setAutoExclusive(True)

            button.setMinimumHeight(34)

            button.clicked.connect(
                lambda checked,
                index=page_index: (
                    self._set_main_page(index)
                )
            )

            layout.addWidget(
                button,
                1,
            )

            self.section_navigation_buttons.append(
                button
            )

        self.section_navigation_buttons[
            0
        ].setChecked(True)

        navigation.setStyleSheet(
            _theme_stylesheet("""
            QWidget#bottomNavigation {
                background-color: $BG_SHELL;
                border: 1px solid $BORDER_SHELL;
                border-radius: 5px;
            }

            QPushButton {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_NAV_BUTTON;
                border-radius: 3px;
                color: $TEXT_NAV;
                padding: 5px 12px;
            }

            QPushButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $BORDER_NAV_HOVER;
                color: $TEXT_BRIGHT;
            }

            QPushButton:checked {
                background-color: $BG_ACCENT;
                border: 1px solid $ACCENT;
                color: $TEXT_BRIGHT;
                font-weight: bold;
            }
            """)
        )

        return navigation

    def _set_main_page(
        self,
        page_index: int,
    ) -> None:
        if not (
            0
            <= page_index
            < self.main_pages.count()
        ):
            return

        self.main_pages.setCurrentIndex(
            page_index
        )

    def _automatic_switching_toggled(
        self,
        enabled: bool,
    ) -> None:
        try:
            self.manager.set_automatic_profile_switching(
                enabled
            )

        except Exception as error:
            self._refresh_profiles_applications_status()

            QMessageBox.critical(
                self,
                "Automatic Switching",
                str(error),
            )
            return

        self._refresh_profiles_applications_status()

        state = (
            "enabled"
            if enabled
            else "disabled"
        )

        self.statusBar().showMessage(
            f"Automatic profile switching {state}."
        )

    def _profile_lock_while_running_toggled(
        self,
        enabled: bool,
    ) -> None:
        try:
            self.manager.set_profile_lock_while_running(
                enabled
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Profile Application Lock",
                str(error),
            )

            self._refresh_profiles_applications_status()
            return

        self._set_profile_dirty(
            True
        )

        state = (
            "enabled"
            if enabled
            else "disabled"
        )

        self.statusBar().showMessage(
            "Lock while application is running "
            f"{state}."
        )

    def _scan_steam_games(
        self,
    ) -> None:
        try:
            games = scan_steam_games()

        except Exception as error:
            QMessageBox.critical(
                self,
                "Scan Steam Games",
                str(error),
            )
            return

        if not games:
            QMessageBox.information(
                self,
                "Scan Steam Games",
                (
                    "No installed Steam games "
                    "were found."
                ),
            )
            return

        dialog = QDialog(
            self
        )

        dialog.setWindowTitle(
            "Installed Steam Games"
        )

        dialog.resize(
            520,
            420,
        )

        root = QVBoxLayout(
            dialog
        )

        search_edit = QLineEdit()

        search_edit.setPlaceholderText(
            "Search installed games..."
        )

        root.addWidget(
            search_edit
        )

        game_list = QListWidget()

        for game in games:
            game_list.addItem(
                game.name
            )

        if game_list.count() > 0:
            game_list.setCurrentRow(
                0
            )

            row_height = (
                game_list.sizeHintForRow(
                    0
                )
            )

            if row_height <= 0:
                row_height = 24

            visible_rows = min(
                12,
                game_list.count(),
            )

            game_list.setMinimumHeight(
                (
                    row_height
                    * visible_rows
                )
                + 8
            )

        root.addWidget(
            game_list
        )

        button_row = QHBoxLayout()

        button_row.addStretch()

        cancel_button = QPushButton(
            "Cancel"
        )

        select_button = QPushButton(
            "Select"
        )

        select_button.setDefault(
            True
        )

        button_row.addWidget(
            cancel_button
        )

        button_row.addWidget(
            select_button
        )

        root.addLayout(
            button_row
        )

        def update_selection_button(
        ) -> None:
            item = game_list.currentItem()

            select_button.setEnabled(
                item is not None
                and not item.isHidden()
            )

        def filter_games(
            text: str,
        ) -> None:
            search_text = (
                text
                .strip()
                .casefold()
            )

            first_visible_row = None

            for row in range(
                game_list.count()
            ):
                item = game_list.item(
                    row
                )

                hidden = (
                    search_text
                    not in item.text()
                    .casefold()
                )

                item.setHidden(
                    hidden
                )

                if (
                    not hidden
                    and first_visible_row
                    is None
                ):
                    first_visible_row = row

            if first_visible_row is None:
                game_list.clearSelection()
                game_list.setCurrentRow(
                    -1
                )

            else:
                game_list.setCurrentRow(
                    first_visible_row
                )

            update_selection_button()

        search_edit.textChanged.connect(
            filter_games
        )

        game_list.currentRowChanged.connect(
            lambda _row:
            update_selection_button()
        )

        game_list.itemDoubleClicked.connect(
            lambda _item:
            dialog.accept()
        )

        cancel_button.clicked.connect(
            dialog.reject
        )

        select_button.clicked.connect(
            dialog.accept
        )

        update_selection_button()

        result = dialog.exec()

        if (
            result
            != QDialog.DialogCode.Accepted
        ):
            return

        selected_row = (
            game_list.currentRow()
        )

        if not (
            0
            <= selected_row
            < len(games)
        ):
            return

        selected_game = games[
            selected_row
        ]

        self.detected_window_class = (
            selected_game.window_class
        )

        self.detected_application_name = (
            selected_game.name
        )

        self.statusBar().showMessage(
            (
                "Selected Steam game: "
                f"{selected_game.name}"
            )
        )

    def _start_active_window_detection(
        self,
    ) -> None:
        self.pa_detect_app_button.setText(
            "Focus App Now..."
        )

        self.pa_detect_app_button.setEnabled(
            False
        )

        QTimer.singleShot(
            3000,
            self._finish_active_window_detection,
        )

    def _finish_active_window_detection(
        self,
    ) -> None:
        self.pa_detect_app_button.setText(
            "Detect Active App"
        )

        self.pa_detect_app_button.setEnabled(
            True
        )

        window = get_active_window()

        if window is None:
            desktop = os.environ.get(
                "XDG_CURRENT_DESKTOP",
                "",
            ).casefold()

            session_type = os.environ.get(
                "XDG_SESSION_TYPE",
                "",
            ).casefold()

            if (
                "gnome" in desktop
                and session_type == "wayland"
            ):
                message = (
                    "No active window was detected.\n\n"
                    "GNOME/Wayland application detection "
                    "requires the Focused Window D-Bus "
                    "GNOME Shell extension."
                )

            else:
                message = (
                    "No active window was detected."
                )

            QMessageBox.warning(
                self,
                "Active Application",
                message,
            )
            return

        if not window.window_class:
            QMessageBox.warning(
                self,
                "Active Application",
                (
                    "The active window did not report "
                    "a window class."
                ),
            )
            return

        self.detected_window_class = (
            window.window_class
        )

        self.detected_application_name = (
            window.title
            or window.window_class
        )

        message = (
            f"Window class:\n"
            f"{window.window_class or '(none)'}\n\n"
            f"Title:\n"
            f"{window.title or '(none)'}\n\n"
            f"PID:\n"
            f"{window.pid if window.pid is not None else '(unknown)'}"
        )

        QMessageBox.information(
            self,
            "Active Application",
            message,
        )

        self.statusBar().showMessage(
            "Detected active application: "
            f"{window.window_class or '(no class)'}"
        )

    def _assign_detected_application(
        self,
    ) -> None:
        window_class = (
            self.detected_window_class
        )

        if not window_class:
            QMessageBox.information(
                self,
                "Assign Application",
                (
                    "Detect an active application "
                    "or select a Steam game first."
                ),
            )
            return

        profile = (
            self.manager.active_profile_name
        )

        if not profile:
            QMessageBox.information(
                self,
                "Assign Application",
                "No profile is open for editing.",
            )
            return

        application_name = (
            getattr(
                self,
                "detected_application_name",
                None,
            )
            or window_class
        )

        def profile_display_name(
            filename: str,
        ) -> str:
            try:
                display_name = (
                    self.manager
                    .get_profile_display_name(
                        filename
                    )
                )

            except Exception:
                return filename

            return (
                display_name
                or filename
            )

        try:
            rules = (
                self.manager
                .get_application_rules()
            )

            existing_rule = None

            normalized_class = (
                window_class
                .strip()
                .casefold()
            )

            for rule in rules:
                rule_class = (
                    rule["window_class"]
                    .strip()
                    .casefold()
                )

                if (
                    rule_class
                    == normalized_class
                ):
                    existing_rule = rule
                    break

            if existing_rule is not None:
                existing_profile = (
                    existing_rule["profile"]
                )

                if existing_profile == profile:
                    QMessageBox.information(
                        self,
                        "Already Assigned",
                        (
                            f"{application_name}\n\n"
                            "This application is already "
                            "assigned to:\n"
                            f"{profile_display_name(profile)}"
                        ),
                    )

                    self.statusBar().showMessage(
                        (
                            f"{application_name} is already "
                            "assigned to "
                            f"{profile_display_name(profile)}."
                        )
                    )
                    return

                conflict_box = QMessageBox(
                    self
                )

                conflict_box.setWindowTitle(
                    "Application Already Assigned"
                )

                conflict_box.setIcon(
                    QMessageBox.Icon.Warning
                )

                conflict_box.setText(
                    (
                        f"{application_name}\n\n"
                        "This application is already "
                        "assigned to:\n"
                        f"{profile_display_name(existing_profile)}"
                        "\n\n"
                        "Move it to:\n"
                        f"{profile_display_name(profile)}?"
                    )
                )

                cancel_button = (
                    conflict_box.addButton(
                        "Cancel",
                        QMessageBox.ButtonRole.RejectRole,
                    )
                )

                move_button = (
                    conflict_box.addButton(
                        "Move",
                        QMessageBox.ButtonRole.AcceptRole,
                    )
                )

                conflict_box.setDefaultButton(
                    cancel_button
                )

                conflict_box.setEscapeButton(
                    cancel_button
                )

                conflict_box.exec()

                if (
                    conflict_box.clickedButton()
                    is not move_button
                ):
                    return

                existing_rule[
                    "window_class"
                ] = window_class

                existing_rule[
                    "profile"
                ] = profile

            else:
                rules.append(
                    {
                        "window_class": window_class,
                        "profile": profile,
                    }
                )

            self.manager.set_application_rules(
                rules
            )

            self._refresh_profiles_applications_rules()
            self._refresh_profile_strip()

        except Exception as error:
            QMessageBox.critical(
                self,
                "Assign Application",
                str(error),
            )
            return

        QMessageBox.information(
            self,
            "Assign Application",
            (
                f"{application_name}\n\n"
                "Assigned to:\n"
                f"{profile_display_name(profile)}"
            ),
        )

        self.statusBar().showMessage(
            (
                f"Assigned {application_name} to "
                f"{profile_display_name(profile)}."
            )
        )

    def _import_logitech_profile(self):
        selected_file, _ = QFileDialog.getOpenFileName(
            self,
            "Import Logitech Profile",
            str(Path.home()),
            (
                "Logitech XML Profiles (*.xml);;"
                "All Files (*)"
            ),
        )

        if not selected_file:
            return

        input_path = Path(selected_file)

        suggested_filename = (
            f"{input_path.stem}.json"
        )

        filename, accepted = QInputDialog.getText(
            self,
            "Import Logitech Profile",
            "Profile filename:",
            QLineEdit.EchoMode.Normal,
            suggested_filename,
        )

        if not accepted:
            return

        filename = filename.strip()

        if not filename:
            return

        if not filename.lower().endswith(".json"):
            filename += ".json"

        # Do not allow paths such as ../profile.json.
        if Path(filename).name != filename:
            QMessageBox.critical(
                self,
                "Import Profile",
                (
                    "The profile filename cannot "
                    "contain directories."
                ),
            )
            return

        output_path = (
            self.manager.profiles_dir
            / filename
        )

        if output_path.exists():
            answer = QMessageBox.question(
                self,
                "Replace Existing Profile?",
                (
                    f"A profile named '{filename}' "
                    "already exists.\n\n"
                    "Replace it?"
                ),
                (
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                ),
                QMessageBox.StandardButton.No,
            )

            if (
                answer
                != QMessageBox.StandardButton.Yes
            ):
                return

        try:
            convert_profile(
                input_path,
                output_path,
            )

            self.manager.set_active_profile(
                filename,
                persist=False,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Import Profile",
                str(error),
            )
            return

        self.selected_layer = 1
        self.selected_button = None

        self.layer_tabs.setCurrentIndex(0)

        self._refresh_profile_list()
        self._refresh_all_buttons()
        self._reset_binding_editor()
        self._load_joystick_settings()

        self.statusBar().showMessage(
            f"Imported and loaded {filename}"
        )

        QMessageBox.information(
            self,
            "Import Complete",
            (
                "The Logitech profile was imported "
                f"successfully as:\n\n{filename}"
            ),
        )

    def _build_device_panel(self) -> QWidget:
        container = QWidget()

        container.setObjectName(
            "g13DevicePanel"
        )

        layout = QVBoxLayout(container)

        layout.setContentsMargins(
            18,
            16,
            18,
            18,
        )

        layout.setSpacing(12)

        hardware_group = QGroupBox(
            "LCD Controls"
        )

        hardware_layout = QHBoxLayout(
            hardware_group
        )

        hardware_layout.setSpacing(8)

        hardware_controls = (
            (
                "Up",
                "LCD1 — previous profile",
            ),
            (
                "Down",
                "LCD2 — next profile",
            ),
            (
                "Select",
                (
                    "LCD3 — temporarily activate "
                    "selected profile"
                ),
            ),
            (
                "Lock",
                (
                    "LCD4 — lock, unlock, or "
                    "transfer profile lock"
                ),
            ),
        )

        hardware_layout.addStretch()

        for control_name, description in (
            hardware_controls
        ):
            control_label = QLabel(
                control_name
            )

            control_label.setObjectName(
                "hardwareControlLabel"
            )

            control_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )

            control_label.setFixedSize(
                68,
                32,
            )

            control_label.setToolTip(
                f"{description}\n\n"
                "Physical device control; "
                "not assignable."
            )

            hardware_layout.addWidget(
                control_label
            )

        hardware_layout.addStretch()

        hardware_group.setStyleSheet(
            _theme_stylesheet("""
            QLabel#hardwareControlLabel {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $BORDER_TEAL;
                border-radius: 4px;
                color: $TEXT_ACCENT;
                font-size: 10px;
                font-weight: bold;
            }
            """)
        )

        layout.addWidget(
            hardware_group
        )

        self.layer_tabs = QTabWidget()
        self.layer_tabs.setObjectName(
            "mStateTabs"
        )

        self.layer_tabs.setDocumentMode(
            True
        )

        self.layer_tabs.tabBar().setDrawBase(False)

        self.layer_tabs.setStyleSheet(
            _theme_stylesheet("""
            QTabWidget#mStateTabs::pane {
                background-color: $BG_TAB_PANE;
                border: 1px solid $BORDER_TEAL;
                border-radius: 6px;
                top: -1px;
                margin-right: -1px;
            }

            QTabWidget#mStateTabs QTabBar::tab {
                background-color: $BG_TAB_INACTIVE;
                border: 1px solid $BORDER_TAB;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                color: $TEXT_TAB;
                min-width: 72px;
                padding: 8px 18px;
                margin-right: 4px;
                font-weight: bold;
            }

            QTabWidget#mStateTabs QTabBar::tab:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT;
                color: $TEXT_BRIGHT;
            }

            QTabWidget#mStateTabs QTabBar::tab:selected {
                background-color: $BG_ACCENT;
                border: 2px solid $ACCENT;
                border-bottom: none;
                color: $TEXT_BRIGHT;
            }
            """)
        )

        for layer in (1, 2, 3):
            self.layer_tabs.addTab(
                self._build_layer_tab(layer),
                f"M{layer}",
            )

        self.layer_tabs.currentChanged.connect(
            self._layer_tab_changed
        )

        tab_row = QHBoxLayout()

        tab_row.setContentsMargins(
            3,
            2,
            0,
            0,
        )

        tab_row.addWidget(
            self.layer_tabs
        )

        layout.addLayout(
            tab_row
        )

        container.setStyleSheet(
            _theme_stylesheet("""
            QWidget#g13DevicePanel {
                background-color: $BG_DEVICE_PANEL;
                border: 2px solid $BORDER_DEVICE;
                border-radius: 24px;
            }
            """)
        )

        return container

    def _build_backlight_page(
        self,
    ) -> QWidget:
        page = QWidget()

        page.setObjectName(
            "backlightPage"
        )

        root = QVBoxLayout(page)

        root.setContentsMargins(
            16,
            12,
            16,
            12,
        )

        root.setSpacing(
            8
        )

        root.addWidget(
            self._build_backlight_panel(),
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        root.addStretch()

        page.setStyleSheet(
            _theme_stylesheet("""
            QWidget#backlightPage {
                background-color: $BG_PAGE;
                border: 1px solid $BORDER_PAGE;
                border-radius: 5px;
            }
            """)
        )

        return page

    def _build_backlight_panel(
        self,
    ) -> QWidget:
        group = QGroupBox()

        group.setFixedSize(
            960,
            400,
        )

        layout = QHBoxLayout(
            group
        )

        layout.setContentsMargins(
            24,
            14,
            24,
            14,
        )

        layout.setSpacing(
            18
        )

        # -----------------------------------------------------
        # LCD preview and M-state selectors
        # -----------------------------------------------------

        left_panel = QWidget()

        left_panel.setFixedWidth(
            450
        )

        left_layout = QVBoxLayout(
            left_panel
        )

        left_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        left_layout.setSpacing(
            10
        )

        left_layout.addStretch()

        dice_grid = QGridLayout()

        dice_grid.setContentsMargins(
            50,
            0,
            50,
            0,
        )

        dice_grid.setHorizontalSpacing(
            12
        )

        dice_grid.setVerticalSpacing(
            24
        )

        self.backlight_selected_layer = 1

        self.backlight_color_buttons: dict[
            int,
            BacklightLensButton,
        ] = {}

        for layer in (1, 2, 3):
            die = BacklightLensButton(
                layer
            )

            die.clicked.connect(
                lambda checked=False,
                selected_layer=layer:
                self._select_backlight_layer(
                    selected_layer
                )
            )

            self.backlight_color_buttons[
                layer
            ] = die

            if layer == 1:
                dice_grid.addWidget(
                    die,
                    0,
                    0,
                    Qt.AlignmentFlag.AlignCenter,
                )

            elif layer == 2:
                dice_grid.addWidget(
                    die,
                    0,
                    1,
                    Qt.AlignmentFlag.AlignCenter,
                )

            else:
                dice_grid.addWidget(
                    die,
                    1,
                    0,
                    1,
                    2,
                    Qt.AlignmentFlag.AlignHCenter,
                )

        left_layout.addLayout(
            dice_grid
        )

        left_layout.addStretch()

        layout.addWidget(
            left_panel
        )

        # -----------------------------------------------------
        # Permanent color editor
        # -----------------------------------------------------

        color_editor = QWidget()

        color_editor.setFixedWidth(
            420
        )

        editor_layout = QVBoxLayout(
            color_editor
        )

        editor_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        editor_layout.setSpacing(
            8
        )

        self.backlight_hue_wheel = HueWheel(
            QColor(
                0,
                0,
                0,
            )
        )

        self.backlight_hue_wheel.RING_WIDTH = (
            25.5
        )

        self.backlight_hue_wheel.OUTER_MARGIN = (
            9.0
        )

        self.backlight_hue_wheel.setFixedSize(
            270,
            270,
        )

        self.backlight_hue_wheel.colorChanged.connect(
            self._backlight_wheel_color_changed
        )

        editor_layout.addWidget(
            self.backlight_hue_wheel,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        # -----------------------------------------------------
        # Brightness
        # -----------------------------------------------------

        brightness_row = QHBoxLayout()

        brightness_row.setSpacing(
            10
        )

        brightness_label = QLabel(
            "Brightness:"
        )

        self.backlight_brightness_slider = QSlider(
            Qt.Orientation.Horizontal
        )

        self.backlight_brightness_slider.setRange(
            0,
            255,
        )

        self.backlight_brightness_slider.setFixedWidth(
            270
        )

        self.backlight_brightness_slider.valueChanged.connect(
            self._backlight_brightness_changed
        )

        self.backlight_brightness_value = QLabel(
            "0"
        )

        self.backlight_brightness_value.setFixedWidth(
            36
        )

        self.backlight_brightness_value.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        brightness_row.addWidget(
            brightness_label
        )

        brightness_row.addWidget(
            self.backlight_brightness_slider
        )

        brightness_row.addWidget(
            self.backlight_brightness_value
        )

        editor_layout.addLayout(
            brightness_row
        )

        # -----------------------------------------------------
        # Hex value
        # -----------------------------------------------------

        hex_row = QHBoxLayout()

        hex_row.setSpacing(
            10
        )

        hex_label = QLabel(
            "Hex:"
        )

        self.backlight_hex_edit = QLineEdit()

        self.backlight_hex_edit.setMaxLength(
            7
        )

        self.backlight_hex_edit.setFixedWidth(
            130
        )

        self.backlight_hex_edit.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.backlight_hex_edit.setPlaceholderText(
            "#RRGGBB"
        )

        self.backlight_hex_edit.editingFinished.connect(
            self._backlight_hex_changed
        )

        hex_row.addStretch()

        hex_row.addWidget(
            hex_label
        )

        hex_row.addWidget(
            self.backlight_hex_edit
        )

        hex_row.addStretch()

        editor_layout.addLayout(
            hex_row
        )

        layout.addWidget(
            color_editor
        )

        # -----------------------------------------------------
        # Debounced profile save
        # -----------------------------------------------------

        self._backlight_updating_controls = False

        self._backlight_pending_layer: int | None = None

        self._backlight_pending_color: QColor | None = None

        self.backlight_save_timer = QTimer(
            self
        )

        self.backlight_save_timer.setSingleShot(
            True
        )

        self.backlight_save_timer.setInterval(
            250
        )

        self.backlight_save_timer.timeout.connect(
            self._commit_pending_backlight_color
        )

        self._load_backlight_editor_color(
            1
        )

        return group

    def _select_backlight_layer(
        self,
        layer: int,
    ) -> None:
        if layer not in {
            1,
            2,
            3,
        }:
            return

        if self.backlight_save_timer.isActive():
            self.backlight_save_timer.stop()

            self._commit_pending_backlight_color()

        self.backlight_selected_layer = layer

        for (
            die_layer,
            die,
        ) in self.backlight_color_buttons.items():
            die.set_selected(
                die_layer == layer
            )

        self._load_backlight_editor_color(
            layer
        )

        self.statusBar().showMessage(
            f"M{layer} selected for backlight editing."
        )

    def _load_backlight_editor_color(
        self,
        layer: int,
    ) -> None:
        try:
            colors = (
                self.manager
                .get_backlight_colors()
            )

        except Exception as error:
            self.statusBar().showMessage(
                f"Backlight color error: {error}"
            )

            return

        if layer not in colors:
            return

        red, green, blue = colors[
            layer
        ]

        color = QColor(
            red,
            green,
            blue,
        )

        self._backlight_updating_controls = True

        try:

            self.backlight_hue_wheel.setColor(
                color,
                emit_signal=False,
            )

            self.backlight_brightness_slider.setValue(
                color.value()
            )

            self.backlight_brightness_value.setText(
                str(
                    color.value()
                )
            )

            self.backlight_hex_edit.setText(
                color.name(
                    QColor.NameFormat.HexRgb
                ).upper()
            )

        finally:
            self._backlight_updating_controls = False


    def _backlight_wheel_color_changed(
        self,
        color: QColor,
    ) -> None:
        if self._backlight_updating_controls:
            return

        color = QColor(
            color
        )

        self._backlight_updating_controls = True

        try:
            self.backlight_brightness_slider.setValue(
                color.value()
            )

            self.backlight_brightness_value.setText(
                str(
                    color.value()
                )
            )

            self.backlight_hex_edit.setText(
                color.name(
                    QColor.NameFormat.HexRgb
                ).upper()
            )

        finally:
            self._backlight_updating_controls = False

        self._preview_backlight_color(
            color
        )


    def _backlight_brightness_changed(
        self,
        value: int,
    ) -> None:
        if self._backlight_updating_controls:
            return

        color = (
            self.backlight_hue_wheel
            .color()
        )

        hue = color.hsvHue()

        if hue < 0:
            hue = 0

        color.setHsv(
            hue,
            color.hsvSaturation(),
            value,
        )

        self._backlight_updating_controls = True

        try:
            self.backlight_hue_wheel.setColor(
                color,
                emit_signal=False,
            )

            self.backlight_brightness_value.setText(
                str(value)
            )

            self.backlight_hex_edit.setText(
                color.name(
                    QColor.NameFormat.HexRgb
                ).upper()
            )

        finally:
            self._backlight_updating_controls = False

        self._preview_backlight_color(
            color
        )


    def _backlight_hex_changed(
        self,
    ) -> None:
        if self._backlight_updating_controls:
            return

        text = (
            self.backlight_hex_edit
            .text()
            .strip()
        )

        if not text.startswith("#"):
            text = f"#{text}"

        color = QColor(
            text
        )

        if not color.isValid():
            self._load_backlight_editor_color(
                self.backlight_selected_layer
            )

            return

        self._backlight_updating_controls = True

        try:
            self.backlight_hue_wheel.setColor(
                color,
                emit_signal=False,
            )

            self.backlight_brightness_slider.setValue(
                color.value()
            )

            self.backlight_brightness_value.setText(
                str(
                    color.value()
                )
            )

            self.backlight_hex_edit.setText(
                color.name(
                    QColor.NameFormat.HexRgb
                ).upper()
            )

        finally:
            self._backlight_updating_controls = False

        self._preview_backlight_color(
            color
        )


    def _preview_backlight_color(
        self,
        color: QColor,
    ) -> None:
        layer = (
            self.backlight_selected_layer
        )

        die = (
            self.backlight_color_buttons.get(
                layer
            )
        )

        if die is not None:
            die.set_color(
                color
            )

        self._backlight_pending_layer = layer

        self._backlight_pending_color = QColor(
            color
        )

        self.backlight_save_timer.start()


    def _commit_pending_backlight_color(
        self,
    ) -> None:
        layer = self._backlight_pending_layer

        color = self._backlight_pending_color

        if (
            layer is None
            or color is None
        ):
            return

        try:
            self.manager.set_backlight_color(
                layer,
                color.red(),
                color.green(),
                color.blue(),
            )

            self.manager.save_active_profile_backlight()

        except Exception as error:
            QMessageBox.critical(
                self,
                "Backlight Color",
                str(error),
            )

            self._load_backlight_editor_color(
                layer
            )

        else:
            self.statusBar().showMessage(
                f"M{layer} backlight color saved."
            )

        finally:
            self._backlight_pending_layer = None

            self._backlight_pending_color = None


    def _refresh_backlight_buttons(
        self,
    ) -> None:
        try:
            colors = (
                self.manager
                .get_backlight_colors()
            )

        except Exception as error:
            self.statusBar().showMessage(
                f"Backlight color error: {error}"
            )

            return

        for (
            layer,
            color,
        ) in colors.items():
            die = (
                self.backlight_color_buttons.get(
                    layer
                )
            )

            if die is None:
                continue

            red, green, blue = color

            die.set_color(
                QColor(
                    red,
                    green,
                    blue,
                )
            )

            die.set_selected(
                layer
                == self.backlight_selected_layer
            )

        if hasattr(
            self,
            "backlight_hue_wheel",
        ):
            self._load_backlight_editor_color(
                self.backlight_selected_layer
            )

    def _build_joystick_page(
        self,
    ) -> QWidget:
        page = QWidget()

        page.setObjectName(
            "joystickPage"
        )

        root = QVBoxLayout(page)

        root.setContentsMargins(
            16,
            12,
            16,
            12,
        )

        root.setSpacing(
            8
        )

        root.addWidget(
            self._build_joystick_settings_panel()
        )

        root.addStretch()

        page.setStyleSheet(
            _theme_stylesheet("""
            QWidget#joystickPage {
                background-color: $BG_PAGE;
                border: 1px solid $BORDER_PAGE;
                border-radius: 5px;
            }

            QLabel#joystickHelpText {
                color: $TEXT_HELP_JOYSTICK;
                font-size: 10px;
                font-weight: normal;
            }
            """)
        )

        return page

    def _build_joystick_settings_panel(
        self,
    ) -> QWidget:
        container = QWidget()

        container.setObjectName(
            "joystickSettingsContainer"
        )

        container_layout = QHBoxLayout(
            container
        )

        container_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        container_layout.addStretch()

        group = QGroupBox()

        group.setFixedSize(
            960,
            400,
        )

        root = QVBoxLayout(group)

        root.setContentsMargins(
            18,
            20,
            18,
            14,
        )

        root.setSpacing(14)

        # -----------------------------------------------------
        # Mode
        # -----------------------------------------------------

        mode_row = QHBoxLayout()

        mode_row.setContentsMargins(
            14,
            0,
            14,
            0,
        )

        mode_label = QLabel(
            "Mode:"
        )

        mode_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter
        )

        self.joystick_mode_combo = QComboBox()

        self.joystick_mode_combo.addItem(
            "Key Bindings (G26–G29)",
            "bindings",
        )

        self.joystick_mode_combo.addItem(
            "Mouse Cursor",
            "mouse",
        )

        self.joystick_mode_combo.currentIndexChanged.connect(
            self._joystick_mode_changed
        )

        self.joystick_mode_combo.setMaximumWidth(
            360
        )

        mode_row.addStretch()

        mode_row.addWidget(
            mode_label
        )

        mode_row.addSpacing(
            8
        )

        mode_row.addWidget(
            self.joystick_mode_combo
        )

        mode_row.addStretch()

        root.addLayout(
            mode_row
        )

        # -----------------------------------------------------
        # Setting controls
        # -----------------------------------------------------

        self.start_deadzone_spin = (
            QDoubleSpinBox()
        )

        self.start_deadzone_spin.setRange(
            0.01,
            0.95,
        )

        self.start_deadzone_spin.setDecimals(2)
        self.start_deadzone_spin.setSingleStep(
            0.01
        )

        self.stop_deadzone_spin = (
            QDoubleSpinBox()
        )

        self.stop_deadzone_spin.setRange(
            0.00,
            0.94,
        )

        self.stop_deadzone_spin.setDecimals(2)
        self.stop_deadzone_spin.setSingleStep(
            0.01
        )

        self.left_gain_spin = (
            self._make_gain_spinbox()
        )

        self.right_gain_spin = (
            self._make_gain_spinbox()
        )

        self.up_gain_spin = (
            self._make_gain_spinbox()
        )

        self.down_gain_spin = (
            self._make_gain_spinbox()
        )

        self.center_alpha_spin = (
            QDoubleSpinBox()
        )

        self.center_alpha_spin.setRange(
            0.0001,
            1.0,
        )

        self.center_alpha_spin.setDecimals(4)

        self.center_alpha_spin.setSingleStep(
            0.001
        )

        compact_spinboxes = (
            self.start_deadzone_spin,
            self.stop_deadzone_spin,
            self.center_alpha_spin,
            self.left_gain_spin,
            self.right_gain_spin,
            self.up_gain_spin,
            self.down_gain_spin,
        )

        for spinbox in compact_spinboxes:
            spinbox.setFixedWidth(145)
            spinbox.setFixedHeight(34)

            spinbox.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )

        self.joystick_mode_combo.setToolTip(
            "Key Bindings uses G26-G29 as directional buttons. "
            "Mouse Cursor uses the joystick to move the pointer."
        )

        self.start_deadzone_spin.setToolTip(
            "How far the stick must move away from center "
            "before movement becomes active."
        )

        self.stop_deadzone_spin.setToolTip(
            "How far the stick must return toward center "
            "before active movement stops. This must be "
            "lower than Start deadzone."
        )

        self.center_alpha_spin.setToolTip(
            "Mouse Cursor mode only. Controls the joystick's "
            "center-tracking response."
        )

        directional_gain_tooltip = (
            "Mouse Cursor mode only. 1.00 is neutral. "
            "Values above 1.00 increase movement in this "
            "direction; values below 1.00 reduce it."
        )

        for spinbox in (
            self.up_gain_spin,
            self.down_gain_spin,
            self.left_gain_spin,
            self.right_gain_spin,
        ):
            spinbox.setToolTip(
                directional_gain_tooltip
            )

        def make_joystick_setting_row(
            label_text: str,
            spinbox: QDoubleSpinBox,
        ) -> QWidget:
            row_widget = QWidget()

            row_widget.setObjectName(
                "joystickSettingRow"
            )

            row_widget.setStyleSheet(
                """
                QWidget#joystickSettingRow {
                    background-color: transparent;
                    border: none;
                }
                """
            )

            row_widget.setFixedHeight(34)

            row_layout = QHBoxLayout(
                row_widget
            )

            row_layout.setContentsMargins(
                0,
                0,
                0,
                0,
            )

            row_layout.setSpacing(14)

            label = QLabel(
                label_text
            )

            label.setFixedWidth(130)

            label.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter
            )

            row_layout.addStretch()

            row_layout.addWidget(
                label
            )

            row_layout.addWidget(
                spinbox
            )

            row_layout.addStretch()

            return row_widget

        # -----------------------------------------------------
        # Deadzone / centering
        # -----------------------------------------------------

        settings_row = QHBoxLayout()

        settings_row.setSpacing(14)

        self.joystick_deadzone_group = QGroupBox(
            "Deadzone / Centering"
        )

        deadzone_group = self.joystick_deadzone_group

        deadzone_group.setFixedSize(
            440,
            230,
        )

        deadzone_group.setStyleSheet(
            _theme_stylesheet("""
            QGroupBox {
                background-color: $BG_GROUP;
                border: 1px solid $BORDER_TEAL;
                border-radius: 5px;
                margin-top: 12px;
                padding-top: 10px;
                font-weight: bold;
            }

            QGroupBox::title {
                color: $TEXT_ACCENT;
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
            }

            QLabel {
                background-color: transparent;
                border: none;
            }
            """)
        )

        deadzone_layout = QVBoxLayout(
            deadzone_group
        )

        deadzone_layout.setContentsMargins(
            14,
            22,
            14,
            14,
        )

        deadzone_layout.setSpacing(0)

        deadzone_help = QLabel(
            "Start controls when input begins. "
            "Stop controls when it releases."
        )

        deadzone_help.setObjectName(
            "joystickHelpText"
        )

        deadzone_help.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        deadzone_help.setFixedHeight(18)

        deadzone_layout.addWidget(
            deadzone_help
        )

        deadzone_layout.addSpacing(8)

        deadzone_layout.addWidget(
            make_joystick_setting_row(
                "Start deadzone:",
                self.start_deadzone_spin,
            )
        )

        deadzone_layout.addStretch(1)

        deadzone_layout.addWidget(
            make_joystick_setting_row(
                "Stop deadzone:",
                self.stop_deadzone_spin,
            )
        )

        deadzone_layout.addStretch(1)

        deadzone_layout.addWidget(
            make_joystick_setting_row(
                "Center tracking:",
                self.center_alpha_spin,
            )
        )

        settings_row.addWidget(
            deadzone_group
        )

        # -----------------------------------------------------
        # Directional gain
        # -----------------------------------------------------

        self.joystick_gain_group = QGroupBox(
            "Directional Gain"
        )

        gain_group = self.joystick_gain_group

        gain_group.setFixedSize(
            440,
            230,
        )

        gain_group.setStyleSheet(
            _theme_stylesheet("""
            QGroupBox {
                background-color: $BG_GROUP;
                border: 1px solid $BORDER_TEAL;
                border-radius: 5px;
                margin-top: 12px;
                padding-top: 10px;
                font-weight: bold;
            }

            QGroupBox::title {
                color: $TEXT_ACCENT;
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
            }

            QLabel {
                background-color: transparent;
                border: none;
            }

            QGroupBox:disabled {
                background-color: $BG_DISABLED_GROUP;
                border-color: $BORDER_DISABLED_GROUP;
            }

            QGroupBox::title:disabled {
                color: $TEXT_DISABLED;
            }

            QLabel:disabled {
                color: $TEXT_DISABLED;
            }
            """)
        )

        gain_layout = QVBoxLayout(
            gain_group
        )

        gain_layout.setContentsMargins(
            14,
            22,
            14,
            14,
        )

        gain_layout.setSpacing(0)

        gain_help = QLabel(
            "Mouse speed by direction. "
            "1.00 = normal."
        )

        gain_help.setObjectName(
            "joystickHelpText"
        )

        gain_help.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        gain_help.setFixedHeight(18)

        gain_layout.addWidget(
            gain_help
        )

        gain_layout.addSpacing(8)

        gain_layout.addWidget(
            make_joystick_setting_row(
                "Up:",
                self.up_gain_spin,
            )
        )

        gain_layout.addStretch(1)

        gain_layout.addWidget(
            make_joystick_setting_row(
                "Down:",
                self.down_gain_spin,
            )
        )

        gain_layout.addStretch(1)

        gain_layout.addWidget(
            make_joystick_setting_row(
                "Left:",
                self.left_gain_spin,
            )
        )

        gain_layout.addStretch(1)

        gain_layout.addWidget(
            make_joystick_setting_row(
                "Right:",
                self.right_gain_spin,
            )
        )

        settings_row.addWidget(
            gain_group
        )

        root.addLayout(settings_row)

        # -----------------------------------------------------
        # Actions
        # -----------------------------------------------------

        button_row = QHBoxLayout()

        button_row.setContentsMargins(
            0,
            0,
            10,
            0,
        )

        button_row.addStretch()

        reload_button = QPushButton(
            "Reload Settings"
        )

        reload_button.setMinimumWidth(170)

        reload_button.clicked.connect(
            self._load_joystick_settings
        )

        self.joystick_save_button = QPushButton(
            "Save Settings"
        )

        self.joystick_save_button.setObjectName(
            "joystickSaveButton"
        )

        self.joystick_save_button.setMinimumWidth(
            170
        )

        self.joystick_save_button.setEnabled(
            False
        )

        self.joystick_save_button.clicked.connect(
            self._save_joystick_settings
        )

        self.joystick_save_button.setStyleSheet(
            _theme_stylesheet("""
            QPushButton#joystickSaveButton {
                background-color: $BG_WARNING;
                border: 1px solid $WARNING;
                color: $TEXT_BRIGHT;
                font-weight: bold;
            }

            QPushButton#joystickSaveButton:hover {
                background-color: $BG_WARNING_HOVER;
                border-color: $WARNING_HOVER;
            }

            QPushButton#joystickSaveButton:disabled {
                background-color: $BG_BUTTON;
                border: 1px solid $BORDER_CONTROL;
                color: $TEXT_DISABLED_BUTTON;
                font-weight: normal;
            }
            """)
        )

        button_row.addWidget(
            reload_button
        )

        button_row.addWidget(
            self.joystick_save_button
        )

        root.addLayout(button_row)

        container_layout.addWidget(group)

        container_layout.addStretch()

        self.joystick_mode_combo.currentIndexChanged.connect(
            self._joystick_settings_changed
        )

        for spinbox in (
            self.start_deadzone_spin,
            self.stop_deadzone_spin,
            self.center_alpha_spin,
            self.left_gain_spin,
            self.right_gain_spin,
            self.up_gain_spin,
            self.down_gain_spin,
        ):
            spinbox.valueChanged.connect(
                self._joystick_settings_changed
            )

        self._joystick_mode_changed()

        container.setStyleSheet(
            """
            QWidget#joystickSettingsContainer {
                background-color: transparent;
                border: none;
            }
            """
        )

        return container

    def _joystick_settings_changed(
        self,
        *args,
    ) -> None:
        self._set_joystick_settings_dirty(
            True
        )

    def _set_joystick_settings_dirty(
        self,
        dirty: bool,
    ) -> None:
        self.joystick_settings_dirty = dirty

        if not hasattr(
            self,
            "joystick_save_button",
        ):
            return

        self.joystick_save_button.setEnabled(
            dirty
        )

        self.joystick_save_button.setText(
            "Save Settings *"
            if dirty
            else "Save Settings"
        )

        self.joystick_save_button.setToolTip(
            "Save unsaved joystick settings"
            if dirty
            else "No unsaved joystick settings"
        )

    def _joystick_mode_changed(
        self,
        index: int = -1,
    ) -> None:
        mode = self.joystick_mode_combo.currentData()

        mouse_mode = mode == "mouse"

        self.center_alpha_spin.setEnabled(
            True
        )

        self.joystick_gain_group.setEnabled(
            mouse_mode
        )

        if mouse_mode:
            self.joystick_deadzone_group.setToolTip(
                "Controls when joystick movement begins "
                "and stops."
            )

            self.center_alpha_spin.setToolTip(
                "Controls center tracking behavior "
                "for mouse movement."
            )

            self.joystick_gain_group.setToolTip(
                "Adjusts mouse cursor movement speed "
                "independently for each direction."
            )

        else:
            self.joystick_deadzone_group.setToolTip(
                "Controls when G26-G29 directional "
                "bindings activate and release."
            )

            self.center_alpha_spin.setToolTip(
                "Controls how quickly the joystick "
                "adapts to small changes in its resting "
                "center position."
            )

            self.joystick_gain_group.setToolTip(
                "Directional gain is used only in "
                "Mouse Cursor mode."
            )

    def _make_gain_spinbox(
        self,
    ) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()

        spinbox.setRange(
            0.10,
            10.00,
        )

        spinbox.setDecimals(2)
        spinbox.setSingleStep(0.01)

        return spinbox

    def _build_layer_tab(
        self,
        layer: int,
    ) -> QWidget:
        tab = QWidget()

        tab_layout = QVBoxLayout(tab)

        tab_layout.setContentsMargins(
            12,
            12,
            12,
            12,
        )

        tab_layout.setSpacing(0)

        device_surface = G13DeviceSurface()

        layout = QVBoxLayout(
            device_surface
        )

        layout.setContentsMargins(
            28,
            28,
            28,
            36,
        )

        layout.setSpacing(8)

        # Exact G1-G22 key-face bounds measured from
        # assets/g13_device.png (1024 x 1536).
        #
        # Each entry is:
        #     source_x, source_y, source_width, source_height
        gkey_source_geometry = {
            # G1-G7
            "G1":  (118, 460, 106, 78),
            "G2":  (242, 463, 90, 78),
            "G3":  (355, 465, 87, 79),
            "G4":  (466, 466, 82, 80),
            "G5":  (574, 466, 85, 79),
            "G6":  (682, 465, 88, 78),
            "G7":  (788, 460, 112, 85),

            # G8-G14
            "G8":  (108, 554, 108, 86),
            "G9":  (233, 568, 95, 76),
            "G10": (350, 573, 90, 74),
            "G11": (460, 574, 88, 77),
            "G12": (572, 573, 89, 78),
            "G13": (684, 568, 91, 77),
            "G14": (793, 557, 112, 85),

            # G15-G19
            "G15": (167, 674, 155, 86),
            "G16": (343, 686, 93, 70),
            "G17": (458, 689, 90, 69),
            "G18": (574, 687, 89, 70),
            "G19": (684, 678, 142, 75),

            # G20-G22
            "G20": (246, 785, 186, 96),
            "G21": (443, 790, 105, 94),
            "G22": (567, 789, 192, 96),
        }

        # Normalized hotspot shapes for the non-rectangular
        # physical keys. Coordinates run from 0.0 to 1.0
        # within each hotspot rectangle.
        gkey_shapes = {
            "G1": [
                (0.10, 0.00),
                (1.00, 0.03),
                (0.95, 1.00),
                (0.00, 0.84),
            ],
            "G7": [
                (0.00, 0.03),
                (0.90, 0.00),
                (1.00, 0.84),
                (0.05, 0.92),
            ],

            "G8": [
                (0.00, 0.00),
                (0.98, 0.10),
                (0.95, 1.00),
                (0.30, 0.90),
                (0.00, 0.20),
            ],
            "G14": [
                (0.00, 0.10),
                (1.00, 0.00),
                (1.00, 0.12),
                (0.68, 0.88),
                (0.05, 0.96),
            ],

            "G15": [
                (0.05, 0.00), # Top Left
                (1.00, 0.10), # Top Right
                (0.98, 0.95), # Bottom Right
                (0.35, 0.90), # Bottom Left
                (0.15, 0.28), # Left side mid-point
            ],
            "G19": [
                (0.00, 0.12),
                (1.00, 0.02),
                (0.98, 0.15),
                (0.68, 0.93),
                (0.00, 1.00),
            ],

            "G20": [
                (0.05, 0.03),
                (1.00, 0.05),
                (1.00, 0.72),
                (0.30, 0.68),
            ],

            "G21": [
                (0.07, 0.05),
                (1.00, 0.05),
                (1.00, 0.70),
                (0.05, 0.70),
            ],

            "G22": [
                (0.02, 0.02),
                (0.92, 0.00),
                (0.92, 0.13),
                (0.68, 0.67),
                (0.52, 0.70),
                (0.02, 0.70),
            ],
        }

        scale_x = (
            device_surface.display_pixmap.width()
            / device_surface.device_pixmap.width()
        )

        scale_y = (
            device_surface.display_pixmap.height()
            / device_surface.device_pixmap.height()
        )

        for (
            button_name,
            source_geometry,
        ) in gkey_source_geometry.items():

            (
                source_x,
                source_y,
                source_width,
                source_height,
            ) = source_geometry

            x = (
                device_surface.device_image_x
                + round(source_x * scale_x)
            )

            y = (
                device_surface.device_image_y
                + round(source_y * scale_y)
            )

            width = round(
                source_width * scale_x
            )

            height = round(
                source_height * scale_y
            )

            control = self._make_control_button(
                layer,
                button_name,
                compact=True,
                compact_size=(
                    width,
                    height,
                ),
            )

            control.setParent(
                device_surface
            )

            control.move(
                x,
                y,
            )

            main_button = self.button_widgets[
                (layer, button_name)
            ]

            shape = gkey_shapes.get(
                button_name
            )

            if shape is not None:
                polygon = QPolygon(
                    [
                        QPoint(
                            round(
                                point_x
                                * (
                                    control.width()
                                    - 1
                                )
                            ),
                            round(
                                point_y
                                * (
                                    control.height()
                                    - 1
                                )
                            ),
                        )
                        for (
                            point_x,
                            point_y,
                        ) in shape
                    ]
                )

                mask = QRegion(
                    polygon
                )

                # Mask only the actual key highlight.
                # Keep the parent control rectangular so
                # the hover-menu arrow cannot be clipped.
                main_button.setMask(
                    mask
                )

                main_button.setProperty(
                    "shapedOverlay",
                    True,
                )

            if button_name == "G7":
                menu_button = getattr(
                    control,
                    "_hover_menu_button",
                    None,
                )

                if menu_button is not None:
                    def position_g7_menu(
                        menu_button=menu_button,
                    ) -> None:
                        menu_button.move(
                            menu_button.x(),
                            max(
                                2,
                                menu_button.y() - 8,
                            ),
                        )

                        menu_button.raise_()

                    QTimer.singleShot(
                        0,
                        position_g7_menu,
                    )

            elif button_name in {
                "G14",
                "G19",
            }:
                menu_button = getattr(
                    control,
                    "_hover_menu_button",
                    None,
                )

                if menu_button is not None:
                    def position_right_edge_menu(
                        menu_button=menu_button,
                    ) -> None:
                        menu_button.move(
                            max(
                                2,
                                menu_button.x() - 20,
                            ),
                            menu_button.y(),
                        )

                        menu_button.raise_()

                    QTimer.singleShot(
                        0,
                        position_right_edge_menu,
                    )

            elif button_name == "G22":
                menu_button = getattr(
                    control,
                    "_hover_menu_button",
                    None,
                )

                if menu_button is not None:
                    def position_g22_menu(
                        control=control,
                        menu_button=menu_button,
                    ) -> None:
                        menu_button.move(
                            round(
                                control.width() * 0.66
                            )
                            - (
                                menu_button.width() // 2
                            ),
                            round(
                                control.height() * 0.51
                            )
                            - (
                                menu_button.height() // 2
                            ),
                        )

                        menu_button.raise_()

                    QTimer.singleShot(
                        0,
                        position_g22_menu,
                    )

            if button_name in {
                "G20",
                "G21",
            }:
                menu_button = getattr(
                    control,
                    "_hover_menu_button",
                    None,
                )

                if menu_button is not None:
                    def position_lower_row_menu(
                        menu_button=menu_button,
                    ) -> None:
                        menu_button.move(
                            menu_button.x(),
                            max(
                                2,
                                menu_button.y() - 15,
                            ),
                        )

                        menu_button.raise_()

                    QTimer.singleShot(
                        0,
                        position_lower_row_menu,
                    )

            main_button.setProperty(
                "imageOverlay",
                True,
            )

            # The physical G-number is already printed
            # on the device image.
            key_label = main_button.findChild(
                QLabel,
                "g13ControlKeyLabel",
            )

            if key_label is not None:
                key_label.hide()

            assignment_label = (
                self.button_assignment_labels
                .get(
                    (
                        layer,
                        button_name,
                    )
                )
            )

            if assignment_label is not None:
                standard_binding_keys = {
                    "G2",
                    "G3",
                    "G4",
                    "G5",
                    "G6",
                    "G9",
                    "G10",
                    "G11",
                    "G12",
                    "G13",
                    "G16",
                    "G17",
                    "G18",
                    "G21",
                }

                edge_binding_keys = {
                    "G1",
                    "G7",
                    "G8",
                    "G14",
                }

                medium_binding_keys = {
                    "G15",
                    "G19",
                }

                long_binding_keys = {
                    "G20",
                    "G22",
                }

                if button_name in standard_binding_keys:
                    floating_width = max(
                        48,
                        width + 8,
                    )

                elif button_name in edge_binding_keys:
                    floating_width = 74

                elif button_name in medium_binding_keys:
                    floating_width = 92

                elif button_name in long_binding_keys:
                    floating_width = 112

                else:
                    floating_width = max(
                        48,
                        width + 8,
                    )

                floating_height = 18

                floating_x = (
                    x
                    + (
                        width
                        - floating_width
                    ) // 2
                )

                floating_x_offsets = {
                    "G1": -7,
                    "G7": 6,
                    "G8": -7,
                    "G14": 5,
                    "G15": -2,
                    "G19": 5,
                    "G20": -9,
                    "G22": 3,
                }

                floating_x += (
                    floating_x_offsets.get(
                        button_name,
                        0,
                    )
                )

                floating_y = y - 4

                assignment_label.setParent(
                    device_surface
                )

                assignment_label.setFixedSize(
                    floating_width,
                    floating_height,
                )

                assignment_label.move(
                    floating_x,
                    floating_y,
                )

                assignment_label.setAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )

                assignment_label.setAttribute(
                    Qt.WidgetAttribute
                    .WA_TransparentForMouseEvents
                )

                assignment_label.setProperty(
                    "floatingBindingBox",
                    True,
                )

                assignment_label.setStyleSheet(
                    _theme_stylesheet("""
                    QLabel {
                        background-color:
                            $FLOATING_BINDING_BG;
                        border:
                            1px solid $FLOATING_BINDING_BORDER;
                        border-radius: 2px;
                        color: $TEXT_FLOATING;
                        font-size: 9px;
                        padding: 1px 4px;
                    }
                    """)
                )

            # Force Qt to reevaluate the new dynamic
            # imageOverlay property.
            main_button.style().unpolish(
                main_button
            )

            main_button.style().polish(
                main_button
            )

            main_button.update()

            control.raise_()

            if assignment_label is not None:
                assignment_label.raise_()

        # -------------------------------------------------
        # Thumb buttons and joystick hotspots
        # -------------------------------------------------
        #
        # Geometry is measured against assets/g13_device.png
        # at its native 1024 x 1536 resolution.
        #
        # G23 = upper thumb button
        # G24 = lower thumb button
        # G25 = joystick click
        # G26 = joystick up
        # G27 = joystick right
        # G28 = joystick down
        # G29 = joystick left

        thumb_source_geometry = {
            "G23": (
                750,
                898,
                67,
                162,
            ),
            "G24": (
                809,
                1060,
                180,
                95,
            ),
        }

        joystick_source_geometry = {
            "G26": (
                879,
                887,
                42,
                26,
            ),
            "G29": (
                828,
                940,
                26,
                42,
            ),
            "G25": (
                888,
                936,
                40,
                40,
            ),
            "G27": (
                959,
                940,
                26,
                42,
            ),
            "G28": (
                879,
                1008,
                42,
                26,
            ),
        }

        thumb_shapes = {
            "G23": [
                (0.82, 0.00), # Tip top right
                (0.96, 0.06), # Right of the top
                (0.75, 0.82), # Right 3/4 down-point
                (0.80, 0.82), # Bottom Right
                (0.48, 1.00), # Bottom tip
                (0.00, 0.70), # Left bottom
                (0.00, 0.32), # Middle left top
                (0.20, 0.24), # Middle left bottom
            ],

            "G24": [
                (0.19, 0.12), # Top left
                (0.92, 0.00), # Top right
                (0.80, 0.24), # Bottom mid-point
                (0.22, 1.00), # Bottom tip
                (0.00, 0.50), # Left point
                (0.10, 0.29), # Top left mid-point
            ],
        }

        joystick_shapes = {
            "G26": [
                (0.50, 0.00),
                (1.00, 1.00),
                (0.00, 1.00),
            ],
            "G29": [
                (0.00, 0.50),
                (1.00, 0.00),
                (1.00, 1.00),
            ],
            "G27": [
                (1.00, 0.50),
                (0.00, 0.00),
                (0.00, 1.00),
            ],
            "G28": [
                (0.00, 0.00),
                (1.00, 0.00),
                (0.50, 1.00),
            ],
        }

        def add_device_hotspot(
            button_name: str,
            source_geometry: tuple[
                int,
                int,
                int,
                int,
            ],
            *,
            shape: list[
                tuple[float, float]
            ] | None = None,
            label_text: str | None = None,
            show_assignment: bool = True,
            ellipse: bool = False,
        ) -> None:
            (
                source_x,
                source_y,
                source_width,
                source_height,
            ) = source_geometry

            scale_x = (
                device_surface.display_pixmap.width()
                / device_surface.device_pixmap.width()
            )

            scale_y = (
                device_surface.display_pixmap.height()
                / device_surface.device_pixmap.height()
            )

            x = (
                device_surface.device_image_x
                + round(
                    source_x
                    * scale_x
                )
            )

            y = (
                device_surface.device_image_y
                + round(
                    source_y
                    * scale_y
                )
            )

            width = max(
                12,
                round(
                    source_width
                    * scale_x
                ),
            )

            height = max(
                12,
                round(
                    source_height
                    * scale_y
                ),
            )

            control = self._make_control_button(
                layer,
                button_name,
                compact=True,
                compact_size=(
                    width,
                    height,
                ),
            )

            control.setParent(
                device_surface
            )

            control.move(
                x,
                y,
            )

            main_button = self.button_widgets[
                (
                    layer,
                    button_name,
                )
            ]

            main_button.setProperty(
                "imageOverlay",
                True,
            )

            key_label = main_button.findChild(
                QLabel,
                "g13ControlKeyLabel",
            )

            is_joystick_direction = (
                button_name
                in {
                    "G26",
                    "G27",
                    "G28",
                    "G29",
                }
            )

            if (
                key_label is not None
                and button_name
                in {
                    "G23",
                    "G24",
                    "G25",
                }
            ):
                key_label.hide()

            elif key_label is not None:
                key_label.setText(
                    label_text
                    if label_text is not None
                    else button_name
                )

                if is_joystick_direction:
                    key_label.setStyleSheet(
                        _theme_stylesheet("""
                        color: $TEXT_JOYSTICK_DIRECTION;
                        font-size: 12px;
                        font-weight: bold;
                        background-color: transparent;
                        """)
                    )
                else:
                    key_label.setStyleSheet(
                        _theme_stylesheet("""
                        color: $WARNING;
                        font-size: 7px;
                        font-weight: bold;
                        background-color: transparent;
                        """)
                    )

            assignment_label = (
                self.button_assignment_labels
                .get(
                    (
                        layer,
                        button_name,
                    )
                )
            )

            if assignment_label is not None:
                if show_assignment:
                    assignment_label.setAlignment(
                        Qt.AlignmentFlag.AlignHCenter
                        | Qt.AlignmentFlag.AlignBottom
                    )

                    assignment_label.setStyleSheet(
                        _theme_stylesheet("""
                        color: $TEXT_PROFILE;
                        font-size: 6px;
                        background-color: transparent;
                        padding-bottom: 1px;
                        """)
                    )

                else:
                    assignment_label.hide()

            if ellipse:
                main_button.setFixedSize(
                    width,
                    height,
                )

                main_button.setMask(
                    QRegion(
                        0,
                        0,
                        width,
                        height,
                        QRegion.RegionType.Ellipse,
                    )
                )

                main_button.setProperty(
                    "shapedOverlay",
                    True,
                )

            elif shape is not None:
                polygon = QPolygon(
                    [
                        QPoint(
                            round(
                                point_x
                                * (
                                    control.width()
                                    - 1
                                )
                            ),
                            round(
                                point_y
                                * (
                                    control.height()
                                    - 1
                                )
                            ),
                        )
                        for (
                            point_x,
                            point_y,
                        ) in shape
                    ]
                )

                main_button.setMask(
                    QRegion(
                        polygon
                    )
                )

                main_button.setProperty(
                    "shapedOverlay",
                    True,
                )

            main_button.style().unpolish(
                main_button
            )

            main_button.style().polish(
                main_button
            )

            main_button.update()

            control.raise_()

        # Physical thumb buttons.
        add_device_hotspot(
            "G23",
            thumb_source_geometry[
                "G23"
            ],
            shape=thumb_shapes[
                "G23"
            ],
            show_assignment=False,
        )

        g23_main_button = self.button_widgets.get(
            (
                layer,
                "G23",
            )
        )

        if g23_main_button is not None:
            g23_control = (
                g23_main_button.parentWidget()
            )

            if g23_control is not None:
                g23_menu_button = getattr(
                    g23_control,
                    "_hover_menu_button",
                    None,
                )

                if g23_menu_button is not None:
                    g23_menu_button.move(
                        max(
                            2,
                            g23_menu_button.x() - 8,
                        ),
                        g23_menu_button.y(),
                    )

                    g23_menu_button.raise_()

        g23_main_button = self.button_widgets.get(
            (
                layer,
                "G23",
            )
        )

        if g23_main_button is not None:
            g23_control = (
                g23_main_button.parentWidget()
            )

            if g23_control is not None:
                g23_menu_button = getattr(
                    g23_control,
                    "_hover_menu_button",
                    None,
                )

                if g23_menu_button is not None:
                    def position_g23_menu(
                        control=g23_control,
                        menu_button=g23_menu_button,
                    ) -> None:
                        menu_button.move(
                            round(
                                control.width()
                                * 0.64
                            )
                            - (
                                menu_button.width()
                                // 2
                            ),
                            control.height()
                            - menu_button.height()
                            - 1,
                        )

                        menu_button.raise_()

                    QTimer.singleShot(
                        0,
                        position_g23_menu,
                    )

        add_device_hotspot(
            "G24",
            thumb_source_geometry[
                "G24"
            ],
            shape=thumb_shapes[
                "G24"
            ],
            show_assignment=False,
        )

        g24_main_button = self.button_widgets.get(
            (
                layer,
                "G24",
            )
        )

        if g24_main_button is not None:
            g24_control = (
                g24_main_button.parentWidget()
            )

            if g24_control is not None:
                g24_menu_button = getattr(
                    g24_control,
                    "_hover_menu_button",
                    None,
                )

                if g24_menu_button is not None:
                    def position_g24_menu(
                        control=g24_control,
                        menu_button=g24_menu_button,
                    ) -> None:
                        menu_button.move(
                            round(
                                control.width()
                                * 0.28
                            )
                            - (
                                menu_button.width()
                                // 2
                            ),
                            control.height()
                            - menu_button.height()
                            - 1,
                        )

                        menu_button.raise_()

                    QTimer.singleShot(
                        0,
                        position_g24_menu,
                    )

        if g24_main_button is not None:
            g24_control = (
                g24_main_button.parentWidget()
            )

            if g24_control is not None:
                g24_menu_button = (
                    g24_control.findChild(
                        QPushButton,
                        "g13ControlMenuButton",
                    )
                )

                if g24_menu_button is not None:
                    g24_menu_button.move(
                        round(
                            g24_control.width()
                            * 0.38
                        )
                        - (
                            g24_menu_button.width()
                            // 2
                        ),
                        g24_control.height()
                        - g24_menu_button.height()
                        - 2,
                    )

                    g24_menu_button.raise_()

        # Joystick directions. Keep these visually simple:
        # the selected-label panel still identifies the real
        # G-number after the user clicks a direction.
        add_device_hotspot(
            "G26",
            joystick_source_geometry[
                "G26"
            ],
            shape=joystick_shapes[
                "G26"
            ],
            label_text="▲",
            show_assignment=False,
        )

        add_device_hotspot(
            "G29",
            joystick_source_geometry[
                "G29"
            ],
            shape=joystick_shapes[
                "G29"
            ],
            label_text="◀",
            show_assignment=False,
        )

        add_device_hotspot(
            "G27",
            joystick_source_geometry[
                "G27"
            ],
            shape=joystick_shapes[
                "G27"
            ],
            label_text="▶",
            show_assignment=False,
        )

        add_device_hotspot(
            "G28",
            joystick_source_geometry[
                "G28"
            ],
            shape=joystick_shapes[
                "G28"
            ],
            label_text="▼",
            show_assignment=False,
        )

        # Joystick press/click sits in the middle and must be
        # raised last so it owns the center of the stick.
        add_device_hotspot(
            "G25",
            joystick_source_geometry[
                "G25"
            ],
            label_text="G25",
            show_assignment=False,
            ellipse=True,
        )

        g25_button = self.button_widgets[
            (
                layer,
                "G25",
            )
        ]

        g25_button.setMask(
            QRegion(
                0,
                0,
                g25_button.width(),
                g25_button.height(),
                QRegion.RegionType.Ellipse,
            )
        )

        device_surface.g25_hotspot_button = (
            self.button_widgets[
                (
                    layer,
                    "G25",
                )
            ]
        )

        # Floating assignment boxes around the thumb/joystick
        # pod, positioned to mimic Logitech Gaming Software.
        pod_binding_source_geometry = {
            # Joystick up
            "G26": (
                858,
                858,
                110,
                28,
            ),

            # Joystick click
            "G25": (
                945,
                888,
                105,
                28,
            ),

            # Joystick left
            "G29": (
                705,
                925,
                108,
                28,
            ),

            # Joystick right
            "G27": (
                975,
                925,
                108,
                28,
            ),

            # Upper silver thumb button
            "G23": (
                690,
                1015,
                110,
                28,
            ),

            # Joystick down
            "G28": (
                930,
                1035,
                112,
                28,
            ),

            # Lower silver thumb button
            "G24": (
                860,
                1135,
                110,
                28,
            ),
        }

        scale_x = (
            device_surface.display_pixmap.width()
            / device_surface.device_pixmap.width()
        )

        scale_y = (
            device_surface.display_pixmap.height()
            / device_surface.device_pixmap.height()
        )

        for (
            button_name,
            (
                source_x,
                source_y,
                source_width,
                source_height,
            ),
        ) in pod_binding_source_geometry.items():
            assignment_label = (
                self.button_assignment_labels.get(
                    (
                        layer,
                        button_name,
                    )
                )
            )

            if assignment_label is None:
                continue

            box_x = (
                device_surface.device_image_x
                + round(
                    source_x
                    * scale_x
                )
            )

            box_y = (
                device_surface.device_image_y
                + round(
                    source_y
                    * scale_y
                )
            )

            box_width = max(
                48,
                round(
                    source_width
                    * scale_x
                ),
            )

            box_height = max(
                18,
                round(
                    source_height
                    * scale_y
                ),
            )

            assignment_label.setParent(
                device_surface
            )

            assignment_label.setFixedSize(
                box_width,
                box_height,
            )

            assignment_label.move(
                box_x,
                box_y,
            )

            assignment_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )

            assignment_label.setAttribute(
                Qt.WidgetAttribute
                .WA_TransparentForMouseEvents
            )

            assignment_label.setProperty(
                "floatingBindingBox",
                True,
            )

            assignment_label.setStyleSheet(
                _theme_stylesheet("""
                QLabel {
                    background-color:
                        $FLOATING_BINDING_BG;
                    border:
                        1px solid $FLOATING_BINDING_BORDER;
                    border-radius: 2px;
                    color: $TEXT_FLOATING;
                    font-size: 9px;
                    padding: 1px 4px;
                }
                """)
            )

            assignment_label.raise_()

            if button_name == "G25":
                device_surface.g25_callout_label = (
                    assignment_label
                )

                device_surface.update()

        tab_layout.addWidget(
            device_surface,
            0,
            (
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
            ),
        )

        tab_layout.addStretch()

        return tab

    def _assign_dropped_command(
        self,
        layer: int,
        button_name: str,
        command_id: str,
    ) -> None:
        try:
            self.manager.assign_command(
                layer,
                button_name,
                command_id,
            )

            self._set_profile_dirty(
                True
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Assign Command",
                str(error),
            )
            return

        self._select_button(
            layer,
            button_name,
        )

        # Wait until the Qt drag operation finishes before
        # rebuilding the Commands list that supplied the drag.
        QTimer.singleShot(
            0,
            self._refresh_all_buttons,
        )

        self.statusBar().showMessage(
            f"Assigned command to "
            f"M{layer} {button_name} "
            f"(unsaved)"
        )

    def _make_control_button(
        self,
        layer: int,
        button_name: str,
        initial_text: str | None = None,
        compact: bool = False,
        compact_size: tuple[int, int] | None = None,
    ) -> QWidget:
        if compact:
            control = G13CompactControl()
        else:
            control = QWidget()

        control.setObjectName(
            "g13SplitControl"
        )

        if compact:
            compact_width, compact_height = (
                compact_size
                if compact_size is not None
                else (40, 32)
            )

            control.setFixedSize(
                compact_width,
                compact_height,
            )
        else:
            compact_width = 0
            compact_height = 0

            control.setMinimumWidth(88)
            control.setFixedHeight(56)

        layout = QHBoxLayout(control)

        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        layout.setSpacing(0)

        base_text = (
            initial_text or button_name
        )

        base_parts = base_text.split(
            "\n",
            1,
        )

        base_detail = (
            base_parts[1]
            if len(base_parts) > 1
            else ""
        )

        button = G13CommandDropButton(
            layer,
            button_name,
            self._assign_dropped_command,
        )

        button.setObjectName(
            "g13ControlButton"
        )

        button.setProperty(
            "baseText",
            base_text,
        )

        button.setProperty(
            "baseDetail",
            base_detail,
        )

        button.setProperty(
            "compactControl",
            compact,
        )

        button.setCheckable(True)

        button.setFixedHeight(
            compact_height
            if compact
            else 56
        )

        button_content_layout = QVBoxLayout(
            button
        )

        if compact:
            button_content_layout.setContentsMargins(
                1,
                1,
                1,
                1,
            )
        else:
            button_content_layout.setContentsMargins(
                3,
                3,
                3,
                3,
            )

        button_content_layout.setSpacing(0)

        key_label = QLabel(
            button_name
        )

        key_label.setStyleSheet(
            _theme_stylesheet(f"""
            color: $WARNING;
            font-size: {
                9 if compact else 11
            }px;
            font-weight: bold;
            background-color: transparent;
            """)
        )

        key_label.setObjectName(
            "g13ControlKeyLabel"
        )

        key_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        key_label.setAttribute(
            Qt.WidgetAttribute
            .WA_TransparentForMouseEvents
        )

        assignment_label = QLabel(
            base_detail
        )

        assignment_label.setObjectName(
            "g13ControlAssignmentLabel"
        )

        assignment_label.setStyleSheet(
            _theme_stylesheet(f"""
            color: $TEXT_ASSIGNMENT;
            font-size: {
                6 if compact else 9
            }px;
            background-color: transparent;
            """)
        )

        assignment_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        assignment_label.setAttribute(
            Qt.WidgetAttribute
            .WA_TransparentForMouseEvents
        )

        button_content_layout.addWidget(
            key_label
        )

        button_content_layout.addWidget(
            assignment_label
        )

        button.clicked.connect(
            lambda checked=False,
            selected_layer=layer,
            selected_button=button_name:
            self._select_button(
                selected_layer,
                selected_button,
            )
        )

        menu_button = QPushButton("▾")

        menu_button.setObjectName(
            "g13ControlMenuButton"
        )

        if compact:
            menu_button.setObjectName(
                "g13CompactMenuButton"
            )

            menu_button.setFixedSize(
                16,
                16,
            )

            menu_button.setStyleSheet(
                _theme_stylesheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    color: $TEXT_COMPACT_MENU;
                    margin: 0;
                    padding: 0;
                    font-size: 10px;
                    font-weight: bold;
                }

                QPushButton:hover {
                    background-color: transparent;
                    border: none;
                    color: $TEXT_BRIGHT;
                }
                """)
            )

        else:
            menu_button.setFixedSize(
                22,
                56,
            )

        menu_button.setToolTip(
            f"{button_name} assignment menu"
        )

        menu_button.clicked.connect(
            lambda checked=False,
            selected_layer=layer,
            selected_button=button_name,
            anchor=menu_button:
            self._show_control_assignment_menu(
                selected_layer,
                selected_button,
                anchor,
            )
        )

        layout.addWidget(
            button,
            1,
        )

        if compact:
            menu_button.setParent(
                control
            )

            menu_button.move(
                control.width() - 17,
                control.height() - 17,
            )

            if isinstance(
                control,
                G13CompactControl,
            ):
                control.configure_hover_menu(
                    button,
                    menu_button,
                )

        else:
            layout.addWidget(
                menu_button
            )

        key = (
            layer,
            button_name,
        )

        self.button_widgets[key] = button

        self.button_assignment_labels[
            key
        ] = assignment_label

        return control

    def _show_control_assignment_menu(
        self,
        layer: int,
        button_name: str,
        anchor: QPushButton,
    ) -> None:
        current_binding = self._raw_binding(
            layer,
            button_name,
        )

        is_assigned = (
            current_binding is not None
        )

        menu = QMenu(self)

        menu.setStyleSheet(
            _theme_stylesheet("""
            QMenu {
                background-color: $BG_MENU;
                border: 1px solid $BORDER_TEAL;
                padding: 4px;
            }

            QMenu::item {
                color: $TEXT_PRIMARY_COOL;
                padding: 7px 24px 7px 10px;
                border-radius: 3px;
            }

            QMenu::item:selected {
                background-color: $BG_ACCENT;
                color: $TEXT_BRIGHT;
            }

            QMenu::item:disabled {
                background-color: transparent;
                color: $TEXT_DISABLED_MENU;
            }

            QMenu::separator {
                height: 1px;
                background-color: $BG_SEPARATOR;
                margin: 4px 6px;
            }
            """)
        )

        assign_action = menu.addAction(
            "Assign New Command"
        )

        edit_action = menu.addAction(
            "Edit Command"
        )

        menu.addSeparator()

        unassign_action = menu.addAction(
            "Unassign"
        )

        assign_action.setEnabled(
            not is_assigned
        )

        edit_action.setEnabled(
            is_assigned
        )

        unassign_action.setEnabled(
            is_assigned
        )

        selected_action = menu.exec(
            anchor.mapToGlobal(
                anchor.rect().bottomLeft()
            )
        )

        if selected_action is None:
            return

        if selected_action == assign_action:
            self._select_button(
                layer,
                button_name,
            )

            keyboard_index = (
                self.action_type_combo.findData(
                    "keyboard"
                )
            )

            if keyboard_index >= 0:
                self.action_type_combo.setCurrentIndex(
                    keyboard_index
                )

            self.key_combo.setFocus()

            self.statusBar().showMessage(
                f"Assign a command to "
                f"M{layer} {button_name}."
            )

            return

        if selected_action == edit_action:
            self._select_button(
                layer,
                button_name,
            )

            self.action_name_edit.setFocus()

            self.statusBar().showMessage(
                f"Editing M{layer} "
                f"{button_name}."
            )

            return

        if selected_action == unassign_action:
            self._select_button(
                layer,
                button_name,
            )

            self._clear_binding()

    def _build_binding_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        commands_group = QGroupBox(
            "COMMANDS"
        )

        commands_group.setFixedHeight(
            250
        )

        commands_layout = QVBoxLayout(
            commands_group
        )

        commands_description = QLabel(
            "Commands stored in this profile."
        )

        commands_description.setObjectName(
            "commandsDescription"
        )

        self.commands_list = ProfileCommandList(
            deselect_callback=(
                self._deselect_profile_command
            ),
        )

        self.commands_list.setDragEnabled(
            False
        )

        self.commands_list.setObjectName(
            "commandsList"
        )

        self.commands_list.setStyleSheet(
            """
            QListWidget#commandsList::item {
                padding: 4px 6px;
            }
            """
        )

        self.commands_list.setMinimumHeight(
            150
        )

        self.commands_list.itemClicked.connect(
            self._edit_profile_command
        )

        new_command_button = QPushButton("+")
        new_command_button.setObjectName(
            "newCommandButton"
        )
        new_command_button.setFixedSize(
            26,
            26,
        )
        new_command_button.setToolTip(
            "New Command"
        )

        new_command_button.clicked.connect(
            self._new_profile_command
        )

        new_command_button.setStyleSheet(
            _theme_stylesheet("""
            QPushButton#newCommandButton {
                background-color: $BG_TEAL_CONTROL;
                border: 1px solid $ACCENT;
                border-radius: 4px;
                color: $TEXT_BRIGHT;
                font-size: 19px;
                font-weight: bold;
                padding: 0px;
            }

            QPushButton#newCommandButton:hover {
                background-color: $BG_TEAL_HOVER;
                border-color: $ACCENT_HOVER;
            }

            QPushButton#newCommandButton:pressed {
                background-color: $BG_ACCENT;
            }
            """)
        )

        self.command_graveyard_button = (
            ProfileCommandDeleteDropButton(
                delete_callback=(
                    self._delete_profile_command_by_id
                )
            )
        )

        commands_header = QHBoxLayout()
        commands_header.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        commands_header.addWidget(
            self.command_graveyard_button,
            0,
            (
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignTop
            ),
        )

        commands_header.addStretch()

        commands_header.addWidget(
            new_command_button,
            0,
            (
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignTop
            ),
        )

        commands_layout.addLayout(
            commands_header
        )

        commands_layout.addWidget(
            self.commands_list
        )

        commands_description.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        commands_layout.addWidget(
            commands_description
        )

        layout.addWidget(
            commands_group
        )

        self.binding_editor_title = QLabel(
            "Command Editor"
        )
        self.binding_editor_title.setObjectName(
            "titleLabel"
        )

        layout.addWidget(
            self.binding_editor_title
        )

        button_layout = QHBoxLayout()

        self.apply_binding_button = QPushButton(
            "Apply Binding"
        )

        self.apply_binding_button.clicked.connect(
            self._apply_binding
        )

        self.clear_binding_button = QPushButton(
            "Clear Binding"
        )

        self.clear_binding_button.clicked.connect(
            self._clear_binding
        )

        button_layout.addWidget(
            self.apply_binding_button
        )

        button_layout.addWidget(
            self.clear_binding_button
        )

        layout.addLayout(
            button_layout
        )

        form = QFormLayout()

        self.action_type_combo = QComboBox()

        self.action_type_combo.addItem(
            "Keyboard",
            "keyboard",
        )

        self.action_type_combo.addItem(
            "Mouse Button",
            "mouse",
        )

        self.action_type_combo.addItem(
            "Mouse Wheel",
            "wheel",
        )

        self.action_type_combo.addItem(
            "Macro",
            "macro",
        )

        self.action_type_combo.currentIndexChanged.connect(
            self._action_type_changed
        )

        self.action_name_edit = QLineEdit()

        form.addRow(
            "Action type:",
            self.action_type_combo,
        )

        form.addRow(
            "Display name:",
            self.action_name_edit,
        )

        layout.addLayout(form)

        self.action_stack = QStackedWidget()

        self.action_stack.addWidget(
            self._build_unbound_page()
        )

        self.action_stack.addWidget(
            self._build_keyboard_page()
        )

        self.action_stack.addWidget(
            self._build_mouse_page()
        )

        self.action_stack.addWidget(
            self._build_wheel_page()
        )

        self.action_stack.addWidget(
            self._build_macro_page()
        )

        layout.addWidget(self.action_stack)

        layout.addStretch()

        return panel

    def _build_unbound_page(
        self,
    ) -> QWidget:
        page = QWidget()

        layout = QVBoxLayout(
            page
        )

        label = QLabel(
            "This control has no assigned action."
        )

        label.setWordWrap(
            True
        )

        layout.addWidget(
            label
        )

        layout.addStretch()

        return page


    def _build_keyboard_page(
        self,
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        form = QFormLayout()

        self.key_combo = KeyCaptureEdit(
            self._keyboard_chord_captured,
            page,
        )

        form.addRow(
            "Key / chord:",
            self.key_combo,
        )

        layout.addLayout(form)

        hint = QLabel(
            "Click the field and press the "
            "physical key or key combination."
        )

        hint.setWordWrap(True)

        layout.addWidget(hint)

        # Keep the existing modifier state objects
        # because the binding loader/builder already
        # uses them internally. They are no longer
        # shown as a separate UI.
        self.modifier_checks: dict[
            str,
            QCheckBox,
        ] = {}

        for modifier_name in MODIFIER_NAMES:
            checkbox = QCheckBox(
                page
            )

            checkbox.hide()

            checkbox.toggled.connect(
                self._sync_keyboard_capture_display
            )

            self.modifier_checks[
                modifier_name
            ] = checkbox

        layout.addStretch()

        return page

    def _keyboard_chord_captured(
        self,
        key_name: str,
        modifiers: list[str],
    ) -> None:
        for (
            modifier_name,
            checkbox,
        ) in self.modifier_checks.items():
            checkbox.blockSignals(True)

            checkbox.setChecked(
                modifier_name in modifiers
            )

            checkbox.blockSignals(False)

        self.key_combo.setModifiers(
            modifiers
        )

    def _sync_keyboard_capture_display(
        self,
    ) -> None:
        modifiers = [
            modifier_name
            for (
                modifier_name,
                checkbox,
            ) in self.modifier_checks.items()
            if checkbox.isChecked()
        ]

        self.key_combo.setModifiers(
            modifiers
        )

    def _build_mouse_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.mouse_button_combo = QComboBox()

        self.mouse_button_combo.addItem(
            "Left Click",
            "left",
        )

        self.mouse_button_combo.addItem(
            "Right Click",
            "right",
        )

        self.mouse_button_combo.addItem(
            "Middle Click",
            "middle",
        )

        self.mouse_button_combo.addItem(
            "Back",
            "back",
        )

        self.mouse_button_combo.addItem(
            "Forward",
            "forward",
        )

        self.mouse_button_combo.addItem(
            "Double Click",
            "double_left",
        )

        self.mouse_button_combo.activated.connect(
            self._mouse_function_changed
        )

        form.addRow(
            "Mouse function:",
            self.mouse_button_combo,
        )

        return page

    def _mouse_function_changed(
        self,
        _index: int,
    ) -> None:
        current_name = (
            self.action_name_edit.text().strip()
        )

        automatic_names = {
            self.mouse_button_combo.itemText(
                index
            )
            for index in range(
                self.mouse_button_combo.count()
            )
        }

        automatic_names.update(
            {
                "Left",
                "Right",
                "Middle",
            }
        )

        if (
            not current_name
            or current_name in automatic_names
        ):
            self.action_name_edit.setText(
                self.mouse_button_combo.currentText()
            )

    def _build_wheel_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.wheel_direction_combo = QComboBox()

        self.wheel_direction_combo.addItem(
            "Up",
            "up",
        )

        self.wheel_direction_combo.addItem(
            "Down",
            "down",
        )

        self.wheel_direction_combo.addItem(
            "Left",
            "left",
        )

        self.wheel_direction_combo.addItem(
            "Right",
            "right",
        )

        self.wheel_direction_combo.activated.connect(
            self._wheel_direction_changed
        )

        self.wheel_amount_spin = QSpinBox()

        self.wheel_amount_spin.setObjectName(
            "wheelAmountSpin"
        )

        self.wheel_amount_spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        self.wheel_amount_spin.setRange(1, 20)
        self.wheel_amount_spin.setValue(1)

        wheel_field_height = (
            self.wheel_direction_combo.sizeHint().height()
        )

        self.wheel_amount_spin.setFixedHeight(
            max(
                1,
                wheel_field_height - 2,
            )
        )

        wheel_amount_control = QWidget()

        wheel_amount_control.setObjectName(
            "wheelAmountControl"
        )

        wheel_amount_control.setFixedHeight(
            wheel_field_height
        )

        wheel_amount_layout = QHBoxLayout(
            wheel_amount_control
        )

        wheel_amount_layout.setContentsMargins(
            0,
            0,
            2,
            0,
        )

        wheel_amount_layout.setSpacing(
            2
        )

        wheel_amount_layout.addWidget(
            self.wheel_amount_spin,
            1,
        )

        wheel_amount_stepper = QWidget()

        wheel_amount_stepper.setObjectName(
            "macroRepeatDelayStepper"
        )

        wheel_amount_stepper.setFixedSize(
            16,
            max(
                1,
                wheel_field_height - 2,
            ),
        )

        wheel_stepper_layout = QVBoxLayout(
            wheel_amount_stepper
        )

        wheel_stepper_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        wheel_stepper_layout.setSpacing(
            0
        )

        wheel_step_button_height = max(
            12,
            (
                wheel_field_height - 2
            ) // 2,
        )

        wheel_amount_up_button = QToolButton()

        wheel_amount_up_button.setObjectName(
            "macroRepeatDelayStepButton"
        )

        wheel_amount_up_button.setText(
            "▲"
        )

        wheel_amount_up_button.setFixedSize(
            16,
            wheel_step_button_height,
        )

        wheel_amount_up_button.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        wheel_amount_up_button.clicked.connect(
            self.wheel_amount_spin.stepUp
        )

        wheel_amount_down_button = QToolButton()

        wheel_amount_down_button.setObjectName(
            "macroRepeatDelayStepButton"
        )

        wheel_amount_down_button.setText(
            "▼"
        )

        wheel_amount_down_button.setFixedSize(
            16,
            wheel_step_button_height,
        )

        wheel_amount_down_button.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        wheel_amount_down_button.clicked.connect(
            self.wheel_amount_spin.stepDown
        )

        wheel_stepper_layout.addWidget(
            wheel_amount_up_button
        )

        wheel_stepper_layout.addWidget(
            wheel_amount_down_button
        )

        wheel_amount_layout.addWidget(
            wheel_amount_stepper
        )

        form.addRow(
            "Direction:",
            self.wheel_direction_combo,
        )

        form.addRow(
            "Steps:",
            wheel_amount_control,
        )

        page.setStyleSheet(
            _theme_stylesheet("""
            QWidget#wheelAmountControl {
                background-color: $BG_INPUT;
                border: 1px solid $BORDER_CONTROL;
                border-radius: 4px;
            }

            QSpinBox#wheelAmountSpin {
                background-color: transparent;
                border: none;
                border-radius: 0px;
                padding: 5px;
            }

            QWidget#macroRepeatDelayStepper {
                background-color: $BG_INPUT;
                border: none;
            }

            QToolButton#macroRepeatDelayStepButton {
                background-color: $BG_INPUT;
                border: none;
                border-left: 1px solid $BORDER_CONTROL;
                color: $TEXT_STEPPER;
                font-size: 7px;
                padding: 0px;
            }

            QToolButton#macroRepeatDelayStepButton:hover {
                background-color: $BG_CONTROL_SUBTLE;
                border-left-color: $BORDER_CONTROL;
                color: $TEXT_STEPPER;
            }

            QToolButton#macroRepeatDelayStepButton:pressed {
                background-color: $BG_BUTTON;
                color: $TEXT_STEPPER;
            }
            """)
        )

        return page

    def _wheel_direction_changed(
        self,
        _index: int,
    ) -> None:
        current_name = (
            self.action_name_edit.text().strip()
        )

        automatic_names = {
            "Scroll Up",
            "Scroll Down",
            "Scroll Left",
            "Scroll Right",
            "Vertical Wheel",
            "Horizontal Wheel",
        }

        if (
            not current_name
            or current_name in automatic_names
        ):
            self.action_name_edit.setText(
                "Scroll "
                f"{self.wheel_direction_combo.currentText()}"
            )

    def _build_macro_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        execution_form = QFormLayout()

        self.macro_execution_mode_combo = (
            QComboBox()
        )

        self.macro_execution_mode_combo.addItem(
            "Run Once",
            "once",
        )

        self.macro_execution_mode_combo.addItem(
            "Repeat While Held",
            "while_held",
        )

        self.macro_execution_mode_combo.addItem(
            "Toggle Repeat",
            "toggle",
        )

        self.macro_repeat_delay_spin = (
            QSpinBox()
        )

        self.macro_repeat_delay_spin.setObjectName(
            "macroRepeatDelaySpin"
        )

        self.macro_repeat_delay_spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        self.macro_repeat_delay_spin.setRange(
            1,
            600000,
        )

        self.macro_repeat_delay_spin.setValue(
            25
        )

        self.macro_repeat_delay_spin.setSuffix(
            " ms"
        )

        repeat_delay_field_height = (
            self.macro_execution_mode_combo
            .sizeHint()
            .height()
        )

        self.macro_repeat_delay_spin.setFixedHeight(
            max(
                1,
                repeat_delay_field_height - 2,
            )
        )

        self.macro_repeat_delay_control = QWidget()

        self.macro_repeat_delay_control.setObjectName(
            "macroRepeatDelayControl"
        )

        self.macro_repeat_delay_control.setFixedHeight(
            repeat_delay_field_height
        )

        repeat_delay_layout = QHBoxLayout(
            self.macro_repeat_delay_control
        )

        repeat_delay_layout.setContentsMargins(
            0,
            0,
            2,
            0,
        )

        repeat_delay_layout.setSpacing(
            2
        )

        repeat_delay_layout.addWidget(
            self.macro_repeat_delay_spin,
            1,
        )

        repeat_delay_stepper = QWidget()

        repeat_delay_stepper.setObjectName(
            "macroRepeatDelayStepper"
        )

        repeat_delay_stepper.setFixedSize(
            16,
            max(
                1,
                repeat_delay_field_height - 2,
            ),
        )

        stepper_layout = QVBoxLayout(
            repeat_delay_stepper
        )

        stepper_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        stepper_layout.setSpacing(
            0
        )

        repeat_delay_step_button_height = max(
            12,
            (
                repeat_delay_field_height - 2
            ) // 2,
        )

        self.macro_repeat_delay_up_button = (
            QToolButton()
        )

        self.macro_repeat_delay_up_button.setObjectName(
            "macroRepeatDelayStepButton"
        )

        self.macro_repeat_delay_up_button.setText(
            "▲"
        )

        self.macro_repeat_delay_up_button.setFixedSize(
            16,
            repeat_delay_step_button_height,
        )

        self.macro_repeat_delay_up_button.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        self.macro_repeat_delay_up_button.clicked.connect(
            self.macro_repeat_delay_spin.stepUp
        )

        self.macro_repeat_delay_down_button = (
            QToolButton()
        )

        self.macro_repeat_delay_down_button.setObjectName(
            "macroRepeatDelayStepButton"
        )

        self.macro_repeat_delay_down_button.setText(
            "▼"
        )

        self.macro_repeat_delay_down_button.setFixedSize(
            16,
            repeat_delay_step_button_height,
        )

        self.macro_repeat_delay_down_button.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        self.macro_repeat_delay_down_button.clicked.connect(
            self.macro_repeat_delay_spin.stepDown
        )

        stepper_layout.addWidget(
            self.macro_repeat_delay_up_button
        )

        stepper_layout.addWidget(
            self.macro_repeat_delay_down_button
        )

        repeat_delay_layout.addWidget(
            repeat_delay_stepper
        )

        self.macro_execution_mode_combo.currentIndexChanged.connect(
            self._macro_execution_mode_changed
        )

        execution_form.addRow(
            "Execution:",
            self.macro_execution_mode_combo,
        )

        execution_form.addRow(
            "Repeat delay:",
            self.macro_repeat_delay_control,
        )

        layout.addLayout(execution_form)

        self.macro_allow_overlap_check = QCheckBox(
            "Allow multiple copies to run at once"
        )

        layout.addWidget(
            self.macro_allow_overlap_check
        )

        self.record_macro_button = QPushButton(
    "Record Keystrokes with MR"
        )

        self.record_macro_button.clicked.connect(
            self._toggle_macro_recording
        )

        layout.addWidget(
            self.record_macro_button
        )

        step_type_row = QHBoxLayout()

        self.macro_step_type_combo = QComboBox()

        self.macro_step_type_combo.addItem(
            "Key Press",
            "key_press",
        )

        self.macro_step_type_combo.addItem(
            "Key Down",
            "key_down",
        )

        self.macro_step_type_combo.addItem(
            "Key Up",
            "key_up",
        )

        self.macro_step_type_combo.addItem(
            "Delay",
            "delay",
        )

        self.macro_step_type_combo.addItem(
            "Mouse Click",
            "mouse_click",
        )

        self.macro_step_type_combo.addItem(
            "Mouse Down",
            "mouse_down",
        )

        self.macro_step_type_combo.addItem(
            "Mouse Up",
            "mouse_up",
        )

        self.macro_step_type_combo.addItem(
            "Mouse Wheel",
            "wheel",
        )

        add_step_button = QPushButton(
            "Add Step"
        )

        add_step_button.clicked.connect(
            self._add_macro_step
        )

        step_type_row.addWidget(
            self.macro_step_type_combo
        )

        step_type_row.addWidget(
            add_step_button
        )

        layout.addLayout(step_type_row)

        self.macro_steps_list = QListWidget()

        self.macro_steps_list.setMinimumHeight(
            100
        )

        self.macro_steps_list.setMaximumHeight(
            150
        )

        self.macro_steps_list.itemDoubleClicked.connect(
            lambda _item: self._edit_macro_step()
        )

        layout.addWidget(
            self.macro_steps_list
        )

        step_buttons = QHBoxLayout()

        step_buttons.setSpacing(
            4
        )

        edit_button = QPushButton(
            "Edit"
        )

        edit_button.clicked.connect(
            self._edit_macro_step
        )

        remove_button = QPushButton(
            "Remove"
        )

        remove_button.clicked.connect(
            self._remove_macro_step
        )

        move_up_button = QPushButton(
            "Move Up"
        )

        move_up_button.clicked.connect(
            self._move_macro_step_up
        )

        move_down_button = QPushButton(
            "Move Down"
        )

        move_down_button.clicked.connect(
            self._move_macro_step_down
        )

        for button in (
            edit_button,
            remove_button,
            move_up_button,
            move_down_button,
        ):
            button.setMinimumWidth(
                0
            )

            button.setStyleSheet(
                """
                QPushButton {
                    padding-left: 4px;
                    padding-right: 4px;
                }
                """
            )

        step_buttons.addWidget(
            edit_button,
            1,
        )

        step_buttons.addWidget(
            remove_button,
            1,
        )

        step_buttons.addWidget(
            move_up_button,
            1,
        )

        step_buttons.addWidget(
            move_down_button,
            1,
        )

        layout.addLayout(step_buttons)

        page.setStyleSheet(
            _theme_stylesheet(
                """
                QWidget#macroRepeatDelayControl {
                    background-color: $BG_INPUT;
                    border: 1px solid $BORDER_CONTROL;
                    border-radius: 4px;
                }

                QSpinBox#macroRepeatDelaySpin {
                    background-color: transparent;
                    border: none;
                    border-radius: 0px;
                    padding: 5px;
                }

                QWidget#macroRepeatDelayStepper {
                    background-color: $BG_INPUT;
                    border: none;
                }

                QToolButton#macroRepeatDelayStepButton {
                    background-color: $BG_INPUT;
                    border: none;
                    border-left: 1px solid $BORDER_CONTROL;
                    color: $TEXT_STEPPER;
                    font-size: 7px;
                    padding: 0px;
                }

                QToolButton#macroRepeatDelayStepButton:hover {
                    background-color: $BG_CONTROL_SUBTLE;
                    border-left-color: $BORDER_CONTROL;
                    color: $TEXT_STEPPER;
                }

                QToolButton#macroRepeatDelayStepButton:pressed {
                    background-color: $BG_BUTTON;
                    color: $TEXT_STEPPER;
                }

                QWidget#macroRepeatDelayControl:disabled {
                    background-color: $BG_DISABLED_CONTROL;
                    border-color: $BORDER_DISABLED_CONTROL;
                }

                QSpinBox#macroRepeatDelaySpin:disabled {
                    background-color: transparent;
                    border: none;
                    color: $TEXT_DISABLED_STEPPER;
                }

                QWidget#macroRepeatDelayStepper:disabled {
                    background-color: $BG_DISABLED_CONTROL;
                }

                QToolButton#macroRepeatDelayStepButton:disabled {
                    background-color: $BG_DISABLED_CONTROL;
                    border-left-color: $BORDER_DISABLED_CONTROL;
                    color: $TEXT_DISABLED_STEPPER;
                }
                """
            )
        )

        self._macro_execution_mode_changed()

        return page

    def _toggle_macro_recording(self):
        if self.active_macro_recorder is not None:
            self.active_macro_recorder.finish_recording()
            return

        self._start_macro_recording()

    def _start_macro_recording(self):
        if self.selected_button is None:
            QMessageBox.information(
                self,
                "Record Macro",
                (
                    "Select the G13 control that "
                    "will receive the macro first."
                ),
            )
            return

        if self.active_macro_recorder is not None:
            self.active_macro_recorder.raise_()
            self.active_macro_recorder.activateWindow()
            return

        recorder = MacroRecorderDialog(
            self,
            record_delays=(
                self.manager
                .get_record_macro_delays()
            ),
        )

        self.active_macro_recorder = recorder

        recorder.accepted.connect(
            lambda current=recorder:
            self._use_macro_recording(current)
        )

        recorder.finished.connect(
            lambda _result, current=recorder:
            self._macro_recorder_closed(current)
        )

        self.record_macro_button.setText(
            "Stop Recording"
        )

        self.statusBar().showMessage(
            "Macro recording active — press MR to stop."
        )

        recorder.show()
        recorder.raise_()
        recorder.activateWindow()

        if (
            self.manager
            .get_show_macro_recording_on_lcd()
        ):
            self.command_publisher.publish_macro_recording(
                True
            )

    def _use_macro_recording(
        self,
        recorder: MacroRecorderDialog,
    ) -> None:
        if not recorder.steps:
            self.statusBar().showMessage(
                "No keyboard events were recorded."
            )
            return

        macro_index = (
            self.action_type_combo
            .findData("macro")
        )

        if macro_index >= 0:
            self.action_type_combo.setCurrentIndex(
                macro_index
            )

        self.macro_steps = [
            dict(step)
            for step in recorder.steps
        ]

        self._refresh_macro_steps()

        if not self.action_name_edit.text().strip():
            self.action_name_edit.setText(
                "Recorded Macro"
            )

        save_button_text = "Apply Binding"

        if hasattr(
            self,
            "apply_binding_button",
        ):
            save_button_text = (
                self.apply_binding_button.text()
            )

        self.statusBar().showMessage(
            (
                f"Recorded {len(self.macro_steps)} "
                f"macro steps. Click {save_button_text} "
                "to save them."
            )
        )

    def _macro_recorder_closed(
        self,
        recorder: MacroRecorderDialog,
    ) -> None:
        if self.active_macro_recorder is recorder:
            self.active_macro_recorder = None

        self.record_macro_button.setText(
            "Record Keystrokes with MR"
        )

        self.command_publisher.publish_macro_recording(
            False
        )

    def _macro_execution_mode_changed(
        self,
    ):
        mode = (
            self.macro_execution_mode_combo
            .currentData()
        )

        repeating = mode in {
            "while_held",
            "toggle",
        }

        if not repeating:
            self.macro_repeat_delay_spin.lineEdit().deselect()
            self.macro_repeat_delay_spin.clearFocus()

        self.macro_repeat_delay_control.setEnabled(
            repeating
        )

    def _format_macro_step(
        self,
        step: dict[str, Any],
    ) -> str:
        step_type = step.get("type")

        if step_type == "key_press":
            return (
                f"Press {step.get('key')} "
                f"for {step.get('duration_ms', 30)} ms"
            )

        if step_type == "key_down":
            return f"Key down: {step.get('key')}"

        if step_type == "key_up":
            return f"Key up: {step.get('key')}"

        if step_type == "delay":
            return (
                f"Delay: "
                f"{step.get('milliseconds', 0)} ms"
            )

        if step_type == "mouse_click":
            return (
                f"Click {step.get('button')} "
                f"for {step.get('duration_ms', 30)} ms"
            )

        if step_type == "mouse_down":
            return (
                f"Mouse down: "
                f"{step.get('button')}"
            )

        if step_type == "mouse_up":
            return (
                f"Mouse up: "
                f"{step.get('button')}"
            )

        if step_type == "wheel":
            amount = step.get("amount", 0)

            return (
                f"Wheel {step.get('direction')}: "
                f"{amount:+d}"
            )

        return str(step)


    def _refresh_macro_steps(self):
        self.macro_steps_list.clear()

        for index, step in enumerate(
            self.macro_steps,
            start=1,
        ):
            self.macro_steps_list.addItem(
                f"{index}. "
                f"{self._format_macro_step(step)}"
            )


    def _add_macro_step(self):
        step_type = (
            self.macro_step_type_combo.currentData()
        )

        step: dict[str, Any] | None = None

        if step_type in {
            "key_press",
            "key_down",
            "key_up",
        }:
            key_name, accepted = (
                QInputDialog.getItem(
                    self,
                    "Add Macro Step",
                    "Key:",
                    KEY_NAMES,
                    0,
                    False,
                )
            )

            if not accepted:
                return

            step = {
                "type": step_type,
                "key": key_name,
            }

            if step_type == "key_press":
                duration, accepted = (
                    QInputDialog.getInt(
                        self,
                        "Key Press Duration",
                        "Duration in milliseconds:",
                        35,
                        0,
                        60000,
                        5,
                    )
                )

                if not accepted:
                    return

                step["duration_ms"] = duration

        elif step_type == "delay":
            milliseconds, accepted = (
                QInputDialog.getInt(
                    self,
                    "Add Delay",
                    "Delay in milliseconds:",
                    100,
                    0,
                    600000,
                    10,
                )
            )

            if not accepted:
                return

            step = {
                "type": "delay",
                "milliseconds": milliseconds,
            }

        elif step_type in {
            "mouse_click",
            "mouse_down",
            "mouse_up",
        }:
            button, accepted = (
                QInputDialog.getItem(
                    self,
                    "Add Mouse Step",
                    "Mouse button:",
                    [
                        "left",
                        "right",
                        "middle",
                    ],
                    0,
                    False,
                )
            )

            if not accepted:
                return

            step = {
                "type": step_type,
                "button": button,
            }

            if step_type == "mouse_click":
                duration, accepted = (
                    QInputDialog.getInt(
                        self,
                        "Mouse Click Duration",
                        "Duration in milliseconds:",
                        35,
                        0,
                        60000,
                        5,
                    )
                )

                if not accepted:
                    return

                step["duration_ms"] = duration

        elif step_type == "wheel":
            direction, accepted = (
                QInputDialog.getItem(
                    self,
                    "Add Wheel Step",
                    "Direction:",
                    [
                        "vertical",
                        "horizontal",
                    ],
                    0,
                    False,
                )
            )

            if not accepted:
                return

            amount, accepted = (
                QInputDialog.getInt(
                    self,
                    "Add Wheel Step",
                    "Amount:",
                    1,
                    -20,
                    20,
                    1,
                )
            )

            if not accepted:
                return

            if amount == 0:
                QMessageBox.information(
                    self,
                    "Add Wheel Step",
                    "Wheel amount cannot be zero.",
                )
                return

            step = {
                "type": "wheel",
                "direction": direction,
                "amount": amount,
            }

        if step is None:
            return

        self.macro_steps.append(step)
        self._refresh_macro_steps()

        self.macro_steps_list.setCurrentRow(
            len(self.macro_steps) - 1
        )


    def _remove_macro_step(self):
        row = self.macro_steps_list.currentRow()

        if row < 0:
            return

        self.macro_steps.pop(row)
        self._refresh_macro_steps()

        if self.macro_steps:
            self.macro_steps_list.setCurrentRow(
                min(
                    row,
                    len(self.macro_steps) - 1,
                )
            )

    def _edit_macro_step(self):
        row = self.macro_steps_list.currentRow()

        if row < 0:
            return

        step = copy.deepcopy(
            self.macro_steps[row]
        )

        step_type = step.get("type")

        # -----------------------------------------------------
        # Keyboard steps
        # -----------------------------------------------------

        if step_type in {
            "key_press",
            "key_down",
            "key_up",
        }:
            current_key = str(
                step.get(
                    "key",
                    KEY_NAMES[0] if KEY_NAMES else "",
                )
            )

            try:
                current_index = KEY_NAMES.index(
                    current_key
                )
            except ValueError:
                current_index = 0

            key_name, accepted = (
                QInputDialog.getItem(
                    self,
                    "Edit Macro Step",
                    "Key:",
                    KEY_NAMES,
                    current_index,
                    False,
                )
            )

            if not accepted:
                return

            step["key"] = key_name

            if step_type == "key_press":
                duration, accepted = (
                    QInputDialog.getInt(
                        self,
                        "Edit Key Press",
                        "Duration in milliseconds:",
                        int(
                            step.get(
                                "duration_ms",
                                35,
                            )
                        ),
                        0,
                        60000,
                        5,
                    )
                )

                if not accepted:
                    return

                step["duration_ms"] = duration

        # -----------------------------------------------------
        # Delay
        # -----------------------------------------------------

        elif step_type == "delay":
            milliseconds, accepted = (
                QInputDialog.getInt(
                    self,
                    "Edit Delay",
                    "Delay in milliseconds:",
                    int(
                        step.get(
                            "milliseconds",
                            100,
                        )
                    ),
                    0,
                    600000,
                    10,
                )
            )

            if not accepted:
                return

            step["milliseconds"] = milliseconds

        # -----------------------------------------------------
        # Mouse-button steps
        # -----------------------------------------------------

        elif step_type in {
            "mouse_click",
            "mouse_down",
            "mouse_up",
        }:
            mouse_buttons = [
                "left",
                "right",
                "middle",
            ]

            current_button = str(
                step.get(
                    "button",
                    "left",
                )
            )

            try:
                current_index = mouse_buttons.index(
                    current_button
                )
            except ValueError:
                current_index = 0

            button, accepted = (
                QInputDialog.getItem(
                    self,
                    "Edit Mouse Step",
                    "Mouse button:",
                    mouse_buttons,
                    current_index,
                    False,
                )
            )

            if not accepted:
                return

            step["button"] = button

            if step_type == "mouse_click":
                duration, accepted = (
                    QInputDialog.getInt(
                        self,
                        "Edit Mouse Click",
                        "Duration in milliseconds:",
                        int(
                            step.get(
                                "duration_ms",
                                35,
                            )
                        ),
                        0,
                        60000,
                        5,
                    )
                )

                if not accepted:
                    return

                step["duration_ms"] = duration

        # -----------------------------------------------------
        # Wheel
        # -----------------------------------------------------

        elif step_type == "wheel":
            directions = [
                "vertical",
                "horizontal",
            ]

            current_direction = str(
                step.get(
                    "direction",
                    "vertical",
                )
            )

            try:
                current_index = directions.index(
                    current_direction
                )
            except ValueError:
                current_index = 0

            direction, accepted = (
                QInputDialog.getItem(
                    self,
                    "Edit Wheel Step",
                    "Direction:",
                    directions,
                    current_index,
                    False,
                )
            )

            if not accepted:
                return

            amount, accepted = (
                QInputDialog.getInt(
                    self,
                    "Edit Wheel Step",
                    "Amount:",
                    int(
                        step.get(
                            "amount",
                            1,
                        )
                    ),
                    -20,
                    20,
                    1,
                )
            )

            if not accepted:
                return

            if amount == 0:
                QMessageBox.information(
                    self,
                    "Edit Wheel Step",
                    "Wheel amount cannot be zero.",
                )
                return

            step["direction"] = direction
            step["amount"] = amount

        else:
            QMessageBox.critical(
                self,
                "Edit Macro Step",
                (
                    "This macro step has an "
                    f"unsupported type: {step_type}"
                ),
            )
            return

        self.macro_steps[row] = step

        self._refresh_macro_steps()
        self.macro_steps_list.setCurrentRow(row)

    def _move_macro_step_up(self):
        row = self.macro_steps_list.currentRow()

        if row <= 0:
            return

        self.macro_steps[
            row - 1
        ], self.macro_steps[row] = (
            self.macro_steps[row],
            self.macro_steps[row - 1],
        )

        self._refresh_macro_steps()
        self.macro_steps_list.setCurrentRow(
            row - 1
        )


    def _move_macro_step_down(self):
        row = self.macro_steps_list.currentRow()

        if (
            row < 0
            or row >= len(self.macro_steps) - 1
        ):
            return

        self.macro_steps[
            row + 1
        ], self.macro_steps[row] = (
            self.macro_steps[row],
            self.macro_steps[row + 1],
        )

        self._refresh_macro_steps()
        self.macro_steps_list.setCurrentRow(
            row + 1
        )

    # ---------------------------------------------------------
    # Live driver input
    # ---------------------------------------------------------

    def _poll_driver_events(self):
        events = (
            self.event_listener.receive_all()
        )

        if events:
            self.driver_connected = True

            self.driver_connection_timer.start()

        for event in events:
            event_type = event.get("type")

            if event_type == "layer":
                self._handle_live_layer_event(
                    event
                )

            elif event_type == "button":
                self._handle_live_button_event(
                    event
                )

            elif event_type == "runtime_profile":
                self._handle_runtime_profile_event(
                    event
                )

            elif (
                event_type
                == "onboard_profiles_updated"
            ):
                self._load_cached_onboard_profiles()

        if events:
            self._refresh_device_settings_status()


    def _handle_live_layer_event(
        self,
        event: dict[str, Any],
    ) -> None:
        layer = event.get("layer")

        if (
            not isinstance(layer, int)
            or layer not in {1, 2, 3}
        ):
            return

        # Ignore repeated heartbeat reports for the same
        # physical M-state. This lets the user browse another
        # M-layer in the editor without being snapped back.

        if self.device_layer == layer:
            return

        self.device_layer = layer

        if hasattr(
            self,
            "backlight_color_buttons",
        ):
            self._select_backlight_layer(
                layer
            )

        target_index = layer - 1

        if (
            self.layer_tabs.currentIndex()
            != target_index
        ):
            self.layer_tabs.setCurrentIndex(
                target_index
            )

        self.statusBar().showMessage(
            f"M{layer} active on G13"
        )

    def _handle_runtime_profile_event(
        self,
        event: dict[str, Any],
    ) -> None:
        active_profile = event.get(
            "active_profile"
        )

        source = event.get("source")

        locked_profile = event.get(
            "locked_profile"
        )

        window_class = event.get(
            "window_class",
            "",
        )

        if (
            not isinstance(active_profile, str)
            or not active_profile
            or not isinstance(source, str)
            or not source
        ):
            return

        normalized_locked_profile = (
            locked_profile
            if (
                isinstance(
                    locked_profile,
                    str,
                )
                and locked_profile
            )
            else None
        )

        runtime_status_changed = (
            self.runtime_active_profile
            != active_profile
            or self.runtime_locked_profile
            != normalized_locked_profile
            or self.runtime_profile_source
            != source
            or self.runtime_window_class
            != window_class
        )

        profile_markers_changed = (
            self.runtime_active_profile
            != active_profile
            or self.runtime_locked_profile
            != normalized_locked_profile
        )

        self.runtime_active_profile = (
            active_profile
        )

        self.runtime_locked_profile = (
            normalized_locked_profile
        )

        self.runtime_profile_source = source

        self.runtime_window_class = (
            window_class
            if isinstance(window_class, str)
            else ""
        )

        if not self._startup_runtime_profile_synced:
            self._startup_runtime_profile_synced = True

            try:
                local_profiles = (
                    self.manager.list_profiles()
                )

            except Exception:
                local_profiles = []

            if (
                active_profile
                in local_profiles
                and active_profile
                != self.manager.active_profile_name
                and not self.profile_dirty
            ):
                self._profile_changed(
                    active_profile
                )

        if profile_markers_changed:
            self._refresh_profile_strip()

        if runtime_status_changed:
            self._refresh_profiles_applications_status()

    def _handle_live_button_event(
        self,
        event: dict[str, Any],
    ) -> None:
        layer = event.get("layer")
        button_name = event.get("button")
        pressed = event.get("pressed")

        if (
            not isinstance(layer, int)
            or layer not in {1, 2, 3}
            or not isinstance(button_name, str)
            or not isinstance(pressed, bool)
        ):
            return

        if button_name == "MR":
            if pressed:
                self._toggle_macro_recording()

            return

        key = (
            layer,
            button_name,
        )

        widget = self.button_widgets.get(key)

        if widget is None:
            return

        if pressed:
            self.live_pressed_buttons.add(key)
        else:
            self.live_pressed_buttons.discard(key)

        widget.setProperty(
            "livePressed",
            pressed,
        )

        # Re-evaluate the Qt stylesheet after changing the
        # dynamic property.
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def closeEvent(self, event):
        profile_dirty = bool(
            getattr(
                self,
                "profile_dirty",
                False,
            )
        )

        joystick_dirty = bool(
            getattr(
                self,
                "joystick_settings_dirty",
                False,
            )
        )

        if profile_dirty or joystick_dirty:
            if profile_dirty and joystick_dirty:
                message = (
                    "The current profile and joystick "
                    "settings have unsaved changes.\n\n"
                    "Save them before closing?"
                )

            elif profile_dirty:
                message = (
                    "The current profile has unsaved "
                    "changes.\n\n"
                    "Save them before closing?"
                )

            else:
                message = (
                    "The joystick settings have unsaved "
                    "changes.\n\n"
                    "Save them before closing?"
                )

            response = QMessageBox.warning(
                self,
                "Unsaved Changes",
                message,
                (
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel
                ),
                QMessageBox.StandardButton.Save,
            )

            if (
                response
                == QMessageBox.StandardButton.Cancel
            ):
                self._restart_after_close = False
                event.ignore()
                return

            if (
                response
                == QMessageBox.StandardButton.Save
            ):
                if (
                    profile_dirty
                    and not self._save_profile()
                ):
                    self._restart_after_close = False
                    event.ignore()
                    return

                if joystick_dirty:
                    self._save_joystick_settings()

                    if self.joystick_settings_dirty:
                        self._restart_after_close = False
                        event.ignore()
                        return

        if self.active_macro_recorder is not None:
            self.active_macro_recorder.cancel_recording()
            self.active_macro_recorder = None

        self.event_timer.stop()
        self.driver_connection_timer.stop()
        self.event_listener.close()
        self.command_publisher.close()

        restart_after_close = bool(
            getattr(
                self,
                "_restart_after_close",
                False,
            )
        )

        self._restart_after_close = False

        super().closeEvent(event)

        if (
            restart_after_close
            and event.isAccepted()
        ):
            application = QApplication.instance()

            if application is not None:
                application.setProperty(
                    "restartAfterExit",
                    True,
                )

    # ---------------------------------------------------------
    # Profile operations
    # ---------------------------------------------------------
    def _load_joystick_settings(self):
        try:
            joystick = (
                self.manager
                .get_joystick_settings()
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Joystick Settings",
                str(error),
            )
            return

        mode = joystick.get(
            "mode",
            "bindings",
        )

        mode_index = (
            self.joystick_mode_combo
            .findData(mode)
        )

        if mode_index >= 0:
            self.joystick_mode_combo.setCurrentIndex(
                mode_index
            )

        self.start_deadzone_spin.setValue(
            float(
                joystick.get(
                    "start_deadzone",
                    0.28,
                )
            )
        )

        self.stop_deadzone_spin.setValue(
            float(
                joystick.get(
                    "stop_deadzone",
                    0.20,
                )
            )
        )

        self.left_gain_spin.setValue(
            float(
                joystick.get(
                    "left_gain",
                    1.0,
                )
            )
        )

        self.right_gain_spin.setValue(
            float(
                joystick.get(
                    "right_gain",
                    1.0,
                )
            )
        )

        self.up_gain_spin.setValue(
            float(
                joystick.get(
                    "up_gain",
                    1.0,
                )
            )
        )

        self.down_gain_spin.setValue(
            float(
                joystick.get(
                    "down_gain",
                    1.0,
                )
            )
        )

        self.center_alpha_spin.setValue(
            float(
                joystick.get(
                    "center_alpha",
                    0.005,
                )
            )
        )

        self._set_joystick_settings_dirty(
            False
        )

        self.statusBar().showMessage(
            "Joystick settings loaded."
        )

    def _save_joystick_settings(self):
        joystick = {
            "mode": (
                self.joystick_mode_combo
                .currentData()
            ),
            "start_deadzone": (
                self.start_deadzone_spin.value()
            ),
            "stop_deadzone": (
                self.stop_deadzone_spin.value()
            ),
            "left_gain": (
                self.left_gain_spin.value()
            ),
            "right_gain": (
                self.right_gain_spin.value()
            ),
            "up_gain": (
                self.up_gain_spin.value()
            ),
            "down_gain": (
                self.down_gain_spin.value()
            ),
            "center_alpha": (
                self.center_alpha_spin.value()
            ),
        }

        try:
            self.manager.save_joystick_settings(
                joystick
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Joystick Settings",
                str(error),
            )
            return

        self._set_joystick_settings_dirty(
            False
        )

        self.statusBar().showMessage(
            "Joystick settings saved."
        )

    def _refresh_profile_list(self):
        self._refresh_profile_strip()
        self._refresh_profiles_applications_status()
        self._refresh_profiles_applications_rules()

    def _profile_changed(
        self,
        filename: str,
    ):
        if not filename:
            return

        if (
            filename
            == self.manager.active_profile_name
        ):
            return

        profile_dirty = bool(
            getattr(
                self,
                "profile_dirty",
                False,
            )
        )

        joystick_dirty = bool(
            getattr(
                self,
                "joystick_settings_dirty",
                False,
            )
        )

        if profile_dirty or joystick_dirty:
            if profile_dirty and joystick_dirty:
                message = (
                    "The current profile and joystick "
                    "settings have unsaved changes.\n\n"
                    "Save them before switching profiles?"
                )

            elif profile_dirty:
                message = (
                    "The current profile has unsaved "
                    "changes.\n\n"
                    "Save them before switching profiles?"
                )

            else:
                message = (
                    "The joystick settings have unsaved "
                    "changes.\n\n"
                    "Save them before switching profiles?"
                )

            response = QMessageBox.warning(
                self,
                "Unsaved Changes",
                message,
                (
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel
                ),
                QMessageBox.StandardButton.Save,
            )

            if (
                response
                == QMessageBox.StandardButton.Cancel
            ):
                return

            if (
                response
                == QMessageBox.StandardButton.Save
            ):
                if (
                    profile_dirty
                    and not self._save_profile()
                ):
                    return

                if joystick_dirty:
                    self._save_joystick_settings()

                    if self.joystick_settings_dirty:
                        return

        try:
            self.manager.set_active_profile(
                filename,
                persist=False,
            )

            display_name = (
                self.manager.get_profile_display_name(
                    filename
                )
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Profile Error",
                str(error),
            )

            self._refresh_profile_list()
            return

        self._set_profile_dirty(
            False
        )

        self.selected_button = None
        self.selected_layer = 1

        self.layer_tabs.setCurrentIndex(0)

        self._reset_binding_editor()
        self._refresh_all_buttons()
        self._refresh_profile_strip()
        self._refresh_profiles_applications_status()
        self._load_joystick_settings()

        self.statusBar().showMessage(
            f"Loaded {display_name}"
        )

    def _create_profile(self):
        display_name, accepted = (
            GamingModeInputDialog.getText(
                self,
                "Create Profile",
                "Profile name:",
            )
        )

        if not accepted:
            return

        display_name = display_name.strip()

        if not display_name:
            return

        try:
            filename = self.manager.create_profile(
                display_name,
                activate=True,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Create Profile",
                str(error),
            )
            return

        self._refresh_profile_list()
        self._refresh_all_buttons()
        self._reset_binding_editor()
        self._load_joystick_settings()

        self.statusBar().showMessage(
            f"Created {display_name}"
        )

    def _duplicate_profile(self):
        source_filename = (
            self.manager.active_profile_name
        )

        if not source_filename:
            return

        profile = (
            self.manager.get_profile_copy()
        )

        current_display_name = str(
            profile.get(
                "name",
                Path(source_filename).stem,
            )
        )

        suggested_name = (
            f"{current_display_name} Copy"
        )

        display_name, accepted = (
            GamingModeInputDialog.getText(
                self,
                "Duplicate Profile",
                "New profile name:",
                QLineEdit.EchoMode.Normal,
                suggested_name,
            )
        )

        if not accepted:
            return

        display_name = display_name.strip()

        if not display_name:
            return

        try:
            filename = (
                self.manager.duplicate_profile(
                    source_filename,
                    display_name,
                    activate=False,
                )
            )

            self.manager.set_active_profile(
                filename,
                persist=False,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Duplicate Profile",
                str(error),
            )
            return

        self.selected_layer = 1
        self.selected_button = None

        self.layer_tabs.setCurrentIndex(0)

        self._refresh_profile_list()
        self._refresh_all_buttons()
        self._reset_binding_editor()
        self._load_joystick_settings()

        self.statusBar().showMessage(
            f"Created and loaded {display_name}"
        )

    def _rename_profile(self):
        source_filename = (
            self.manager.active_profile_name
        )

        if not source_filename:
            return

        profile = (
            self.manager.get_profile_copy()
        )

        current_display_name = str(
            profile.get(
                "name",
                Path(source_filename).stem,
            )
        )

        display_name, accepted = (
            GamingModeInputDialog.getText(
                self,
                "Rename Profile",
                "Profile name:",
                QLineEdit.EchoMode.Normal,
                current_display_name,
            )
        )

        if not accepted:
            return

        display_name = display_name.strip()

        if not display_name:
            return

        try:
            self.manager.rename_profile(
                source_filename,
                display_name,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Rename Profile",
                str(error),
            )
            return

        self._refresh_profile_list()
        self._refresh_all_buttons()

        if self.selected_button is not None:
            self._load_selected_binding()

        self.statusBar().showMessage(
            f"Renamed profile to {display_name}"
        )

    def _delete_profile(self):
        filename = (
            self.manager.active_profile_name
        )

        if not filename:
            return

        other_profiles = [
            profile_name
            for profile_name
            in self.manager.list_profiles()
            if profile_name != filename
        ]

        if not other_profiles:
            QMessageBox.information(
                self,
                "Delete Profile",
                (
                    "The last remaining profile "
                    "cannot be deleted."
                ),
            )
            return

        profile = (
            self.manager.get_profile_copy()
        )

        display_name = str(
            profile.get(
                "name",
                filename,
            )
        )

        answer = QMessageBox.question(
            self,
            "Delete Profile?",
            (
                f"Delete the profile:\n\n"
                f"{display_name}\n\n"
                "This cannot be undone."
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )

        if (
            answer
            != QMessageBox.StandardButton.Yes
        ):
            return

        saved_fallback = (
            self.manager.get_fallback_profile()
        )

        deleting_saved_fallback = (
            filename == saved_fallback
        )

        if saved_fallback in other_profiles:
            replacement_profile = (
                saved_fallback
            )
        else:
            replacement_profile = (
                other_profiles[0]
            )

        try:
            # The manager prevents deletion of the profile
            # currently open for editing, so open another
            # profile first.
            #
            # Only persist this change when the deleted
            # profile was also the saved fallback.
            self.manager.set_active_profile(
                replacement_profile,
                persist=deleting_saved_fallback,
            )

            self.manager.delete_profile(
                filename
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Delete Profile",
                str(error),
            )
            return

        self.selected_layer = 1
        self.selected_button = None

        self.layer_tabs.setCurrentIndex(0)

        self._refresh_profile_list()
        self._refresh_all_buttons()
        self._reset_binding_editor()
        self._load_joystick_settings()

        self.statusBar().showMessage(
            f"Deleted {filename}; "
            f"loaded {replacement_profile}"
        )

    def _set_profile_dirty(
        self,
        dirty: bool,
    ) -> None:
        self.profile_dirty = dirty

        if not hasattr(
            self,
            "header_save_button",
        ):
            return

        self.header_save_button.setEnabled(
            dirty
        )

        self.header_save_button.setText(
            "Save *"
            if dirty
            else "Save"
        )

        self.header_save_button.setToolTip(
            "Save unsaved profile changes"
            if dirty
            else "No unsaved profile changes"
        )

    def _reload_profile(self):
        try:
            self.manager.reload_active_profile()

        except Exception as error:
            QMessageBox.critical(
                self,
                "Reload Profile",
                str(error),
            )
            return

        self._set_profile_dirty(
            False
        )

        self._refresh_all_buttons()
        self._load_joystick_settings()

        if self.selected_button is not None:
            self._load_selected_binding()

        self.statusBar().showMessage(
            "Profile reloaded; "
            "unsaved changes discarded."
        )

    def _save_profile(self) -> bool:
        try:
            self.manager.save_active_profile()

        except Exception as error:
            QMessageBox.critical(
                self,
                "Save Profile",
                str(error),
            )
            return False

        self._set_profile_dirty(
            False
        )

        self._refresh_all_buttons()

        self.statusBar().showMessage(
            "Profile saved."
        )

        return True

    # ---------------------------------------------------------
    # Button and binding editing
    # ---------------------------------------------------------

    def _layer_tab_changed(self, index: int):
        self.selected_layer = index + 1

    def _select_button(
        self,
        layer: int,
        button_name: str,
    ):
        self.selected_command_id = None
        self.creating_profile_command = False
        self.selected_layer = layer
        self.selected_button = button_name

        for key, widget in (
            self.button_widgets.items()
        ):
            widget.setChecked(
                key == (layer, button_name)
            )

        self._load_selected_binding()
        self._update_binding_editor_mode()

    def _new_profile_command(
        self,
    ) -> None:
        self.selected_button = None
        self.selected_command_id = None
        self.creating_profile_command = True

        for widget in self.button_widgets.values():
            widget.setChecked(False)

        self.commands_list.clearSelection()

        self._reset_editor_fields()

        keyboard_index = (
            self.action_type_combo.findData(
                "keyboard"
            )
        )

        if keyboard_index >= 0:
            self.action_type_combo.setCurrentIndex(
                keyboard_index
            )

        self.action_name_edit.clear()
        self.key_combo.setCurrentText("")

        self.key_combo.setFocus()

        self._update_binding_editor_mode()

        self.statusBar().showMessage(
            "Creating new profile command."
        )

    def _deselect_profile_command(
        self,
    ) -> None:
        if (
            getattr(
                self,
                "selected_command_id",
                None,
            )
            is None
        ):
            return

        self.commands_list.clearSelection()

        self.commands_list.setCurrentItem(
            None
        )

        self.commands_list._clear_command_hover()

        self._reset_binding_editor()

        self.statusBar().showMessage(
            "Profile command deselected."
        )


    def _edit_profile_command(
        self,
        item,
    ) -> None:
        command_id = item.data(
            Qt.ItemDataRole.UserRole
        )

        if (
            not isinstance(command_id, str)
            or not command_id
        ):
            return

        try:
            commands = self.manager.get_commands()
            action = commands.get(command_id)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Edit Command",
                str(error),
            )
            return

        if not isinstance(action, dict):
            QMessageBox.critical(
                self,
                "Edit Command",
                "The selected command is invalid.",
            )
            return

        self.selected_button = None
        self.selected_command_id = command_id
        self.creating_profile_command = False

        for widget in self.button_widgets.values():
            widget.setChecked(False)

        self._load_selected_binding(
            action
        )
        self._update_binding_editor_mode()

        command_name = str(
            action.get(
                "name",
                "Unnamed Command",
            )
        )

        self.statusBar().showMessage(
            f"Editing profile command: "
            f"{command_name}"
        )

    def _delete_selected_profile_command(
        self,
    ) -> None:
        item = self.commands_list.currentItem()

        if item is None:
            QMessageBox.information(
                self,
                "Delete Command",
                "Select a profile command first.",
            )
            return

        command_id = item.data(
            Qt.ItemDataRole.UserRole
        )

        if (
            not isinstance(command_id, str)
            or not command_id
        ):
            return

        try:
            commands = self.manager.get_commands()
            action = commands.get(command_id)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Delete Command",
                str(error),
            )
            return

        if not isinstance(action, dict):
            return

        command_name = str(
            action.get(
                "name",
                "Unnamed Command",
            )
        )

        answer = QMessageBox.question(
            self,
            "Delete Command",
            (
                f"Delete '{command_name}'?\n\n"
                "Any G-key assignments using this "
                "command will also be cleared."
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self.manager.delete_command(
                command_id
            )

            self._set_profile_dirty(
                True
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Delete Command",
                str(error),
            )
            return

        if (
            getattr(
                self,
                "selected_command_id",
                None,
            )
            == command_id
        ):
            self._reset_binding_editor()

        self._refresh_all_buttons()

        self.statusBar().showMessage(
            f"Deleted profile command "
            f"{command_name} (unsaved)"
        )

    def _delete_profile_command_by_id(
        self,
        command_id: str,
    ) -> None:
        for index in range(
            self.commands_list.count()
        ):
            item = self.commands_list.item(
                index
            )

            if (
                item.data(
                    Qt.ItemDataRole.UserRole
                )
                == command_id
            ):
                self.commands_list.setCurrentItem(
                    item
                )
                self._delete_selected_profile_command()
                return

    def _raw_binding(
        self,
        layer: int,
        button: str,
    ) -> dict[str, Any] | None:
        profile = self.manager.get_profile_copy()

        layers = profile.get("layers", {})
        layer_bindings = layers.get(
            str(layer),
            {},
        )

        action = layer_bindings.get(button)

        if isinstance(action, dict):
            return action

        return None

    def _load_selected_binding(
        self,
        action_override: dict[str, Any] | None = None,
    ):
        if action_override is None:
            if self.selected_button is None:
                self._reset_binding_editor()
                return

            action = self._raw_binding(
                self.selected_layer,
                self.selected_button,
            )

        else:
            action = copy.deepcopy(
                action_override
            )

        self._reset_editor_fields()

        if action is None:
            self.action_type_combo.setCurrentIndex(
                -1
            )

            return

        action_type = action.get("type")

        index = self.action_type_combo.findData(
            action_type
        )

        if index < 0:
            self.action_type_combo.setCurrentIndex(
                -1
            )

            return

        self.action_type_combo.setCurrentIndex(
            index
        )

        self.action_name_edit.setText(
            str(action.get("name", ""))
        )

        if action_type == "keyboard":
            self.key_combo.setCurrentText(
                str(action.get("key", ""))
            )

            modifiers = action.get(
                "modifiers",
                [],
            )

            for modifier_name, checkbox in (
                self.modifier_checks.items()
            ):
                checkbox.setChecked(
                    modifier_name in modifiers
                )

        elif action_type == "mouse":
            button = action.get(
                "button"
            )

            if (
                button == "left"
                and action.get(
                    "click_count"
                ) == 2
            ):
                button = "double_left"

            index = (
                self.mouse_button_combo.findData(
                    button
                )
            )

            if index >= 0:
                self.mouse_button_combo.setCurrentIndex(
                    index
                )

        elif action_type == "wheel":
            direction = action.get(
                "direction",
                "vertical",
            )

            amount = int(
                action.get(
                    "amount",
                    1,
                )
            )

            if direction == "vertical":
                wheel_direction = (
                    "up"
                    if amount >= 0
                    else "down"
                )

            elif direction == "horizontal":
                wheel_direction = (
                    "right"
                    if amount >= 0
                    else "left"
                )

            else:
                wheel_direction = "up"

            index = (
                self.wheel_direction_combo.findData(
                    wheel_direction
                )
            )

            if index >= 0:
                self.wheel_direction_combo.setCurrentIndex(
                    index
                )

            self.wheel_amount_spin.setValue(
                max(
                    1,
                    abs(amount),
                )
            )

        elif action_type == "macro":

            execution_mode = action.get(
                "execution_mode",
                "once",
            )

            execution_index = (
                self.macro_execution_mode_combo
                .findData(execution_mode)
            )

            if execution_index >= 0:
                self.macro_execution_mode_combo.setCurrentIndex(
                    execution_index
                )

            self.macro_repeat_delay_spin.setValue(
                int(
                    action.get(
                        "repeat_delay_ms",
                        25,
                    )
                )
            )

            self.macro_allow_overlap_check.setChecked(
                bool(
                    action.get(
                        "allow_overlap",
                        False,
                    )
                )
            )

            raw_steps = action.get(
                "steps",
                [],
            )

            if isinstance(raw_steps, list):
                self.macro_steps = [
                    copy.deepcopy(step)
                    for step in raw_steps
                    if isinstance(step, dict)
                ]
            else:
                self.macro_steps = []

            self._refresh_macro_steps()

    def _reset_editor_fields(self):
        self.action_name_edit.clear()

        if KEY_NAMES:
            self.key_combo.setCurrentIndex(0)

        for checkbox in (
            self.modifier_checks.values()
        ):
            checkbox.setChecked(False)

        self.mouse_button_combo.setCurrentIndex(0)
        self.wheel_direction_combo.setCurrentIndex(0)
        self.wheel_amount_spin.setValue(1)

        self.macro_allow_overlap_check.setChecked(
            False
        )

        self.macro_steps = []
        self._refresh_macro_steps()

        once_index = (
            self.macro_execution_mode_combo
            .findData("once")
        )

        if once_index >= 0:
            self.macro_execution_mode_combo.setCurrentIndex(
                once_index
            )

        self.macro_repeat_delay_spin.setValue(
            25
        )

        self._macro_execution_mode_changed()

    def _reset_binding_editor(self):
        self.selected_button = None
        self.selected_command_id = None
        self.creating_profile_command = False

        for widget in self.button_widgets.values():
            widget.setChecked(False)

        self._reset_editor_fields()

        self.action_type_combo.setCurrentIndex(
            -1
        )

        self._update_binding_editor_mode()

    def _update_binding_editor_mode(
        self,
    ) -> None:
        if not all((
            hasattr(self, "binding_editor_title"),
            hasattr(self, "apply_binding_button"),
            hasattr(self, "clear_binding_button"),
        )):
            return

        creating_command = bool(
            getattr(
                self,
                "creating_profile_command",
                False,
            )
        )

        command_id = getattr(
            self,
            "selected_command_id",
            None,
        )

        if creating_command:
            self.binding_editor_title.setText(
                "Command Editor"
            )
            self.apply_binding_button.setText(
                "Create Command"
            )
            self.clear_binding_button.setText(
                "Cancel"
            )
            return

        if command_id is not None:
            self.binding_editor_title.setText(
                "Command Editor"
            )
            self.apply_binding_button.setText(
                "Update Command"
            )
            self.clear_binding_button.setText(
                "Cancel"
            )
            return

        self.binding_editor_title.setText(
            "Command Editor"
        )
        self.apply_binding_button.setText(
            "Apply Binding"
        )
        self.clear_binding_button.setText(
            "Clear Binding"
        )

    def _action_type_changed(
        self,
        index: int,
    ) -> None:
        if index < 0:
            self.action_stack.setCurrentIndex(
                0
            )
            return

        self.action_stack.setCurrentIndex(
            index + 1
        )

    def _default_action_name(
        self,
        action_type: str,
    ) -> str:
        if action_type == "keyboard":
            return self.key_combo.text().strip()

        if action_type == "mouse":
            return (
                self.mouse_button_combo
                .currentText()
            )

        if action_type == "wheel":
            return (
                "Scroll "
                f"{self.wheel_direction_combo.currentText()}"
            )
        if action_type == "macro":
            return "Macro"

        return ""

    def _build_action_from_editor(
        self,
    ) -> dict[str, Any] | None:
        action_type = (
            self.action_type_combo.currentData()
        )

        if action_type is None:
            return None

        name = self.action_name_edit.text().strip()

        if not name:
            name = self._default_action_name(
                action_type
            )

        if action_type == "keyboard":
            key_name = (
                self.key_combo.currentText().strip()
            )

            if not key_name:
                raise ValueError(
                    "Select a keyboard key."
                )

            action: dict[str, Any] = {
                "type": "keyboard",
                "name": name,
                "key": key_name,
            }

            modifiers = [
                modifier_name
                for modifier_name, checkbox
                in self.modifier_checks.items()
                if checkbox.isChecked()
            ]

            if modifiers:
                action["modifiers"] = modifiers

            return action

        if action_type == "mouse":
            button = (
                self.mouse_button_combo
                .currentData()
            )

            if button == "double_left":
                return {
                    "type": "mouse",
                    "name": name,
                    "button": "left",
                    "click_count": 2,
                }

            return {
                "type": "mouse",
                "name": name,
                "button": button,
            }

        if action_type == "wheel":
            wheel_direction = (
                self.wheel_direction_combo
                .currentData()
            )

            amount = (
                self.wheel_amount_spin.value()
            )

            if wheel_direction == "up":
                direction = "vertical"
                signed_amount = amount

            elif wheel_direction == "down":
                direction = "vertical"
                signed_amount = -amount

            elif wheel_direction == "right":
                direction = "horizontal"
                signed_amount = amount

            elif wheel_direction == "left":
                direction = "horizontal"
                signed_amount = -amount

            else:
                raise ValueError(
                    "Unsupported wheel direction: "
                    f"{wheel_direction}"
                )

            return {
                "type": "wheel",
                "name": name,
                "direction": direction,
                "amount": signed_amount,
            }

        if action_type == "macro":
            if not self.macro_steps:
                raise ValueError(
                    "A macro must contain at least "
                    "one step."
                )

            return {
                "type": "macro",
                "name": name,
                "execution_mode": (
                    self.macro_execution_mode_combo
                    .currentData()
                ),
                "repeat_delay_ms": (
                    self.macro_repeat_delay_spin
                    .value()
                ),
                "allow_overlap": (
                    self.macro_allow_overlap_check
                    .isChecked()
                ),
                "steps": copy.deepcopy(
                    self.macro_steps
                ),
            }

        raise ValueError(
            f"Unsupported action type: {action_type}"
        )

    def _apply_binding(self):
        command_id = getattr(
            self,
            "selected_command_id",
            None,
        )

        creating_command = bool(
            getattr(
                self,
                "creating_profile_command",
                False,
            )
        )

        if (
            self.selected_button is None
            and command_id is None
            and not creating_command
        ):
            QMessageBox.information(
                self,
                "Apply",
                (
                    "Select a G13 control or "
                    "profile command first."
                ),
            )
            return

        try:
            action = self._build_action_from_editor()

            if creating_command:
                if action is None:
                    raise ValueError(
                        "A profile command cannot be Unbound."
                    )

                command_id = (
                    self.manager.create_command(
                        action
                    )
                )

                self.selected_command_id = command_id
                self.creating_profile_command = False
                self._update_binding_editor_mode()

                self._set_profile_dirty(
                    True
                )

                self._refresh_all_buttons()

                for row in range(
                    self.commands_list.count()
                ):
                    item = self.commands_list.item(
                        row
                    )

                    if (
                        item is not None
                        and item.data(
                            Qt.ItemDataRole.UserRole
                        )
                        == command_id
                    ):
                        self.commands_list.setCurrentItem(
                            item
                        )
                        break

                self.statusBar().showMessage(
                    f"Created profile command "
                    f"{action.get('name', 'Unnamed Command')} "
                    f"(unsaved)"
                )

                return

            if command_id is not None:
                if action is None:
                    raise ValueError(
                        "A profile command cannot be Unbound."
                    )

                self.manager.update_command(
                    command_id,
                    action,
                )

                self._set_profile_dirty(
                    True
                )

                self._refresh_all_buttons()

                self.statusBar().showMessage(
                    f"Changed profile command "
                    f"{action.get('name', 'Unnamed Command')} "
                    f"(unsaved)"
                )

                return

            if action is None:
                self.manager.clear_binding(
                    self.selected_layer,
                    self.selected_button,
                )

            else:
                self.manager.set_command_binding(
                    self.selected_layer,
                    self.selected_button,
                    action,
                )

            self._set_profile_dirty(
                True
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Binding Error",
                str(error),
            )
            return

        self._refresh_all_buttons()

        self.statusBar().showMessage(
            f"Changed M{self.selected_layer} "
            f"{self.selected_button} "
            f"(unsaved)"
        )

    def _clear_binding(self):
        if self.selected_button is None:
            command_id = getattr(
                self,
                "selected_command_id",
                None,
            )

            creating_command = bool(
                getattr(
                    self,
                    "creating_profile_command",
                    False,
                )
            )

            if (
                command_id is not None
                or creating_command
            ):
                self._reset_binding_editor()
                self.commands_list.clearSelection()

                if creating_command:
                    message = (
                        "New profile command canceled."
                    )
                else:
                    message = (
                        "Profile command edit canceled."
                    )

                self.statusBar().showMessage(
                    message
                )

            return

        try:
            self.manager.clear_binding(
                self.selected_layer,
                self.selected_button,
            )

            self._set_profile_dirty(
                True
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Clear Binding",
                str(error),
            )
            return

        self._load_selected_binding()
        self._refresh_all_buttons()

        self.statusBar().showMessage(
            f"Cleared M{self.selected_layer} "
            f"{self.selected_button} "
            f"(unsaved)"
        )

    def _refresh_commands(
        self,
    ) -> None:
        if not hasattr(
            self,
            "commands_list",
        ):
            return

        self.commands_list.clear()

        try:
            commands = (
                self.manager
                .get_commands()
            )

        except Exception as error:
            self.statusBar().showMessage(
                f"Commands error: {error}"
            )
            return

        command_entries = []

        for (
            command_id,
            action,
        ) in commands.items():
            if not isinstance(
                action,
                dict,
            ):
                continue

            name = str(
                action.get(
                    "name",
                    "Unnamed Command",
                )
            )

            command_entries.append(
                (
                    name.casefold(),
                    name,
                    command_id,
                    action,
                )
            )

        command_entries.sort(
            key=lambda entry: (
                entry[0],
                entry[2],
            )
        )

        if not command_entries:
            self.commands_list.addItem(
                "(No profile commands)"
            )

            item = self.commands_list.item(
                0
            )

            if item is not None:
                item.setFlags(
                    Qt.ItemFlag.NoItemFlags
                )

            return

        for (
            _,
            name,
            command_id,
            action,
        ) in command_entries:
            self.commands_list.addItem(
                name
            )

            item = self.commands_list.item(
                self.commands_list.count()
                - 1
            )

            if item is None:
                continue

            item.setData(
                Qt.ItemDataRole.UserRole,
                command_id,
            )

            action_type = str(
                action.get(
                    "type",
                    "unknown",
                )
            )

            item.setToolTip(
                f"{name}\n"
                f"Type: {action_type}"
            )

    def _refresh_all_buttons(self):
        for (
            layer,
            button_name,
        ), widget in self.button_widgets.items():

            key = (
                layer,
                button_name,
            )

            assignment_label = (
                self.button_assignment_labels
                .get(key)
            )

            if assignment_label is None:
                continue

            action = self._raw_binding(
                layer,
                button_name,
            )

            if action is None:
                base_detail = widget.property(
                    "baseDetail"
                )

                if not isinstance(
                    base_detail,
                    str,
                ):
                    base_detail = ""

                assignment_label.setText(
                    base_detail
                )

                if assignment_label.property(
                    "floatingBindingBox"
                ):
                    assignment_label.setVisible(
                        bool(base_detail)
                    )

                widget.setToolTip(
                    "Unbound"
                )

                continue

            action_name = str(
                action.get(
                    "name",
                    "Assigned",
                )
            )

            if assignment_label.property(
                "floatingBindingBox"
            ):
                cleaned_name = " ".join(
                    action_name.split()
                )

                available_width = max(
                    1,
                    assignment_label.width() - 10,
                )

                text_width = (
                    assignment_label
                    .fontMetrics()
                    .horizontalAdvance(
                        cleaned_name
                    )
                )

                if text_width > available_width:
                    assignment_label.setAlignment(
                        Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter
                    )

                    display_name = (
                        assignment_label
                        .fontMetrics()
                        .elidedText(
                            cleaned_name,
                            Qt.TextElideMode.ElideRight,
                            available_width,
                        )
                    )

                else:
                    assignment_label.setAlignment(
                        Qt.AlignmentFlag.AlignCenter
                    )

                    display_name = cleaned_name

            else:
                assignment_label.setAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )

                display_name = (
                    _compact_assignment_name(
                        action_name
                    )
                )

            assignment_label.setText(
                display_name
            )

            if assignment_label.property(
                "floatingBindingBox"
            ):
                assignment_label.show()
                assignment_label.raise_()

            widget.setToolTip(
                action_name
            )

        self._refresh_backlight_buttons()
        self._refresh_commands()

class G13FusionStyle(QProxyStyle):
    def pixelMetric(
        self,
        metric,
        option=None,
        widget=None,
    ) -> int:
        if (
            metric
            == QStyle.PixelMetric.PM_IndicatorWidth
        ):
            return 16

        if (
            metric
            == QStyle.PixelMetric.PM_IndicatorHeight
        ):
            return 18

        return super().pixelMetric(
            metric,
            option,
            widget,
        )

    def drawPrimitive(
        self,
        element,
        option,
        painter,
        widget=None,
    ) -> None:
        if (
            element
            == QStyle.PrimitiveElement.PE_IndicatorCheckBox
        ):
            rect = option.rect.adjusted(
                1,
                2,
                -1,
                -2,
            )

            enabled = bool(
                option.state
                & QStyle.StateFlag.State_Enabled
            )

            checked = bool(
                option.state
                & QStyle.StateFlag.State_On
            )

            hovered = bool(
                option.state
                & QStyle.StateFlag.State_MouseOver
            )

            if enabled:
                background = _theme_color(
                    "BG_BUTTON"
                )

                border = _theme_color(
                    (
                        "BORDER_CHECKBOX_HOVER"
                        if hovered
                        else "BORDER_CHECKBOX"
                    )
                )

                mark = _theme_color(
                    "TEXT_PRIMARY"
                )

            else:
                background = _theme_color(
                    "BG_CHECKBOX_DISABLED"
                )
                border = _theme_color(
                    "BORDER_CHECKBOX_DISABLED"
                )
                mark = _theme_color(
                    "TEXT_DISABLED"
                )

            painter.save()

            painter.setRenderHint(
                QPainter.RenderHint.Antialiasing,
                True,
            )

            painter.setPen(
                QPen(
                    border,
                    1,
                )
            )

            painter.setBrush(
                background
            )

            painter.drawRoundedRect(
                rect,
                3,
                3,
            )

            if checked:
                mark_rect = rect.adjusted(
                    4,
                    4,
                    -4,
                    -4,
                )

                mark_pen = QPen(
                    mark,
                    2,
                )

                mark_pen.setCapStyle(
                    Qt.PenCapStyle.RoundCap
                )

                painter.setPen(
                    mark_pen
                )

                painter.drawLine(
                    mark_rect.topLeft(),
                    mark_rect.bottomRight(),
                )

                painter.drawLine(
                    mark_rect.topRight(),
                    mark_rect.bottomLeft(),
                )

            painter.restore()
            return

        super().drawPrimitive(
            element,
            option,
            painter,
            widget,
        )

def apply_style(app: QApplication):
    app.setStyle(
        G13FusionStyle("Fusion")
    )

    app.setStyleSheet(
        _theme_stylesheet("""
        QMainWindow,
        QWidget {
            background-color: $BG_WINDOW;
            color: $TEXT_PRIMARY;
            font-size: 13px;
        }

        QGroupBox {
            border: 1px solid $BORDER_DEFAULT;
            border-radius: 6px;
            margin-top: 12px;
            padding-top: 10px;
            font-weight: bold;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }

        QPushButton {
            background-color: $BG_BUTTON;
            border: 1px solid $BORDER_CONTROL;
            border-radius: 5px;
            padding: 7px;
        }

        QPushButton:hover {
            background-color: $BG_BUTTON_HOVER;
        }

        QPushButton:checked {
            background-color: $WARNING;
            border-color: $WARNING_HOVER;
            color: $TEXT_ON_ACCENT;
            font-weight: bold;
        }

        QPushButton[livePressed="true"] {
            background-color: $BG_LIVE;
            border: 2px solid $BORDER_LIVE;
            color: $TEXT_ON_LIVE;
            font-weight: bold;
        }

        QWidget#g13SplitControl {
            background-color: transparent;
        }

        QPushButton#g13ControlButton {
            border-top-right-radius: 0;
            border-bottom-right-radius: 0;
            margin: 0;
            padding: 0;
        }

        QPushButton#g13ControlButton[compactControl="true"] {
            border-radius: 2px;
            margin: 0;
            padding: 0;
        }

        QPushButton#g13ControlButton[imageOverlay="true"] {
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 3px;
            margin: 0;
            padding: 0;
        }

        QPushButton#g13ControlButton[imageOverlay="true"][overlayHover="true"] {
            background-color: $OVERLAY_CONTROL_HOVER;
            border: 1px solid $OVERLAY_CONTROL_HOVER_BORDER;
        }

        QPushButton#g13ControlButton
        [imageOverlay="true"]
        [shapedOverlay="true"]
        [overlayHover="true"] {
            border: none;
        }

        QPushButton#g13ControlButton[imageOverlay="true"]:checked {
            background-color: $OVERLAY_CONTROL_SELECTED;
            border: 2px solid $ACCENT;
        }

        QPushButton#g13ControlButton[imageOverlay="true"][livePressed="true"] {
            background-color: $OVERLAY_CONTROL_LIVE;
            border: 2px solid $BORDER_LIVE;
        }

        QPushButton#g13ControlButton
        [imageOverlay="true"]
        [shapedOverlay="true"] {
            border: none;
            border-radius: 0;
        }

        QPushButton#g13ControlButton
        [imageOverlay="true"]
        [shapedOverlay="true"]:checked {
            border: none;
        }

        QPushButton#g13ControlButton
        [imageOverlay="true"]
        [shapedOverlay="true"]
        [livePressed="true"] {
            border: none;
        }

        QPushButton#g13ControlButton:checked {
            background-color: $BG_ACCENT;
            border-color: $ACCENT;
        }

        QLabel#g13ControlKeyLabel {
            background-color: transparent;
            color: $WARNING;
            font-size: 11px;
            font-weight: bold;
        }

        QLabel#g13ControlAssignmentLabel {
            background-color: transparent;
            color: $TEXT_ASSIGNMENT;
            font-size: 9px;
        }

        QPushButton#g13ControlButton:checked
        QLabel#g13ControlKeyLabel,
        QPushButton#g13ControlButton:checked
        QLabel#g13ControlAssignmentLabel {
            color: $TEXT_ON_ACCENT;
        }

        QPushButton#g13ControlButton[livePressed="true"]
        QLabel#g13ControlKeyLabel,
        QPushButton#g13ControlButton[livePressed="true"]
        QLabel#g13ControlAssignmentLabel {
            color: $TEXT_ON_LIVE;
        }

        QPushButton#g13ControlMenuButton {
            border-left: none;
            border-top-left-radius: 0;
            border-bottom-left-radius: 0;
            margin: 0;
            padding: 0;
            font-size: 11px;
            font-weight: bold;
        }

        QPushButton#g13ControlMenuButton:hover {
            background-color: $BG_TEAL_HOVER;
            border-color: $ACCENT;
            color: $TEXT_BRIGHT;
        }

        QComboBox,
        QLineEdit,
        QSpinBox {
            background-color: $BG_INPUT;
            border: 1px solid $BORDER_CONTROL;
            border-radius: 4px;
            padding: 5px;
        }

        QLineEdit#keyCaptureEdit:focus {
            border: 2px solid $ACCENT;
        }

        QWidget#macroRepeatDelayControl,
        QWidget#macroRepeatDelayStepper {
            background-color: transparent;
            border: none;
        }

        QToolButton#macroRepeatDelayStepButton {
            background-color: $BG_STEPPER;
            border: 1px solid $BORDER_CONTROL;
            color: $TEXT_CONTROL;
            font-size: 8px;
            padding: 0px;
        }

        QToolButton#macroRepeatDelayStepButton:hover {
            background-color: $BG_STEPPER_HOVER;
            border-color: $BORDER_NEUTRAL_HOVER;
            color: $TEXT_BRIGHT;
        }

        QToolButton#macroRepeatDelayStepButton:pressed {
            background-color: $BG_STEPPER_PRESSED;
        }

        QToolButton#macroRepeatDelayStepButton:disabled {
            background-color: $BG_DISABLED_CONTROL;
            border-color: $BORDER_DISABLED_CONTROL;
            color: $TEXT_DISABLED_STEPPER;
        }

        QSpinBox#macroRepeatDelaySpin:disabled {
            background-color: $BG_DISABLED_CONTROL;
            border: 1px solid $BORDER_DISABLED_CONTROL;
            color: $TEXT_DISABLED_STEPPER;
        }

        QTabWidget::pane {
            border: 1px solid $BORDER_DEFAULT;
        }

        QTabBar::tab {
            background-color: $BG_TAB_DEFAULT;
            padding: 9px 24px;
            margin-right: 2px;
        }

        QTabBar::tab:selected {
            background-color: $WARNING;
            color: $TEXT_ON_ACCENT;
            font-weight: bold;
        }

        #titleLabel {
            font-size: 20px;
            font-weight: bold;
        }

        #headingLabel {
            font-weight: bold;
        }

        #selectedLabel {
            background-color: $BG_INPUT;
            border: 1px solid $BORDER_DEFAULT;
            border-radius: 5px;
            padding: 10px;
            font-size: 16px;
            font-weight: bold;
        }
        """)
    )

class EditorAlreadyRunning(RuntimeError):
    def __init__(
        self,
        pid: int | None,
    ) -> None:
        super().__init__(
            "G13 Zombie Control Center is already running."
        )

        self.pid = pid


def _acquire_editor_instance_lock(
) -> int:
    runtime_dir = Path(
        os.environ.get(
            "XDG_RUNTIME_DIR"
        )
        or f"/run/user/{os.getuid()}"
    )

    if not runtime_dir.is_absolute():
        raise RuntimeError(
            "The user runtime directory must "
            "be an absolute path."
        )

    runtime_info = runtime_dir.stat()

    if (
        not stat.S_ISDIR(
            runtime_info.st_mode
        )
        or runtime_info.st_uid
        != os.getuid()
    ):
        raise RuntimeError(
            "The user runtime directory must "
            "belong to the current user."
        )

    lock_path = (
        runtime_dir
        / (
            "g13-control-center-editor-"
            f"{os.getuid()}.lock"
        )
    )

    lock_fd = os.open(
        lock_path,
        (
            os.O_CREAT
            | os.O_RDWR
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
        ),
        0o600,
    )

    try:
        lock_info = os.fstat(
            lock_fd
        )

        if (
            not stat.S_ISREG(
                lock_info.st_mode
            )
            or lock_info.st_uid
            != os.getuid()
        ):
            raise RuntimeError(
                "The editor lock is not a "
                "user-owned regular file."
            )

        try:
            fcntl.flock(
                lock_fd,
                (
                    fcntl.LOCK_EX
                    | fcntl.LOCK_NB
                ),
            )

        except OSError as error:
            if error.errno not in {
                errno.EACCES,
                errno.EAGAIN,
            }:
                raise

            os.lseek(
                lock_fd,
                0,
                os.SEEK_SET,
            )

            raw_pid = os.read(
                lock_fd,
                64,
            ).decode(
                "ascii",
                errors="ignore",
            ).strip()

            try:
                existing_pid = int(
                    raw_pid
                )

            except ValueError:
                existing_pid = None

            raise EditorAlreadyRunning(
                existing_pid
            ) from None

        os.ftruncate(
            lock_fd,
            0,
        )

        os.lseek(
            lock_fd,
            0,
            os.SEEK_SET,
        )

        os.write(
            lock_fd,
            (
                f"{os.getpid()}\n"
            ).encode(
                "ascii"
            ),
        )

    except BaseException:
        os.close(
            lock_fd
        )

        raise

    return lock_fd

def main():
    app = QApplication(sys.argv)

    app.setApplicationName(
        "G13 Zombie Control Center"
    )

    app.setApplicationDisplayName(
        ""
    )

    app.setDesktopFileName(
        "zombie-control-center"
    )

    apply_style(app)

    try:
        editor_lock_fd = (
            _acquire_editor_instance_lock()
        )

    except EditorAlreadyRunning as error:
        pid_text = (
            str(error.pid)
            if error.pid is not None
            else "Unavailable"
        )

        QMessageBox.information(
            None,
            "Already Running",
            (
                "G13 Zombie Control Center is "
                "already running.\n\n"
                "Another editor instance is "
                "already active.\n\n"
                f"Running PID: {pid_text}\n\n"
                "Close this message and return "
                "to the existing window.\n\n"
                "If the existing instance is not "
                "responding, the PID above can be "
                "used to end it from a terminal."
            ),
        )

        return

    except Exception as error:
        QMessageBox.critical(
            None,
            "Startup Error",
            (
                "G13 Zombie Control Center could "
                "not create its editor instance "
                "lock.\n\n"
                f"{error}"
            ),
        )

        sys.exit(1)

    install_editor_theme_event_filter(
        app
    )

    window = G13Editor()
    show_editor(window)

    apply_gaming_mode_shell_theme(
        window
    )

    exit_code = app.exec()

    restart_after_exit = bool(
        app.property(
            "restartAfterExit"
        )
    )

    os.close(
        editor_lock_fd
    )

    if restart_after_exit:
        editor_path = (
            Path(__file__).resolve()
        )

        try:
            os.chdir(
                editor_path.parent
            )

            os.execv(
                sys.executable,
                [
                    sys.executable,
                    str(editor_path),
                    *sys.argv[1:],
                ],
            )

        except OSError as error:
            print(
                f"Unable to restart the editor: {error}",
                file=sys.stderr,
                flush=True,
            )

            sys.exit(1)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
