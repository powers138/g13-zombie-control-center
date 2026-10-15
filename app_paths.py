from __future__ import annotations

import os
from pathlib import Path


APP_DIRECTORY_NAME = "zombiecc"

APP_ROOT_DIR = (
    Path(__file__).resolve().parent
)

FACTORY_DEFAULTS_DIR = (
    APP_ROOT_DIR
    / "resources"
    / "defaults"
)

FACTORY_SETTINGS_FILE = (
    FACTORY_DEFAULTS_DIR
    / "settings.json"
)

FACTORY_DEFAULT_PROFILE_FILE = (
    FACTORY_DEFAULTS_DIR
    / "default-profile.json"
)


def _xdg_home(
    environment_name: str,
    fallback: Path,
) -> Path:
    raw_value = os.environ.get(
        environment_name,
        "",
    ).strip()

    if raw_value:
        candidate = Path(
            raw_value
        ).expanduser()

        if candidate.is_absolute():
            return candidate

    return fallback


HOME_DIR = Path.home()

XDG_DATA_HOME = _xdg_home(
    "XDG_DATA_HOME",
    HOME_DIR / ".local" / "share",
)

XDG_CONFIG_HOME = _xdg_home(
    "XDG_CONFIG_HOME",
    HOME_DIR / ".config",
)

XDG_STATE_HOME = _xdg_home(
    "XDG_STATE_HOME",
    HOME_DIR / ".local" / "state",
)


APP_DATA_DIR = (
    XDG_DATA_HOME
    / APP_DIRECTORY_NAME
)

PROFILES_DIR = (
    APP_DATA_DIR
    / "profiles"
)

CONFIG_DIR = (
    XDG_CONFIG_HOME
    / APP_DIRECTORY_NAME
)

SETTINGS_FILE = (
    CONFIG_DIR
    / "settings.json"
)

STATE_DIR = (
    XDG_STATE_HOME
    / APP_DIRECTORY_NAME
)

LOGS_DIR = (
    STATE_DIR
    / "logs"
)

DIAGNOSTICS_DIR = (
    STATE_DIR
    / "diagnostics"
)
