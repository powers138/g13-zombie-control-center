from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveWindow:
    window_id: str
    window_class: str
    pid: int | None
    title: str

GAMESCOPE_DISPLAY = ":0"
GAMESCOPE_STEAM_APP_ID = 769


def _get_active_window_gamescope(
) -> ActiveWindow | None:
    environment = os.environ.copy()

    environment[
        "DISPLAY"
    ] = GAMESCOPE_DISPLAY

    try:
        result = subprocess.run(
            [
                "xprop",
                "-root",
                "GAMESCOPE_FOCUSED_APP_GFX",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
            env=environment,
        )

    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
    ):
        return None

    output = result.stdout.strip()

    if "=" not in output:
        return None

    raw_app_id = (
        output.rsplit(
            "=",
            1,
        )[1]
        .strip()
    )

    try:
        app_id = int(
            raw_app_id
        )

    except ValueError:
        return None

    if app_id <= 0:
        return None

    if app_id == GAMESCOPE_STEAM_APP_ID:
        return ActiveWindow(
            window_id=(
                f"gamescope:{app_id}"
            ),
            window_class="",
            pid=None,
            title="Steam Gaming Mode",
        )

    return ActiveWindow(
        window_id=(
            f"gamescope:{app_id}"
        ),
        window_class=(
            f"steam_app_{app_id}"
        ),
        pid=None,
        title=(
            f"Steam AppID {app_id}"
        ),
    )

def _run_kdotool(
    *arguments: str,
) -> str:
    try:
        result = subprocess.run(
            [
                "kdotool",
                *arguments,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )

    except FileNotFoundError as error:
        raise RuntimeError(
            "kdotool is not installed."
        ) from error

    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "kdotool did not respond."
        ) from error

    except subprocess.CalledProcessError as error:
        message = error.stderr.strip()

        if not message:
            message = error.stdout.strip()

        if not message:
            message = (
                f"kdotool exited with status "
                f"{error.returncode}."
            )

        raise RuntimeError(message) from error

    return result.stdout.strip()


def _get_active_window_kde() -> ActiveWindow | None:
    try:
        window_id = _run_kdotool(
            "getactivewindow"
        )

    except RuntimeError:
        return None

    if not window_id:
        return None

    try:
        window_class = _run_kdotool(
            "getwindowclassname",
            window_id,
        )
    except RuntimeError:
        window_class = ""

    try:
        title = _run_kdotool(
            "getwindowname",
            window_id,
        )
    except RuntimeError:
        title = ""

    try:
        pid_text = _run_kdotool(
            "getwindowpid",
            window_id,
        )

        pid = int(pid_text)

    except (RuntimeError, ValueError):
        pid = None

    return ActiveWindow(
        window_id=window_id,
        window_class=window_class,
        pid=pid,
        title=title,
    )

def _run_xprop(*arguments: str) -> str:
    try:
        result = subprocess.run(
            ["xprop", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
    ):
        return ""

    return result.stdout.strip()


def _xprop_string(line: str) -> str:
    if "=" not in line:
        return ""

    value = line.split("=", 1)[1].strip()

    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return ""

    return parsed if isinstance(parsed, str) else ""


def _get_active_window_x11() -> ActiveWindow | None:
    root_property = _run_xprop(
        "-root",
        "_NET_ACTIVE_WINDOW",
    )

    if "#" not in root_property:
        return None

    window_id = root_property.rsplit("#", 1)[1].strip()

    if not window_id or window_id in {"0", "0x0"}:
        return None

    properties = _run_xprop(
        "-id",
        window_id,
        "WM_CLASS",
        "_NET_WM_NAME",
        "WM_NAME",
        "_NET_WM_PID",
    )

    if not properties:
        return None

    window_class = ""
    title = ""
    fallback_title = ""
    pid = None

    for line in properties.splitlines():
        if line.startswith("WM_CLASS") and "=" in line:
            value = line.split("=", 1)[1].strip()

            try:
                classes = ast.literal_eval(f"({value})")
            except (SyntaxError, ValueError):
                classes = ()

            if isinstance(classes, tuple):
                values = [
                    item
                    for item in classes
                    if isinstance(item, str) and item
                ]

                if values:
                    window_class = values[-1]

        elif line.startswith("_NET_WM_NAME"):
            title = _xprop_string(line)

        elif line.startswith("WM_NAME"):
            fallback_title = _xprop_string(line)

        elif line.startswith("_NET_WM_PID") and "=" in line:
            try:
                pid = int(
                    line.split("=", 1)[1].strip()
                )
            except ValueError:
                pid = None

    return ActiveWindow(
        window_id=window_id,
        window_class=window_class,
        pid=pid,
        title=title or fallback_title,
    )

def _get_active_window_gnome_wayland(
) -> ActiveWindow | None:
    try:
        result = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Shell",
                "--object-path",
                (
                    "/org/gnome/shell/extensions/"
                    "FocusedWindow"
                ),
                "--method",
                (
                    "org.gnome.shell.extensions."
                    "FocusedWindow.Get"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )

    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
    ):
        return None

    output = result.stdout.strip()

    if not output:
        return None

    try:
        response = ast.literal_eval(
            output
        )

    except (
        SyntaxError,
        ValueError,
    ):
        return None

    if (
        not isinstance(response, tuple)
        or not response
        or not isinstance(response[0], str)
    ):
        return None

    try:
        window_data = json.loads(
            response[0]
        )

    except json.JSONDecodeError:
        return None

    if not isinstance(window_data, dict):
        return None

    if window_data.get("focus") is False:
        return None

    raw_window_id = window_data.get(
        "id"
    )

    if raw_window_id is None:
        return None

    window_class = (
        window_data.get("wm_class")
        or window_data.get(
            "wm_class_instance"
        )
        or ""
    )

    title = (
        window_data.get("title")
        or ""
    )

    raw_pid = window_data.get(
        "pid"
    )

    try:
        pid = int(raw_pid)

    except (
        TypeError,
        ValueError,
    ):
        pid = None

    return ActiveWindow(
        window_id=str(
            raw_window_id
        ),
        window_class=str(
            window_class
        ),
        pid=pid,
        title=str(
            title
        ),
    )


def get_active_window() -> ActiveWindow | None:
    gamescope_window = (
        _get_active_window_gamescope()
    )

    if gamescope_window is not None:
        return gamescope_window

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
        return _get_active_window_gnome_wayland()

    if "kde" in desktop or "plasma" in desktop:
        return _get_active_window_kde()

    if session_type == "x11":
        return _get_active_window_x11()

    return _get_active_window_kde()

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Display the active window identity."
        )
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help=(
            "Seconds to wait before checking "
            "the active window."
        ),
    )

    arguments = parser.parse_args()

    if arguments.delay > 0:
        print(
            f"Checking active window in "
            f"{arguments.delay:g} seconds..."
        )

        time.sleep(arguments.delay)

    window = get_active_window()

    if window is None:
        print("No active window detected.")
        return

    print(
        f"Window ID: {window.window_id}"
    )

    print(
        f"Class: {window.window_class}"
    )

    print(
        "PID:",
        window.pid
        if window.pid is not None
        else "unknown",
    )

    print(
        f"Title: {window.title}"
    )


if __name__ == "__main__":
    main()
