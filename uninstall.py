#!/usr/bin/env python3
"""Safely uninstall G13 Zombie Control Center for the current user.

By default, remove only the installed application/runtime integration and keep
profiles, settings, logs, migration snapshots and updater backups for a future
reinstall. Use --purge-user-data only when those user files should also be
permanently deleted.

Run on the Linux host as the normal user, beside the release/install scripts.
Development environments, unrelated containers, Steam shortcuts, device
permissions and G13 onboard memory are never modified here.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Mapping

import install_desktop
import install_driver_service
import update_app as payload_tools


UNIT = install_driver_service.UNIT
DROPIN = install_driver_service.DROPIN
CONTAINER = payload_tools.CONTAINER
ENTRY_NAME = install_desktop.ENTRY_NAME


class UninstallError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise UninstallError(message)


@dataclass(frozen=True)
class Paths:
    payload: payload_tools.Layout
    desktop: install_desktop.Layout
    service: install_driver_service.Layout

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str],
    ) -> "Paths":
        payload = payload_tools.Layout.from_environment(env)
        desktop = install_desktop.make_layout(env)
        service = install_driver_service.Layout.from_environment(env)

        require(
            payload.home == desktop.home
            and payload.data == desktop.data
            and payload.state == desktop.state
            and service.payload == payload
            and service.config == desktop.config,
            "Installer path helpers disagree about the current XDG layout.",
        )

        return cls(payload, desktop, service)

    @property
    def profiles(self) -> Path:
        return self.payload.base / "profiles"

    @property
    def config_root(self) -> Path:
        return self.desktop.config / "zombiecc"

    @property
    def state_root(self) -> Path:
        return self.payload.state / "zombiecc"

    @property
    def lock(self) -> Path:
        return self.payload.base / ".install.lock"


def _read(
    path: Path,
    *,
    missing: bool = False,
) -> payload_tools.Blob | None:
    try:
        return payload_tools.read_blob(
            path,
            missing=missing,
        )
    except payload_tools.UpdateError as error:
        raise UninstallError(str(error)) from error


def _directory(path: Path) -> None:
    try:
        payload_tools.directory(path)
    except (payload_tools.UpdateError, OSError) as error:
        raise UninstallError(str(error)) from error


def _payload_parent_directories(
    payload: payload_tools.Payload,
) -> set[Path]:
    parents: set[Path] = set()

    for name in payload.order:
        parent = Path(name).parent

        while parent != Path("."):
            parents.add(parent)
            parent = parent.parent

    return parents


def check_application_tree(
    paths: Paths,
    payload: payload_tools.Payload,
) -> None:
    """Refuse to delete unexpected persistent files from the app tree.

    The venv and __pycache__ directories are disposable runtime products, so
    they are not enumerated file-by-file.
    """

    app = paths.payload.app
    allowed_files = set(payload.order) | {payload_tools.MARKER}
    allowed_directories = _payload_parent_directories(payload)

    for root_text, directories, files in os.walk(
        app,
        topdown=True,
        followlinks=False,
    ):
        root = Path(root_text)
        relative_root = root.relative_to(app)

        if relative_root == Path(".venv"):
            directories[:] = []
            continue

        if "__pycache__" in relative_root.parts:
            directories[:] = []
            continue

        for name in directories:
            path = root / name
            relative = path.relative_to(app)
            info = path.lstat()

            require(
                stat.S_ISDIR(info.st_mode)
                and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == os.getuid(),
                f"Unexpected/symlinked application directory: {path}",
            )

            if relative == Path(".venv") or name == "__pycache__":
                continue

            require(
                relative in allowed_directories,
                f"Untracked application directory needs review before uninstall: {path}",
            )

        for name in files:
            path = root / name
            relative = path.relative_to(app)

            if "__pycache__" in relative.parts:
                continue

            require(
                relative.as_posix() in allowed_files,
                f"Untracked application file needs review before uninstall: {path}",
            )

            _read(path)

    _directory(app / ".venv")
    _read(app / ".venv/pyvenv.cfg")


def validate_installation(
    paths: Paths,
) -> tuple[
    payload_tools.Payload,
    payload_tools.Blob,
    payload_tools.Blob,
]:
    layout = paths.payload

    try:
        payload_tools.basic_installation(layout)
    except (payload_tools.UpdateError, OSError) as error:
        raise UninstallError(str(error)) from error

    require(
        not os.path.lexists(layout.pending),
        "An unfinished update exists. Keep Zombie closed and recover that "
        "update before uninstalling.",
    )

    try:
        payload = payload_tools.Payload.load(layout.app)
        launcher = payload_tools.read_blob(layout.launcher)
        receipt = payload_tools.marker(layout)
    except (payload_tools.UpdateError, OSError, ValueError) as error:
        raise UninstallError(str(error)) from error

    assert launcher is not None

    require(
        payload.digest(launcher.data).encode()
        == receipt.data.strip(),
        "Installed application/launcher fingerprint differs from the known "
        "Stage 1/update format. Nothing will be removed.",
    )

    check_application_tree(paths, payload)

    return payload, launcher, receipt


def expected_file(
    path: Path,
    expected: bytes,
    *,
    description: str,
) -> payload_tools.Blob | None:
    value = _read(path, missing=True)

    if value is None:
        return None

    require(
        value.data == expected,
        f"Different {description} already exists; leaving it untouched: {path}",
    )

    return value


class HostTools:
    def __init__(self, env: Mapping[str, str]):
        self.env = dict(env)
        self.env["DBX_CONTAINER_MANAGER"] = "podman"
        self.env["SYSTEMD_PAGER"] = "cat"
        self.env["SYSTEMD_COLORS"] = "0"

        self.systemctl = self._find("systemctl")
        self.podman = self._find("podman")
        self.distrobox = self._find("distrobox")

    def _find(self, name: str) -> str:
        found = shutil.which(
            name,
            path=self.env.get("PATH", os.defpath),
        )
        require(found is not None, f"Required host command missing: {name}")
        return str(found)

    def call(
        self,
        command: list[str],
        *,
        timeout: int = 45,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            env=self.env,
            cwd="/",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            close_fds=True,
        )

    def run(
        self,
        command: list[str],
        *,
        timeout: int = 45,
    ) -> str:
        result = self.call(command, timeout=timeout)

        require(
            result.returncode == 0,
            f"Command failed ({result.returncode}): "
            f"{' '.join(command[:4])}\n"
            + result.stdout[-6000:]
            + result.stderr[-6000:],
        )

        return result.stdout

    def service_state(self) -> dict[str, str]:
        result = self.call(
            [
                self.systemctl,
                "--user",
                "show",
                UNIT,
                "--no-pager",
                "--property=LoadState,ActiveState,SubState,UnitFileState,FragmentPath,MainPID",
            ]
        )

        values: dict[str, str] = {}

        for line in result.stdout.splitlines():
            name, separator, value = line.partition("=")
            if separator:
                values[name] = value

        require(
            result.returncode == 0
            or (
                result.returncode == 4
                and values.get("LoadState") == "not-found"
            ),
            "Cannot query the production user service.\n"
            + result.stderr[-4000:],
        )

        require(
            "LoadState" in values
            and "ActiveState" in values,
            "Incomplete production service state.",
        )

        return values

    def disable_and_stop_service(self) -> None:
        state = self.service_state()

        if state.get("LoadState") == "not-found":
            return

        self.run(
            [
                self.systemctl,
                "--user",
                "disable",
                "--now",
                UNIT,
            ],
            timeout=120,
        )

        state = self.service_state()

        require(
            state.get("ActiveState") == "inactive",
            "Production driver did not stop cleanly.",
        )

        require(
            state.get("UnitFileState", "")
            in {"", "disabled"},
            "Production driver did not become disabled.",
        )

    def daemon_reload(self) -> None:
        self.run(
            [
                self.systemctl,
                "--user",
                "daemon-reload",
            ]
        )

    def container_info(self) -> dict | None:
        result = self.call(
            [
                self.podman,
                "container",
                "exists",
                CONTAINER,
            ]
        )

        if result.returncode == 1:
            return None

        require(
            result.returncode == 0,
            f"Cannot check production container (exit {result.returncode}).",
        )

        records = json.loads(
            self.run(
                [
                    self.podman,
                    "container",
                    "inspect",
                    CONTAINER,
                ]
            )
        )

        require(
            isinstance(records, list)
            and len(records) == 1
            and isinstance(records[0], dict),
            "Unexpected production-container metadata.",
        )

        return records[0]

    def remove_container(self) -> None:
        if self.container_info() is None:
            return

        self.run(
            [
                self.distrobox,
                "rm",
                CONTAINER,
                "--force",
                "--yes",
            ],
            timeout=180,
        )

        require(
            self.container_info() is None,
            "Production Distrobox still exists after removal.",
        )

    def configured_desktop(
        self,
        layout: install_desktop.Layout,
    ) -> Path | None:
        helper = shutil.which(
            "xdg-user-dir",
            path=self.env.get("PATH", os.defpath),
        )

        if helper is None:
            return None

        result = self.call(
            [helper, "DESKTOP"],
            timeout=10,
        )

        if result.returncode != 0:
            return None

        value = result.stdout.rstrip("\n")

        if not value.startswith("/"):
            return None

        try:
            path = install_desktop.absolute_path(
                value,
                "Configured Desktop",
            )
        except install_desktop.DesktopInstallError:
            return None

        if not path.is_dir():
            return None

        try:
            return path.resolve(strict=True)
        except OSError:
            return None

    def refresh_desktop_cache(self) -> None:
        desktops = (
            self.env.get("XDG_CURRENT_DESKTOP", "")
            .casefold()
            .split(":")
        )

        if "kde" not in desktops:
            return

        helper = shutil.which(
            "kbuildsycoca6",
            path=self.env.get("PATH", os.defpath),
        )

        if helper is None:
            return

        result = self.call([helper], timeout=30)

        if result.returncode != 0:
            print(
                "NOTE: Uninstall completed; KDE cache refresh reported: "
                + result.stderr.strip(),
                file=sys.stderr,
            )


def validate_container(info: dict | None) -> bool:
    if info is None:
        return False

    config = info.get("Config") or {}
    labels = config.get("Labels") or {}
    annotations = config.get("Annotations") or {}

    require(
        config.get("Image") == payload_tools.IMAGE
        and info.get("OCIRuntime") == "crun"
        and labels.get("manager") == "distrobox"
        and labels.get("io.zombiecc.installer") == payload_tools.OWNER
        and labels.get("distrobox.unshare_groups") == "0"
        and annotations.get("run.oci.keep_original_groups") == "1",
        "A container named 'zombiecc' exists but does not match the "
        "installer-owned production runtime. It will not be removed.",
    )

    return True


def editor_is_running(
    paths: Paths,
    *,
    proc_root: Path = Path("/proc"),
) -> bool:
    app = paths.payload.app.resolve()

    if not proc_root.exists():
        return False

    for process in proc_root.iterdir():
        if not process.name.isdecimal():
            continue

        try:
            if process.stat().st_uid != os.getuid():
                continue

            raw = (process / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            raise UninstallError(
                f"Cannot inspect process PID {process.name}: {error}."
            ) from error

        if not raw:
            continue

        argv = [
            os.fsdecode(argument)
            for argument in raw.rstrip(b"\0").split(b"\0")
        ]

        if (
            not argv
            or re.fullmatch(
                r"python(?:\d+(?:\.\d+)*)?[dt]?",
                Path(argv[0]).name,
            )
            is None
        ):
            continue

        script = payload_tools._python_script_argument(argv)

        if script is None:
            continue

        path = Path(script)

        if not path.is_absolute():
            try:
                path = Path(os.readlink(process / "cwd")) / path
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as error:
                raise UninstallError(
                    f"Cannot inspect working directory of PID {process.name}: {error}."
                ) from error

        try:
            resolved = path.resolve()
        except OSError:
            continue

        if resolved == app / "editor.py":
            return True

    return False

def _process_environment(process: Path) -> dict[str, str]:
    try:
        raw = (process / "environ").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return {}
    except OSError as error:
        raise UninstallError(
            f"Cannot inspect environment of Python PID {process.name}: {error}."
        ) from error

    environment: dict[str, str] = {}

    for entry in raw.rstrip(b"\0").split(b"\0"):
        if not entry:
            continue

        key, separator, value = entry.partition(b"=")

        if separator:
            environment[os.fsdecode(key)] = os.fsdecode(value)

    return environment


def check_uninstall_preflight_idle(
    paths: Paths,
    service_state: Mapping[str, str],
    *,
    proc_root: Path = Path("/proc"),
) -> None:
    app = paths.payload.app.resolve()
    service_active = service_state.get("ActiveState") in {
        "active",
        "activating",
        "deactivating",
    }

    main_pid = 0

    if service_active:
        try:
            main_pid = int(service_state.get("MainPID", "0"))
        except ValueError as error:
            raise UninstallError(
                "Production service returned an invalid MainPID."
            ) from error

        require(
            main_pid > 0,
            "Production service is active but has no valid MainPID.",
        )

    if not proc_root.exists():
        return

    for process in sorted(proc_root.iterdir(), key=lambda path: path.name):
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue

        try:
            if process.stat().st_uid != os.getuid():
                continue

            raw = (process / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            raise UninstallError(
                f"Cannot inspect your process PID {process.name}: {error}."
            ) from error

        if not raw:
            continue

        argv = [
            os.fsdecode(argument)
            for argument in raw.rstrip(b"\0").split(b"\0")
        ]

        if (
            not argv
            or re.fullmatch(
                r"python(?:\d+(?:\.\d+)*)?[dt]?",
                Path(argv[0]).name,
            )
            is None
        ):
            continue

        cwd: Path | None = None
        reason = payload_tools._installed_python_use(argv, None, app)

        if reason is None:
            try:
                cwd = Path(os.readlink(process / "cwd"))
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as error:
                raise UninstallError(
                    "Cannot inspect working directory of "
                    f"Python PID {process.name}: {error}."
                ) from error

            reason = payload_tools._installed_python_use(argv, cwd, app)

        if reason is None:
            continue

        script_argument = payload_tools._python_script_argument(argv)
        script_path: Path | None = None

        if script_argument is not None:
            script_path = Path(script_argument)

            if not script_path.is_absolute():
                if cwd is None:
                    try:
                        cwd = Path(os.readlink(process / "cwd"))
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except OSError as error:
                        raise UninstallError(
                            "Cannot inspect working directory of "
                            f"Python PID {process.name}: {error}."
                        ) from error

                script_path = cwd / script_path

            try:
                script_path = script_path.resolve()
            except OSError:
                pass

        pid = int(process.name)

        if (
            service_active
            and pid == main_pid
            and script_path == app / "driver_service.py"
        ):
            continue

        if (
            service_active
            and script_path == app / "g13-test.py"
        ):
            environment = _process_environment(process)

            if (
                environment.get("G13_DRIVER_SERVICE_CONTROL")
                == "stdin-pipe-v1"
                and environment.get("SYSTEMD_EXEC_PID")
                == str(main_pid)
            ):
                continue

        raise UninstallError(
            "Zombie is running from the installed application "
            f"(PID {process.name}: {reason}). Close the editor, "
            "driver session bridge, or other Zombie Python process "
            "before uninstalling. The production driver may remain "
            "running; the uninstaller will stop it after preflight."
        )

@dataclass(frozen=True)
class Plan:
    paths: Paths
    payload: payload_tools.Payload
    launcher: payload_tools.Blob
    receipt: payload_tools.Blob
    desktop_files: tuple[tuple[Path, payload_tools.Blob], ...]
    session_files: tuple[tuple[Path, payload_tools.Blob], ...]
    service_files: tuple[tuple[Path, payload_tools.Blob], ...]
    service_state: tuple[tuple[str, str], ...]
    container_present: bool
    purge_user_data: bool


def _desktop_candidates(
    paths: Paths,
    tools: HostTools,
) -> tuple[Path, ...]:
    candidates: list[Path] = [paths.desktop.menu]

    configured = tools.configured_desktop(paths.desktop)

    if configured is not None:
        candidates.append(configured / ENTRY_NAME)

    fallback = paths.payload.home / "Desktop" / ENTRY_NAME

    if fallback.parent.is_dir():
        candidates.append(fallback)

    result: list[Path] = []
    seen: set[str] = set()

    for path in candidates:
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            result.append(path)

    return tuple(result)


def build_plan(
    paths: Paths,
    tools: HostTools,
    *,
    purge_user_data: bool,
) -> Plan:
    payload, launcher, receipt = validate_installation(paths)

    expected_desktop = install_desktop.entry_contents(paths.desktop)
    desktop_files: list[tuple[Path, payload_tools.Blob]] = []

    for path in _desktop_candidates(paths, tools):
        value = expected_file(
            path,
            expected_desktop,
            description="Zombie desktop entry",
        )

        if value is not None:
            desktop_files.append((path, value))

    session_files = []
    value = expected_file(
        paths.service.session_entry,
        install_driver_service.session_entry_contents(paths.service),
        description="Zombie driver session entry")
    if value is not None:
        session_files.append((paths.service.session_entry, value))

    service_files: list[tuple[Path, payload_tools.Blob]] = []

    for path, expected in (
        (
            paths.service.unit,
            install_driver_service.unit_contents(
                paths.service,
                tools.distrobox,
            ),
        ),
        (
            paths.service.dropin,
            install_driver_service.dropin_contents(),
        ),
    ):
        value = expected_file(
            path,
            expected,
            description="Zombie production service file",
        )

        if value is not None:
            service_files.append((path, value))

    service_state = tools.service_state()
    fragment = service_state.get("FragmentPath", "")

    if service_state.get("LoadState") == "loaded":
        require(
            any(path == paths.service.unit for path, _ in service_files),
            "Production service is loaded but its managed unit file is missing.",
        )

        require(
            not fragment
            or Path(fragment).resolve()
            == paths.service.unit.resolve(),
            "A different service unit is loaded under Zombie's production name.",
        )
    else:
        require(
            service_state.get("LoadState") == "not-found",
            "Production service is masked or has a load error; review it before uninstalling.",
        )

    container_present = validate_container(
        tools.container_info()
    )

    if purge_user_data:
        for path in (
            paths.profiles,
            paths.config_root,
            paths.state_root,
        ):
            if os.path.lexists(path):
                _directory(path)

    return Plan(
        paths=paths,
        payload=payload,
        launcher=launcher,
        receipt=receipt,
        desktop_files=tuple(desktop_files),
        session_files=tuple(session_files),
        service_files=tuple(service_files),
        service_state=tuple(sorted(service_state.items())),
        container_present=container_present,
        purge_user_data=purge_user_data,
    )


def display_plan(plan: Plan) -> None:
    paths = plan.paths
    state = dict(plan.service_state)

    print("G13 Zombie Control Center -- uninstall plan")
    print(f"Application: {paths.payload.app}")
    print(f"Launcher:    {paths.payload.launcher}")
    print(
        f"Container:   {CONTAINER} "
        f"({'remove' if plan.container_present else 'already absent'})"
    )
    print(
        "Driver:      "
        f"{state.get('ActiveState', 'unknown')}/"
        f"{state.get('UnitFileState', 'unknown')} "
        "-> stop/disable/remove managed unit"
    )

    for path, _ in plan.session_files:
        print(f"remove autostart   {path}")

    for path, _ in plan.desktop_files:
        print(f"remove desktop     {path}")

    for path, _ in plan.service_files:
        print(f"remove service     {path}")

    print(f"remove launcher    {paths.payload.launcher}")
    print(f"remove application {paths.payload.app}")

    if plan.purge_user_data:
        print("\nPURGE requested -- permanently remove user data:")
        print(f"  profiles:       {paths.profiles}")
        print(f"  configuration:  {paths.config_root}")
        print(f"  logs/backups:   {paths.state_root}")
    else:
        print("\nPreserved by default:")
        print(f"  profiles:       {paths.profiles}")
        print(f"  settings:       {paths.config_root}")
        print(f"  logs/backups:   {paths.state_root}")

    print("\nAlways unchanged:")
    print("  development/source project files")
    print("  non-production Distrobox containers")
    print("  Steam Non-Steam shortcuts")
    print("  device permissions / udev configuration")
    print("  G13 onboard memory")


def unlink_expected(
    path: Path,
    expected: payload_tools.Blob,
) -> None:
    require(
        _read(path, missing=True) == expected,
        f"Managed file changed during uninstall; leaving it untouched: {path}",
    )
    path.unlink()


def remove_empty(path: Path) -> None:
    try:
        path.rmdir()
    except (FileNotFoundError, OSError):
        pass


def purge_directory(path: Path) -> None:
    if not os.path.lexists(path):
        return
    _directory(path)
    shutil.rmtree(path)


def execute(
    initial: Plan,
    tools: HostTools,
    *,
    proc_root: Path = Path("/proc"),
) -> None:
    paths = initial.paths
    tombstone: Path | None = None

    try:
        lock_context = payload_tools.install_lock(paths.payload)
    except payload_tools.UpdateError as error:
        raise UninstallError(str(error)) from error

    with lock_context:
        plan = build_plan(
            paths,
            tools,
            purge_user_data=initial.purge_user_data,
        )

        require(
            not editor_is_running(paths, proc_root=proc_root),
            "The installed editor is still running. Close it before uninstalling.",
        )

        check_uninstall_preflight_idle(
            paths,
            dict(plan.service_state),
            proc_root=proc_root,
        )

        # All refusal-style process checks have passed. Stop the production
        # service, then verify its managed Python processes are actually gone.
        tools.disable_and_stop_service()

        try:
            payload_tools.check_installed_app_idle(
                paths.payload,
                proc_root=proc_root,
            )
        except payload_tools.UpdateError as error:
            raise UninstallError(str(error)) from error

        if plan.container_present:
            tools.remove_container()
            print(f"Removed production Distrobox: {CONTAINER}")

        for path, expected in plan.session_files:
            unlink_expected(path, expected)
            print(f"Removed autostart: {path}")

        for path, expected in plan.desktop_files:
            unlink_expected(path, expected)
            print(f"Removed desktop entry: {path}")

        service_lookup = dict(plan.service_files)

        if paths.service.dropin in service_lookup:
            unlink_expected(
                paths.service.dropin,
                service_lookup[paths.service.dropin],
            )
            print(f"Removed service drop-in: {paths.service.dropin}")

        if paths.service.unit in service_lookup:
            unlink_expected(
                paths.service.unit,
                service_lookup[paths.service.unit],
            )
            print(f"Removed service unit: {paths.service.unit}")

        remove_empty(paths.service.dropin.parent)
        tools.daemon_reload()

        require(
            tools.service_state().get("LoadState") == "not-found",
            "Production service still appears loaded after managed unit removal.",
        )

        # Validate the app again using the launcher bytes captured before it is
        # unlinked. This prevents an unexpected payload change from being hidden
        # by removal of the launcher first.
        try:
            payload = payload_tools.Payload.load(paths.payload.app)
            receipt = payload_tools.marker(paths.payload)
        except (payload_tools.UpdateError, OSError, ValueError) as error:
            raise UninstallError(str(error)) from error

        require(
            payload.digest(plan.launcher.data).encode()
            == receipt.data.strip(),
            "Installed application changed during uninstall; stopping before payload removal.",
        )

        check_application_tree(paths, payload)

        try:
            payload_tools.check_installed_app_idle(
                paths.payload,
                proc_root=proc_root,
            )
        except payload_tools.UpdateError as error:
            raise UninstallError(str(error)) from error

        unlink_expected(
            paths.payload.launcher,
            plan.launcher,
        )
        print(f"Removed launcher: {paths.payload.launcher}")

        shutil.rmtree(paths.payload.app)
        print(f"Removed application and venv: {paths.payload.app}")

        if plan.purge_user_data:
            purge_directory(paths.config_root)
            print(f"Purged configuration: {paths.config_root}")

            purge_directory(paths.state_root)
            print(f"Purged state/logs/backups: {paths.state_root}")

            if os.path.lexists(paths.payload.base):
                _directory(paths.payload.base)
                tombstone = (
                    paths.payload.data
                    / f".zombiecc-uninstall-{os.getpid()}"
                )
                require(
                    not os.path.lexists(tombstone),
                    f"Temporary purge path already exists: {tombstone}",
                )
                os.rename(paths.payload.base, tombstone)

    if tombstone is not None:
        purge_directory(tombstone)
        print("Purged profiles and remaining Zombie data root.")

    tools.refresh_desktop_cache()

    if initial.purge_user_data:
        print(
            "Uninstall complete. Application, runtime, integration "
            "and user data removed."
        )
    else:
        print(
            "Uninstall complete. Profiles, settings and state were "
            "preserved for reinstall."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect/print the uninstall plan; change nothing.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation, not safety checks.",
    )
    parser.add_argument(
        "--purge-user-data",
        action="store_true",
        help=(
            "Also permanently delete profiles, settings, logs, diagnostics, "
            "migration snapshots and updater backups."
        ),
    )
    args = parser.parse_args(argv)

    old_umask = os.umask(0o077)

    try:
        env = dict(os.environ)

        try:
            payload_tools.host_guard(env)
        except payload_tools.UpdateError as error:
            raise UninstallError(str(error)) from error

        paths = Paths.from_environment(env)
        tools = HostTools(env)
        plan = build_plan(
            paths,
            tools,
            purge_user_data=args.purge_user_data,
        )
        display_plan(plan)

        if args.dry_run:
            print(
                "\nDRY RUN complete. No files, services or containers "
                "were changed."
            )
            return 0

        if not args.yes:
            require(
                sys.stdin.isatty(),
                "Interactive confirmation required; use --yes if unattended.",
            )

            if args.purge_user_data:
                reply = input(
                    "\nType PURGE to uninstall Zombie AND permanently delete "
                    "profiles/settings/state: "
                ).strip()

                if reply != "PURGE":
                    print("Cancelled.")
                    return 0

            else:
                reply = input(
                    "\nUninstall Zombie while preserving profiles/settings/state? "
                    "[y/N] "
                ).strip().lower()

                if reply != "y":
                    print("Cancelled.")
                    return 0

        execute(plan, tools)
        return 0

    except (
        UninstallError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"uninstall.py: {error}", file=sys.stderr)
        return 1

    except (KeyboardInterrupt, EOFError):
        print("\nCancelled/interrupted.", file=sys.stderr)
        return 130

    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
