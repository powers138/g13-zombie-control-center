from __future__ import annotations

from pathlib import Path

from app_paths import (
    DIAGNOSTICS_DIR,
    FACTORY_DEFAULT_PROFILE_FILE,
    FACTORY_SETTINGS_FILE,
    LOGS_DIR,
    PROFILES_DIR,
    SETTINGS_FILE,
)


DEFAULT_PROFILE_FILENAME = "default.json"


def _copy_factory_file_if_missing(
    source: Path,
    destination: Path,
) -> bool:
    if destination.exists():
        return False

    try:
        content = source.read_bytes()
    except OSError as error:
        raise RuntimeError(
            "Factory file could not be read:\n"
            f"{source}"
        ) from error

    try:
        with destination.open("xb") as output:
            output.write(content)

    except FileExistsError:
        # Another Zombie process may have created the
        # file between the existence check and the
        # exclusive create.
        return False

    return True


def bootstrap_user_data(
    *,
    profiles_dir: Path = PROFILES_DIR,
    settings_file: Path = SETTINGS_FILE,
    logs_dir: Path = LOGS_DIR,
    diagnostics_dir: Path = DIAGNOSTICS_DIR,
    factory_settings_file: Path = (
        FACTORY_SETTINGS_FILE
    ),
    factory_default_profile_file: Path = (
        FACTORY_DEFAULT_PROFILE_FILE
    ),
) -> None:
    profiles_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    settings_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    logs_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    diagnostics_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    _copy_factory_file_if_missing(
        factory_settings_file,
        settings_file,
    )

    has_user_profiles = any(
        profile_path.is_file()
        for profile_path
        in profiles_dir.glob("*.json")
    )

    if not has_user_profiles:
        _copy_factory_file_if_missing(
            factory_default_profile_file,
            profiles_dir
            / DEFAULT_PROFILE_FILENAME,
        )
