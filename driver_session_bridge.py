from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping


UNIT = "zombie-control-center-driver.service"
POLL_SECONDS = 0.5

SESSION_ENVIRONMENT = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_DESKTOP",
    "DESKTOP_SESSION",
)


def is_bridge_session(
    environment: Mapping[str, str],
) -> bool:
    desktop = environment.get(
        "XDG_CURRENT_DESKTOP",
        "",
    ).casefold()

    session = environment.get(
        "DESKTOP_SESSION",
        "",
    ).casefold()

    return (
        "xfce" in desktop
        or "xfce" in session
        or "x-cinnamon" in desktop
    )


def import_session_environment(
    environment: Mapping[str, str],
    *,
    run=subprocess.run,
) -> None:
    names = [
        name
        for name in SESSION_ENVIRONMENT
        if environment.get(name)
    ]

    if not names:
        return

    run(
        [
            "systemctl",
            "--user",
            "import-environment",
            *names,
        ],
        check=True,
        env=dict(environment),
    )


def get_session_state(
    session_id: str,
    *,
    run=subprocess.run,
) -> str | None:
    result = run(
        [
            "loginctl",
            "show-session",
            session_id,
            "--property=State",
            "--value",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    state = result.stdout.strip()

    return state or None


def run_bridge(
    environment: Mapping[str, str],
    *,
    run=subprocess.run,
    sleep=time.sleep,
    stop_requested: Callable[[], bool] = lambda: False,
) -> int:
    if not is_bridge_session(
        environment
    ):
        return 0

    session_id = environment.get(
        "XDG_SESSION_ID",
        "",
    ).strip()

    if not session_id:
        print(
            "Zombie driver session bridge: "
            "XDG_SESSION_ID is unavailable.",
            file=sys.stderr,
        )
        return 1

    import_session_environment(
        environment,
        run=run,
    )

    run(
        [
            "systemctl",
            "--user",
            "start",
            UNIT,
        ],
        check=True,
    )

    try:
        while not stop_requested():
            state = get_session_state(
                session_id,
                run=run,
            )

            if (
                state is None
                or state == "closing"
            ):
                break

            sleep(
                POLL_SECONDS
            )

    finally:
        run(
            [
                "systemctl",
                "--user",
                "stop",
                UNIT,
            ],
            check=False,
        )

    return 0


def main() -> int:
    stop = False

    def request_stop(
        signum,
        frame,
    ) -> None:
        nonlocal stop
        stop = True

    previous_sigterm = signal.signal(
        signal.SIGTERM,
        request_stop,
    )

    previous_sigint = signal.signal(
        signal.SIGINT,
        request_stop,
    )

    try:
        return run_bridge(
            os.environ,
            stop_requested=lambda: stop,
        )

    except (
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(
            "Zombie driver session bridge:",
            error,
            file=sys.stderr,
        )
        return 1

    finally:
        signal.signal(
            signal.SIGTERM,
            previous_sigterm,
        )

        signal.signal(
            signal.SIGINT,
            previous_sigint,
        )


if __name__ == "__main__":
    raise SystemExit(main())
