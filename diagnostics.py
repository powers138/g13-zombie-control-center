from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import os
from pathlib import Path
import platform

from app_paths import (
    CONFIG_DIR,
    DIAGNOSTICS_DIR,
    LOGS_DIR,
    PROFILES_DIR,
    STATE_DIR,
)
from app_version import APP_VERSION


def _single_line(
    value: object | None,
    fallback: str,
) -> str:
    if value is None:
        return fallback

    text = " ".join(
        str(value).split()
    )

    return text or fallback


def _local_timestamp(
    generated_at: datetime | None,
) -> datetime:
    timestamp = (
        generated_at
        if generated_at is not None
        else datetime.now().astimezone()
    )

    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()

    return timestamp


def build_diagnostic_report(
    *,
    qt_version: str,
    qt_platform: str,
    editor_path: Path,
    driver_connected: bool,
    active_profile: str | None,
    profile_source: str | None,
    window_class: str,
    environment: Mapping[str, str] | None = None,
    generated_at: datetime | None = None,
) -> str:
    env = (
        os.environ
        if environment is None
        else environment
    )

    timestamp = _local_timestamp(
        generated_at
    )

    def environment_value(
        name: str,
    ) -> str:
        return _single_line(
            env.get(name),
            "(unset)",
        )

    return (
        "G13 Zombie Control Center Diagnostics\n"
        f"Version: {APP_VERSION}\n"
        f"Generated: "
        f"{timestamp.isoformat(timespec='seconds')}\n"
        "\n"
        "Application\n"
        "-----------\n"
        f"Python: {platform.python_version()}\n"
        f"Qt: {_single_line(qt_version, '(unknown)')}\n"
        f"Qt platform: "
        f"{_single_line(qt_platform, '(unknown)')}\n"
        f"Editor path: {editor_path}\n"
        "\n"
        "Session\n"
        "-------\n"
        f"XDG_CURRENT_DESKTOP: "
        f"{environment_value('XDG_CURRENT_DESKTOP')}\n"
        f"DESKTOP_SESSION: "
        f"{environment_value('DESKTOP_SESSION')}\n"
        f"XDG_SESSION_TYPE: "
        f"{environment_value('XDG_SESSION_TYPE')}\n"
        "\n"
        "Driver\n"
        "------\n"
        f"Connected: "
        f"{'Yes' if driver_connected else 'No'}\n"
        "\n"
        "Runtime\n"
        "-------\n"
        f"Active profile: "
        f"{_single_line(active_profile, '(unknown)')}\n"
        f"Profile source: "
        f"{_single_line(profile_source, '(unknown)')}\n"
        f"Window class: "
        f"{_single_line(window_class, '(none)')}\n"
        "\n"
        "Paths\n"
        "-----\n"
        f"Profiles: {PROFILES_DIR}\n"
        f"Configuration: {CONFIG_DIR}\n"
        f"State: {STATE_DIR}\n"
        f"Logs: {LOGS_DIR}\n"
        f"Diagnostics: {DIAGNOSTICS_DIR}\n"
    )


def write_diagnostic_report(
    *,
    qt_version: str,
    qt_platform: str,
    editor_path: Path,
    driver_connected: bool,
    active_profile: str | None,
    profile_source: str | None,
    window_class: str,
    environment: Mapping[str, str] | None = None,
    generated_at: datetime | None = None,
    output_dir: Path = DIAGNOSTICS_DIR,
) -> Path:
    timestamp = _local_timestamp(
        generated_at
    )

    report = build_diagnostic_report(
        qt_version=qt_version,
        qt_platform=qt_platform,
        editor_path=editor_path,
        driver_connected=driver_connected,
        active_profile=active_profile,
        profile_source=profile_source,
        window_class=window_class,
        environment=environment,
        generated_at=timestamp,
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    if (
        output_dir.is_symlink()
        or not output_dir.is_dir()
    ):
        raise RuntimeError(
            "Diagnostics path is not a normal directory: "
            f"{output_dir}"
        )

    output_dir.chmod(
        0o700
    )

    report_path = (
        output_dir
        / (
            "zombiecc-diagnostics-"
            f"{timestamp:%Y%m%d-%H%M%S-%f}.txt"
        )
    )

    file_descriptor = os.open(
        report_path,
        (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
        ),
        0o600,
    )

    try:
        os.fchmod(
            file_descriptor,
            0o600,
        )

        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            file_descriptor = -1

            handle.write(
                report
            )

            handle.flush()

            os.fsync(
                handle.fileno()
            )

    finally:
        if file_descriptor >= 0:
            os.close(
                file_descriptor
            )

    return report_path
