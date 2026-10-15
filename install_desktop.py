#!/usr/bin/env python3
"""Install Zombie's host menu entry and optional desktop shortcut.

Run beside install.sh, after Stage 1. Python standard library only.
No app payload/venv changes, container commands, services, or session integration writes.
Existing identical entries are left alone; different entries are never replaced.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Iterator, Mapping

APP_ID = "zombie-control-center"
ENTRY_NAME = APP_ID + ".desktop"


class DesktopInstallError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DesktopInstallError(message)


def absolute_path(value: str, name: str) -> Path:
    require(bool(value) and value.startswith("/"), f"{name} must be an absolute path.")
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value),
            f"{name} contains a control character.")
    return Path(os.path.normpath(value))


def xdg_home(env: Mapping[str, str], key: str, fallback: Path) -> Path:
    value = env.get(key, "")
    return absolute_path(value, key) if value.startswith("/") else fallback


def string_value(value: str) -> str:
    """Desktop Entry string escaping (not shell quoting)."""
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "A desktop-entry value contains a control character.")
    return value.replace("\\", "\\\\").replace(" ", "\\s")


def exec_argument(value: str) -> str:
    """Quote one Exec argument, then apply Desktop Entry string escaping."""
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "An Exec argument contains a control character.")
    quoted = "".join("\\" + c if c in '\\"`$' else c for c in value)
    # %% means a literal percent, never a file/URL field supplied by the desktop.
    return '"' + quoted.replace("\\", "\\\\").replace("%", "%%") + '"'


@dataclass(frozen=True)
class Layout:
    home: Path
    data: Path
    config: Path
    state: Path

    @property
    def app(self) -> Path:
        return self.data / "zombiecc" / "app"

    @property
    def launcher(self) -> Path:
        return self.home / ".local" / "bin" / "zombiecc"

    @property
    def icon(self) -> Path:
        # Reuse the installed G13 illustration; no unapproved logo is invented.
        return self.app / "assets" / "g13_device.png"

    @property
    def menu(self) -> Path:
        return self.data / "applications" / ENTRY_NAME

    def environment(self, env: Mapping[str, str]) -> dict[str, str]:
        result = dict(env)
        result.update(HOME=str(self.home), XDG_DATA_HOME=str(self.data),
                      XDG_CONFIG_HOME=str(self.config), XDG_STATE_HOME=str(self.state))
        return result


def make_layout(env: Mapping[str, str]) -> Layout:
    home = absolute_path(env.get("HOME", ""), "HOME")
    return Layout(home, xdg_home(env, "XDG_DATA_HOME", home / ".local/share"),
                  xdg_home(env, "XDG_CONFIG_HOME", home / ".config"),
                  xdg_home(env, "XDG_STATE_HOME", home / ".local/state"))


def host_guard(env: Mapping[str, str]) -> None:
    require(sys.platform.startswith("linux"), "This installer is for Linux.")
    require(not (env.get("CONTAINER_ID") or Path("/run/.containerenv").exists()
                 or Path("/.dockerenv").exists()),
            "Run on the host, not inside Distrobox. Use exit first.")
    require(os.geteuid() != 0, "Run as your normal user, without sudo.")


def regular_owned_file(path: Path) -> bytes:
    # Avoid following a replaced symlink or blocking on a special file.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(),
                f"Expected an ordinary file owned by you: {path}")
        return stream.read()


def check_directory(path: Path) -> None:
    """Check the destination and its nearest existing parent without creating it."""
    current = path
    while not current.exists() and not current.is_symlink():
        current = current.parent
    require(not current.is_symlink() and current.is_dir(),
            f"Not an ordinary destination directory: {current}")
    require(current.stat().st_uid == os.getuid() and os.access(current, os.W_OK | os.X_OK),
            f"Directory is not owned/writable by you: {current}")


def check_installation(layout: Layout) -> None:
    require(not layout.app.is_symlink() and layout.app.is_dir(),
            f"Stage 1 application is missing or symlinked: {layout.app}")
    marker = regular_owned_file(layout.app / ".zombiecc-stage1").strip()
    require(re.fullmatch(rb"[0-9a-f]{64}", marker) is not None,
            "Unrecognized Stage 1 installation marker.")
    for path in (layout.app / "editor.py", layout.app / ".venv" / "pyvenv.cfg"):
        regular_owned_file(path)
    regular_owned_file(layout.launcher)
    require(os.access(layout.launcher, os.X_OK), f"Launcher is not executable: {layout.launcher}")
    require(regular_owned_file(layout.icon).startswith(b"\x89PNG\r\n\x1a\n"),
            f"Installed G13 icon source is not a PNG: {layout.icon}")
    for command in ("/usr/bin/env", "/bin/bash"):
        require(os.access(command, os.X_OK), f"Required host command is missing: {command}")
    # Do not test the venv interpreter on the host: it belongs to the container.


def configured_desktop(layout: Layout, env: Mapping[str, str]) -> Path:
    helper = shutil.which("xdg-user-dir", path=env.get("PATH", os.defpath))
    require(helper is not None,
            "xdg-user-dir is unavailable. Omit --desktop-shortcut to install only the menu entry.")
    result = subprocess.run([helper, "DESKTOP"], env=layout.environment(env),
                            capture_output=True, text=True, timeout=10, check=True)
    path = absolute_path(result.stdout.rstrip("\n"), "Configured Desktop")
    require(path.is_dir(), f"Configured Desktop does not exist; it will not be created: {path}")
    path = path.resolve(strict=True)  # A user-configured Desktop folder may itself be a symlink.
    require(path != layout.home.resolve(),
            "Desktop directory is disabled (points to HOME). No shortcut will be installed.")
    check_directory(path)
    return path


def entry_contents(layout: Layout) -> bytes:
    # Pin the installation's XDG roots and discard development-only overrides.
    # env receives separate quoted arguments, not a shell command string.
    command = ["/usr/bin/env", "-u", "ZOMBIECC_CONTAINER", "-u", "ZOMBIECC_APP_DIR",
               "-u", "ZOMBIECC_PYTHON", f"XDG_DATA_HOME={layout.data}",
               f"XDG_CONFIG_HOME={layout.config}", f"XDG_STATE_HOME={layout.state}",
               "/bin/bash", str(layout.launcher)]
    lines = ["[Desktop Entry]", "Type=Application", "Name=G13 Zombie Control Center",
             "GenericName=G13 Gamepad Configuration",
             "Comment=Configure G13 keys, macros, profiles, and backlighting",
             "Exec=" + " ".join(exec_argument(arg) for arg in command),
             "TryExec=" + string_value(str(layout.launcher)),
             "Icon=" + string_value(str(layout.icon)),
             "Terminal=false", "DBusActivatable=false", "Categories=Settings;HardwareSettings;",
             "Keywords=Logitech;G13;Zombie;keypad;macros;backlight;",
             "X-ZombieCC-Managed=desktop-v1"]
    # No guessed StartupWMClass, file associations, or KDE-only menu restriction.
    return ("\n".join(lines) + "\n").encode("utf-8")


@dataclass(frozen=True)
class Entry:
    path: Path
    mode: int


def target_action(entry: Entry, content: bytes) -> str:
    check_directory(entry.path.parent)
    require(not entry.path.is_symlink(), f"Refusing a symlinked desktop entry: {entry.path}")
    if not entry.path.exists():
        return "create"
    require(regular_owned_file(entry.path) == content,
            f"A different desktop entry already exists; leaving it alone: {entry.path}")
    return "keep identical"


@contextmanager
def install_lock(layout: Layout) -> Iterator[None]:
    # Share Stage 1's lock; do not unlink a lock file that another process may use.
    path = layout.data / "zombiecc" / ".install.lock"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "Invalid installation lock.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DesktopInstallError("Another Zombie installer is already running.") from error
        yield
    finally:
        os.close(descriptor)


def publish_entry(entry: Entry, content: bytes, env: Mapping[str, str]) -> None:
    if target_action(entry, content) == "keep identical":
        print(f"Unchanged: {entry.path}")
        if entry.mode == 0o755 and not os.access(entry.path, os.X_OK):
            print("NOTE: Existing desktop shortcut is not executable; its permissions were preserved.")
        return
    entry.path.parent.mkdir(parents=True, exist_ok=True)
    check_directory(entry.path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".zombiecc-desktop-", suffix=".desktop",
                                           dir=entry.path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), entry.mode)
            os.fsync(stream.fileno())
        validator = shutil.which("desktop-file-validate", path=env.get("PATH", os.defpath))
        if validator:
            result = subprocess.run([validator, temporary], env=dict(env), capture_output=True,
                                    text=True, timeout=15)
            require(result.returncode == 0,
                    "Desktop entry validation failed: " + result.stdout + result.stderr)
        # Atomic no-overwrite publication. Never replace a concurrently created entry.
        try:
            os.link(temporary, entry.path)
        except FileExistsError:
            target_action(entry, content)  # Accept only an identical concurrent result.
        print(f"Installed: {entry.path}")
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_entries(layout: Layout, entries: list[Entry], env: Mapping[str, str]) -> None:
    with install_lock(layout):
        check_installation(layout)
        content = entry_contents(layout)
        # Check ALL requested destinations before publishing the first entry.
        for entry in entries:
            target_action(entry, content)
        for entry in entries:
            publish_entry(entry, content, layout.environment(env))
    if "kde" in env.get("XDG_CURRENT_DESKTOP", "").casefold().split(":"):
        helper = shutil.which("kbuildsycoca6", path=env.get("PATH", os.defpath))
        if helper:
            try:
                result = subprocess.run([helper], env=layout.environment(env), capture_output=True,
                                        text=True, timeout=30)
                if result.returncode:
                    print("NOTE: Entries installed; KDE cache refresh reported: " + result.stderr.strip())
            except (OSError, subprocess.TimeoutExpired) as error:
                print(f"NOTE: Entries installed; KDE cache refresh did not finish: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop-shortcut", action="store_true",
                        help="Also install an executable copy in the configured Desktop folder.")
    parser.add_argument("--dry-run", action="store_true", help="Check and print the plan; write nothing.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation, not safety checks.")
    args = parser.parse_args(argv)
    previous_umask = os.umask(0o077)
    try:
        env = dict(os.environ)
        host_guard(env)
        layout = make_layout(env)
        check_installation(layout)
        entries = [Entry(layout.menu, 0o644)]
        if args.desktop_shortcut:
            desktop_entry = Entry(configured_desktop(layout, env) / ENTRY_NAME, 0o755)
            require(desktop_entry.path.resolve() != layout.menu.resolve(),
                    "Desktop and application-menu entry destinations must be different.")
            entries.append(desktop_entry)
        content = entry_contents(layout)
        print("G13 Zombie Control Center -- desktop integration plan")
        print(f"Launcher: {layout.launcher}\nIcon:     {layout.icon}")
        for entry in entries:
            print(f"{target_action(entry, content):14s} {entry.path}")
        print("Unchanged: app payload, venv, profiles/settings, session integration, services, and device/group permissions.")
        print("\nGenerated entry:\n" + content.decode("utf-8"))
        if args.dry_run:
            print("DRY RUN complete. No files written and no application/container started.")
            return 0
        if not args.yes:
            require(sys.stdin.isatty(), "Interactive confirmation required; use --yes if unattended.")
            if input("Install only these desktop entries? [y/N] ").strip().lower() != "y":
                print("Cancelled.")
                return 0
        install_entries(layout, entries, env)
        print("Desktop entries installed. No application/driver started.")
        return 0
    except (DesktopInstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"install_desktop.py: {error}", file=sys.stderr)
        print("Existing differing files were not replaced. Any completed entry is retained for retry.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
