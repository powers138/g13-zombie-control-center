"""Host service launcher and optional pipe watchdog for the G13 driver.

The host owns the ONLY write end of an otherwise-unused stdin pipe. The
Distrobox client carries that pipe to the driver. A normal service stop closes
it and waits for the container command to exit. If the host launcher dies,
EOF also requests cooperative driver shutdown. No hardware modules are used.

Normal manual driver launches do not enable the watchdog.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable

CONTROL_ENVIRONMENT = "G13_DRIVER_SERVICE_CONTROL"
CONTROL_MODE = "stdin-pipe-v1"


def start_service_watchdog(
    request_stop: Callable[[], None],
) -> threading.Thread | None:
    """Watch the supervisor pipe, only for explicitly supervised launches.

    Call after the driver has acquired its single-instance lock. The callback
    must only request cooperative shutdown, not perform cleanup in this thread.
    Raw os.read avoids buffered-stdin locks during interpreter shutdown.
    """
    if os.environ.get(CONTROL_ENVIRONMENT) != CONTROL_MODE:
        return None

    if not stat.S_ISFIFO(os.fstat(0).st_mode):
        raise RuntimeError(
            "The supervised G13 driver requires its service control pipe."
        )

    descriptor = os.dup(0)
    os.set_inheritable(descriptor, False)

    def watch() -> None:
        try:
            while os.read(descriptor, 64):
                # No commands are sent through this pipe; only EOF matters.
                pass
        except OSError:
            # Losing the control channel must not leave an orphan driver.
            pass
        finally:
            os.close(descriptor)
            request_stop()

    thread = threading.Thread(
        target=watch,
        name="g13-service-watchdog",
        daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        os.close(descriptor)
        raise
    return thread


def _isolated_runtime_command(
    project_dir: Path,
    environment: dict[str, str],
) -> list[str]:
    """Build a production-only command; leave development launches unchanged.

    Reapply the required environment AFTER the container's login shell runs.
    Values travel as separate arguments, not interpolated shell source. The
    supervisor's stdin control pipe is left intact through every exec.
    """
    home = environment.get("HOME", "")
    if not os.path.isabs(home):
        raise ValueError("Production service HOME must be an absolute path.")

    values = {
        "PYTHONNOUSERSITE": "1",
        CONTROL_ENVIRONMENT: CONTROL_MODE,
    }
    for name, suffix in (
        ("XDG_DATA_HOME", ".local/share"),
        ("XDG_CONFIG_HOME", ".config"),
        ("XDG_STATE_HOME", ".local/state"),
    ):
        value = environment.get(name, "")
        values[name] = value if os.path.isabs(value) else str(Path(home) / suffix)

    runtime = environment.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not os.path.isabs(runtime):
        raise ValueError("Production service XDG_RUNTIME_DIR must be an absolute path.")
    values["XDG_RUNTIME_DIR"] = runtime
    values["DBUS_SESSION_BUS_ADDRESS"] = (
        environment.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime}/bus"
    )

    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["DBX_CONTAINER_MANAGER"] = "podman"
    environment.update(values)

    return [
        "/bin/bash", "-lc",
        'set -eu; cd -- "$1" || exit 1; shift; '
        'exec /usr/bin/env -u PYTHONHOME -u PYTHONPATH "$@" '
        './.venv/bin/python -u g13-test.py',
        "zombiecc-driver-service", str(project_dir),
        *(f"{name}={value}" for name, value in values.items()),
    ]


def _resolve_distrobox(
    distrobox: str | None,
) -> str:
    if distrobox is None:
        distrobox = shutil.which(
            "distrobox"
        )

    if not distrobox:
        raise RuntimeError(
            "Distrobox was not found in PATH."
        )

    if not os.path.isabs(distrobox):
        resolved = shutil.which(
            distrobox
        )

        if resolved is None:
            raise RuntimeError(
                f"Distrobox executable was not found: "
                f"{distrobox}"
            )

        distrobox = resolved

    if (
        not os.path.isfile(distrobox)
        or not os.access(
            distrobox,
            os.X_OK,
        )
    ):
        raise RuntimeError(
            f"Distrobox executable is not usable: "
            f"{distrobox}"
        )

    return distrobox


def run_service(
    project_dir: Path,
    container_name: str = "g13-fedora",
    distrobox: str | None = None,
    *,
    isolated_runtime: bool = False,
) -> int:
    """Run on the host; keep the driver control pipe open until a stop request.

    systemd must use KillMode=mixed: initially signal this supervisor only,
    allowing the container client to remain attached until driver cleanup ends.
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Run the service launcher on the main thread.")
    project_dir = project_dir.expanduser().resolve(strict=True)
    if not (project_dir / "g13-test.py").is_file():
        raise RuntimeError(f"g13-test.py is missing from {project_dir}.")
    if not container_name or container_name.startswith("-"):
        raise ValueError("A valid existing Distrobox container name is required.")

    distrobox = _resolve_distrobox(
        distrobox
    )

    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_handlers = {}
    process: subprocess.Popen[bytes] | None = None
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, request_stop)

        environment = os.environ.copy()
        environment[CONTROL_ENVIRONMENT] = CONTROL_MODE
        shell_command = [
            "bash",
            "-lc",
            'cd -- "$1" || exit 1; exec ./.venv/bin/python -u g13-test.py',
            "g13-driver-service",
            str(project_dir),
        ]
        if isolated_runtime:
            shell_command = _isolated_runtime_command(project_dir, environment)
        command = [
            distrobox, "enter", "--name", container_name, "--no-tty", "--",
            *shell_command,
        ]
        print(f"Starting G13 driver through Distrobox: {container_name}", flush=True)
        process = subprocess.Popen(
            command,
            cwd=project_dir,
            env=environment,
            stdin=subprocess.PIPE,
            # Driver output follows this service into the journal.
            stdout=None,
            stderr=None,
            close_fds=True,
            start_new_session=True,
        )
        assert process.stdin is not None

        while process.poll() is None:
            if stop_requested and not process.stdin.closed:
                print(
                    "Service stop requested; requesting driver cleanup.",
                    flush=True,
                )
                process.stdin.close()
            time.sleep(0.05)

        code = process.wait()
        print(f"Distrobox driver command exited: {code}", flush=True)
        return code if code >= 0 else 128 - code
    finally:
        if process is not None and process.stdin is not None:
            # EOF also protects against an unexpected client/supervisor exit.
            process.stdin.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the G13 driver under a host user service."
    )
    parser.add_argument("--container", default="g13-fedora")

    parser.add_argument(
        "--distrobox",
        default=None,
        help=(
            "Absolute path to the Distrobox executable. "
            "If omitted, locate distrobox through PATH."
        ),
    )
    parser.add_argument(
        "--isolated-runtime", action="store_true",
        help="Use production Python isolation and preserve the host XDG paths.",
    )
    arguments = parser.parse_args()

    if (
        os.environ.get("CONTAINER_ID")
        or Path("/run/.containerenv").exists()
        or Path("/.dockerenv").exists()
    ):
        print("Run this launcher on the host, not inside Distrobox.", file=sys.stderr)
        return 1

    try:
        return run_service(
            Path(__file__).resolve().parent, arguments.container,
            distrobox=arguments.distrobox,
            isolated_runtime=arguments.isolated_runtime,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"G13 service launch failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
