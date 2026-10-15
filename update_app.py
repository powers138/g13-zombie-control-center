#!/usr/bin/env python3
"""Controlled, additive application-payload updates for Zombie's Stage 1 install.

Run on the host from a separate source/release directory. This first updater
requires the installed launcher and BOTH dependency manifests to stay identical.
It replaces/adds manifest files, not the venv, services, permissions or user data.
Keep the installed editor/driver closed until it finishes (including recovery).

--dry-run reads/inspects only. --rollback BACKUP restores a verified snapshot.
--recover restores an interrupted update's original payload. No Git required.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from typing import Iterator, Mapping

IMAGE = "quay.io/fedora/fedora:44"
OWNER = "stage1-v1"
CONTAINER = "zombiecc"
MARKER = ".zombiecc-stage1"
PENDING = ".update-pending.json"
FORMAT = "zombiecc-code-update-v1"
DEPENDENCIES = ("container-packages.txt", "requirements-runtime.txt")
REQUIRED = {
    "editor.py", "g13-test.py", "driver_service.py", "profile_manager.py",
    "app_paths.py", "user_data_bootstrap.py", "app-files.txt", *DEPENDENCIES,
    "resources/defaults/default-profile.json", "resources/defaults/settings.json",
}

RELEASE_REQUIRED = {
    "VERSION",
    "app_version.py",
}

VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)"
    r"\."
    r"(?:0|[1-9][0-9]*)"
    r"\."
    r"(?:0|[1-9][0-9]*)"
)

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise UpdateError(message)


def absolute(value: str, name: str) -> Path:
    require(value.startswith("/") and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            f"{name} must be an absolute path without control characters.")
    return Path(os.path.normpath(value))


def host_guard(env: Mapping[str, str]) -> None:
    require(sys.platform.startswith("linux"), "Run on the Linux host.")
    require(os.geteuid() != 0, "Run as your normal user, without sudo.")
    require(not (env.get("CONTAINER_ID") or Path("/run/.containerenv").exists()
                 or Path("/.dockerenv").exists()), "Run on the host, not inside Distrobox.")
    require(os.uname().machine == "x86_64", "Only the tested x86_64 runtime is supported.")


@dataclass(frozen=True)
class Layout:
    home: Path
    data: Path
    state: Path

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> Layout:
        home = absolute(env.get("HOME", ""), "HOME")
        def xdg(name: str, fallback: Path) -> Path:
            value = env.get(name, "")
            return absolute(value, name) if value.startswith("/") else fallback
        return cls(home, xdg("XDG_DATA_HOME", home / ".local/share"),
                   xdg("XDG_STATE_HOME", home / ".local/state"))

    @property
    def base(self) -> Path:
        return self.data / "zombiecc"

    @property
    def app(self) -> Path:
        return self.base / "app"

    @property
    def launcher(self) -> Path:
        return self.home / ".local/bin/zombiecc"

    @property
    def backups(self) -> Path:
        return self.state / "zombiecc/updates"

    @property
    def pending(self) -> Path:
        return self.base / PENDING


def directory(path: Path) -> None:
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid(),
            f"Expected an ordinary directory owned by you: {path}")


def descendant(root: Path, relative: str, *, create: bool = False) -> Path:
    """No symlink components below the chosen root; HOME aliases are allowed."""
    directory(root)
    current = root
    for part in Path(relative).parts[:-1]:
        current = current / part
        if create:
            try:
                current.mkdir(mode=0o700)
                sync_directory(current.parent)
            except FileExistsError:
                pass
        directory(current)
    return current / Path(relative).name


@dataclass(frozen=True)
class Blob:
    data: bytes
    mode: int


def read_blob(path: Path, *, missing: bool = False) -> Blob | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        if missing:
            return None
        raise
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid(),
                f"Expected an ordinary file owned by you: {path}")
        require(before.st_nlink == 1, f"Refusing a hard-linked file: {path}")
        data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
        require(len(data) <= MAX_FILE_BYTES, f"File is unexpectedly large: {path}")
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                f"File changed while being read: {path}")
        mode = stat.S_IMODE(before.st_mode)
        require(not mode & 0o7022, f"Unsafe permissions on file: {path}")
        return Blob(data, mode)


def manifest(content: bytes) -> tuple[str, ...]:
    names = []
    for line in content.decode("utf-8").split("\n"):
        name = line.removesuffix("\r")
        if not name or name.startswith("#"):
            continue
        require(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*", name)
                is not None, f"Unsafe manifest path: {name!r}")
        parts = Path(name).parts
        require(not any(p.startswith("test_") or p == "__pycache__" for p in parts)
                and parts[0] not in {"profiles", "config", "docs", "tools"}
                and name not in {"zombiecc", "install.sh", "g13-editor.sh", "update_app.py",
                                 "install_desktop.py"}
                and not name.endswith(".service"), f"Non-app file in manifest: {name}")
        require(name not in names, f"Duplicate manifest entry: {name}")
        names.append(name)
    require(REQUIRED.issubset(names), "Required application entries are missing from app-files.txt.")
    require(len(names) <= 512, "Application manifest is unexpectedly large.")
    require(not any(a != b and b.startswith(a + "/") for a in names for b in names),
            "A manifest file is also used as a directory.")
    return tuple(names)

def payload_version(
    files: dict[str, Blob],
) -> str | None:
    blob = files.get(
        "VERSION"
    )

    if blob is None:
        return None

    try:
        value = blob.data.decode(
            "utf-8"
        ).strip()

    except UnicodeDecodeError as error:
        raise UpdateError(
            "VERSION must contain UTF-8 text."
        ) from error

    require(
        VERSION_PATTERN.fullmatch(
            value
        )
        is not None,
        "VERSION must use "
        "MAJOR.MINOR.PATCH format.",
    )

    return value

@dataclass(frozen=True)
class Payload:
    order: tuple[str, ...]
    files: dict[str, Blob]

    @classmethod
    def load(cls, root: Path) -> Payload:
        directory(root)
        first = read_blob(root / "app-files.txt")
        assert first is not None
        order = manifest(first.data)
        files = {}
        size = 0
        for name in order:
            value = read_blob(descendant(root, name))
            assert value is not None
            size += len(value.data)
            require(size <= MAX_PAYLOAD_BYTES, "Application payload is unexpectedly large.")
            files[name] = value
        require(files["app-files.txt"] == first, "Manifest changed while reading the payload.")
        payload_version(
            files
        )
        return cls(order, files)

    def digest(self, launcher: bytes) -> str:
        # EXACT stage1 fingerprint() stream: sha256sum reads each file from stdin
        # and writes '<hex>  -\n'. Preserve manifest order, including its own entry.
        result = hashlib.sha256()
        result.update((IMAGE + "\0" + OWNER + "\0").encode())
        for name in self.order:
            result.update(name.encode() + b"\0")
            result.update((hashlib.sha256(self.files[name].data).hexdigest() + "  -\n").encode())
        result.update(b"launcher\0")
        result.update((hashlib.sha256(launcher).hexdigest() + "  -\n").encode())
        return result.hexdigest()


def marker(layout: Layout) -> Blob:
    value = read_blob(layout.app / MARKER)
    require(value is not None and re.fullmatch(rb"[a-f0-9]{64}\n?", value.data) is not None,
            "Invalid or missing Stage 1 installation marker.")
    return value


def basic_installation(layout: Layout) -> None:
    directory(layout.base)
    directory(layout.app)
    directory(layout.app / ".venv")
    read_blob(layout.app / ".venv/pyvenv.cfg")
    value = read_blob(layout.launcher)
    require(value is not None and bool(value.mode & 0o100), "Installed launcher is not executable.")


@dataclass(frozen=True)
class Plan:
    layout: Layout
    source: Path
    old: Payload
    new: Payload
    launcher: Blob
    next_launcher: Blob
    old_marker: Blob

    @property
    def changes(self) -> list[str]:
        # Existing permissions are retained; only contents drive an update.
        return [name for name in self.new.order
                if name not in self.old.files or self.old.files[name].data != self.new.files[name].data]

    @property
    def launcher_changed(self) -> bool:
        return self.launcher.data != self.next_launcher.data


def build_plan(source: Path, layout: Layout) -> Plan:
    basic_installation(layout)
    require(not os.path.lexists(layout.pending),
            "An unfinished update exists. Keep Zombie closed and run update_app.py --recover.")
    source = source.resolve(strict=True)
    target = layout.app.resolve(strict=True)
    require(not source.is_relative_to(target) and not target.is_relative_to(source),
            "Source and installed application must be separate, not nested.")
    old = Payload.load(layout.app)
    launch = read_blob(layout.launcher)
    receipt = marker(layout)
    assert launch is not None
    require(old.digest(launch.data).encode() == receipt.data.strip(),
            "Installed payload or launcher differs from the Stage 1 fingerprint. Nothing will be overwritten.")
    source_launch = read_blob(source / "zombiecc")
    assert source_launch is not None
    require(bool(source_launch.mode & 0o100),
            "New release launcher is not executable.")
    next_launch = Blob(source_launch.data, launch.mode)
    new = Payload.load(source)
    missing_release_files = (
        RELEASE_REQUIRED
        - set(new.files)
    )

    require(
        not missing_release_files,
        "New release payload is missing: "
        + ", ".join(
            sorted(
                missing_release_files
            )
        ),
    )

    require(
        payload_version(
            new.files
        )
        is not None,
        "New release payload has no VERSION.",
    )
    for name in DEPENDENCIES:
        require(old.files[name].data == new.files[name].data,
                f"{name} changed. This updater does not change dependencies or rebuild the venv.")
    removed = set(old.order) - set(new.order)
    require(not removed, "This first updater does not remove manifest files: " + ", ".join(sorted(removed)))
    for name in set(new.order) - set(old.order):
        path = descendant(layout.app, name) if (layout.app / name).parent.exists() else layout.app / name
        require(not os.path.lexists(path), f"New payload file would overwrite an untracked installed file: {name}")
        # Check any already-existing parent, even when later parents are absent.
        parent = (layout.app / name).parent
        while parent != layout.app:
            if os.path.lexists(parent):
                directory(parent)
            parent = parent.parent
    return Plan(layout, source, old, new, launch, next_launch, receipt)


def _python_script_argument(argv: list[str]) -> str | None:
    """Return Python's script operand, never -c code, a -m name or script args."""
    index = 1
    while index < len(argv):
        value = argv[index]
        if value == "--":
            return argv[index + 1] if index + 1 < len(argv) else None
        if value == "-":
            return None
        if not value.startswith("-"):
            return value
        if value == "--check-hash-based-pycs":
            index += 2
            continue
        if value.startswith("--"):
            index += 1
            continue
        # Short options may be grouped, e.g. -uB or -uc CODE. -W/-X consume
        # their attached value or the next argument; do not parse that value.
        for offset, option in enumerate(value[1:], 1):
            if option in "cm":
                return None
            if option in "WX":
                if offset == len(value) - 1:
                    index += 1
                break
        index += 1
    return None


def _installed_python_use(argv: list[str], cwd: Path | None, app: Path) -> str | None:
    """Match paths used by a Python invocation, not words in a ps listing."""
    if not argv or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?[dt]?", Path(argv[0]).name) is None:
        return None
    app = app.resolve()

    def inside(value: str, *, interpreter: bool = False) -> bool:
        path = Path(value)
        if not path.is_absolute():
            if cwd is None:
                return False
            path = cwd / path
        # Resolving the interpreter's final symlink would collapse BOTH venvs
        # to /usr/bin/python. Resolve its directory, but keep the filename.
        path = path.parent.resolve() / path.name if interpreter else path.resolve()
        return path.is_relative_to(app)

    if inside(argv[0], interpreter=True):
        return "Python interpreter in the installed application"
    script = _python_script_argument(argv)
    if script is not None and inside(script):
        return "Python script in the installed application"
    if cwd is not None and cwd.resolve().is_relative_to(app):
        # Also covers -m, -c and interactive Python that can import this tree.
        return "Python working directory in the installed application"
    return None


def check_installed_app_idle(layout: Layout, *, proc_root: Path = Path("/proc")) -> None:
    """Inspect this user's host processes, irrespective of PID namespace sharing.

    /proc cmdline preserves NUL-separated argv and cwd disambiguates relative
    paths. Do not use Podman's formatted hpid/args output (hpid may be '?').
    This is a point-in-time guard, not a lock against launching a new editor;
    keep the installed application closed throughout the update.
    """
    app = layout.app.resolve()
    for process in sorted(proc_root.iterdir(), key=lambda path: path.name):
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            raw = (process / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue  # A process exited during the scan.
        except OSError as error:
            raise UpdateError(
                f"Cannot inspect your process PID {process.name}: {error}. "
                "The running-application check was not bypassed."
            ) from error
        if not raw:
            continue  # Zombie/finished process.
        argv = [os.fsdecode(arg) for arg in raw.rstrip(b"\0").split(b"\0")]
        if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?[dt]?", Path(argv[0]).name) is None:
            continue  # Ignore grep, shells, Podman and Distrobox wrappers.
        reason = _installed_python_use(argv, None, app)
        if reason is None:
            try:
                cwd = Path(os.readlink(process / "cwd"))
                reason = _installed_python_use(argv, cwd, app)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as error:
                raise UpdateError(
                    f"Cannot inspect working directory of Python PID {process.name}: {error}. "
                    "Cannot safely determine whether it uses the installed application."
                ) from error
        require(
            reason is None,
            f"Zombie is running from the installed application "
            f"(PID {process.name}: {reason}). "
            "Close that editor, driver, or Python process first.",
        )


class Runtime:
    """Subprocess boundary: no shell interpolation and no inherited lock FDs."""
    def __init__(self, env: Mapping[str, str]):
        self.env = dict(env)
        self.env["DBX_CONTAINER_MANAGER"] = "podman"
        self.podman = shutil.which("podman", path=env.get("PATH", os.defpath))
        self.distrobox = shutil.which("distrobox", path=env.get("PATH", os.defpath))
        require(bool(self.podman and self.distrobox), "Podman and Distrobox are required on the host.")

    def run(self, args: list[str], *, timeout: int = 30) -> str:
        result = subprocess.run(args, env=self.env, cwd="/", stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout, close_fds=True)
        require(result.returncode == 0,
                f"Command failed (exit {result.returncode}): {' '.join(args[:2])}\n"
                + result.stdout[-6000:] + result.stderr[-6000:])
        return result.stdout

    def check_idle(self, layout: Layout) -> None:
        mode = self.run([self.podman, "info", "--format",
                         "{{.Host.Security.Rootless}} {{.Host.ServiceIsRemote}}"])
        require(mode.strip() == "true false", "A local rootless Podman engine is required.")
        records = json.loads(self.run([self.podman, "container", "inspect", CONTAINER]))
        require(isinstance(records, list) and len(records) == 1, "Unexpected Podman inspection result.")
        info = records[0]
        config = info.get("Config", {})
        labels = config.get("Labels") or {}
        annotations = config.get("Annotations") or {}
        require(labels.get("manager") == "distrobox"
                and labels.get("io.zombiecc.installer") == OWNER
                and config.get("Image") == IMAGE and info.get("OCIRuntime") == "crun"
                and labels.get("distrobox.unshare_groups") == "0"
                and annotations.get("run.oci.keep_original_groups") == "1",
                "Production container does not match the Stage 1 runtime. It will not be replaced.")
        state = info.get("State", {})
        require(state.get("Paused") is False and not state.get("Restarting") and not state.get("Dead"),
                "Production container is paused, restarting or unavailable.")
        require(isinstance(state.get("Running"), bool), "Cannot determine the production container state.")
        # Distrobox may share host PIDs, so podman top is not an ownership
        # boundary. A stopped container also does not rule out a host-side
        # supervisor using the installed driver_service.py.
        check_installed_app_idle(layout)

    def validate(self, layout: Layout, staged: Path) -> str:
        # -I ignores Python environment variables and user site; -B avoids cache
        # writes. This only compiles sources/loads factory JSON and runtime libs.
        # No application modules are imported, no settings or devices are opened.
        return self.run([
            self.distrobox, "enter", "--name", CONTAINER, "--no-tty", "--",
            str(layout.app / ".venv/bin/python"), "-I", "-B", "-c", RUNTIME_CHECK,
            str(layout.app), str(staged),
        ], timeout=180)


RUNTIME_CHECK = r'''
import importlib, importlib.metadata, json, site, sys
from pathlib import Path
app, source = (Path(p).resolve() for p in sys.argv[1:3])
if sys.version_info[:2] != (3, 14) or Path(sys.prefix).resolve() != app / '.venv':
    raise RuntimeError('Wrong production Python/venv.')
if site.ENABLE_USER_SITE is not False:
    raise RuntimeError('User Python packages are enabled.')
for name in ('PySide6', 'hid', 'lupa', 'evdev', 'gi'):
    mod = importlib.import_module(name)
    origin = Path(mod.__file__).resolve()
    if name in ('evdev', 'gi'):
        valid = origin.is_relative_to('/usr/lib') or origin.is_relative_to('/usr/lib64')
    else:
        valid = origin.is_relative_to(app / '.venv')
    if not valid:
        raise RuntimeError(f'Unexpected dependency origin: {name}: {origin}')
for line in (source / 'requirements-runtime.txt').read_text().splitlines():
    if line and not line.startswith('#'):
        name, expected = line.split('==', 1)
        if importlib.metadata.version(name) != expected:
            raise RuntimeError(f'Runtime version mismatch: {name}')
from PySide6 import QtCore, QtGui, QtWidgets
from gi.repository import Gio, GLib
from lupa.lua51 import LuaRuntime
if LuaRuntime().eval('_VERSION') != 'Lua 5.1':
    raise RuntimeError('Lua 5.1 is unavailable.')
for line in (source / 'app-files.txt').read_text().splitlines():
    if line.endswith('.py'):
        path = source / line
        compile(path.read_bytes(), str(path), 'exec')
for name in ('default-profile.json', 'settings.json'):
    if not isinstance(json.loads((source / 'resources/defaults' / name).read_text()), dict):
        raise RuntimeError(f'Invalid factory JSON: {name}')
print('OK: unchanged runtime, staged Python syntax and factory JSON.')
print('No application code, driver or user-data bootstrap was executed.')
'''


@contextmanager
def install_lock(layout: Layout) -> Iterator[None]:
    directory(layout.base)
    fd = os.open(layout.base / ".install.lock",
                 os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1,
                "Invalid installation lock.")
        os.set_inheritable(fd, False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError("Another Zombie installer/updater holds the installation lock. Do not delete it.") from error
        yield
    finally:
        os.close(fd)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_new(source: Path, target: Path) -> None:
    """Linux atomic no-overwrite publication without a hard-link crash window."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    require(rename is not None, "This host lacks renameat2; no unsafe publication fallback is used.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def publish(path: Path, blob: Blob, expected: Blob | None) -> None:
    directory(path.parent)
    require(read_blob(path, missing=True) == expected, f"Destination changed during update: {path}")
    fd, name = tempfile.mkstemp(prefix=".zombiecc-update-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob.data)
            stream.flush()
            os.fchmod(stream.fileno(), blob.mode)
            os.fsync(stream.fileno())
        require(read_blob(path, missing=True) == expected, f"Destination changed during update: {path}")
        if expected is None:
            rename_new(temporary, path)
        else:
            os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def put_snapshot(root: Path, payload: Payload) -> None:
    root.mkdir(mode=0o700)
    sync_directory(root.parent)
    for name in payload.order:
        path = descendant(root, name, create=True)
        publish(path, payload.files[name], None)


def backup_directory(layout: Layout) -> Path:
    # Permit the normal /home -> /var/home alias; reject symlinks below state.
    directory(layout.state)
    root = descendant(layout.state, "zombiecc/updates/placeholder", create=True).parent
    directory(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    result = Path(tempfile.mkdtemp(prefix=stamp, dir=root))
    sync_directory(root)
    return result


def write_backup(plan: Plan) -> Path:
    location = backup_directory(plan.layout)
    put_snapshot(location / "before", plan.old)
    # Preserve existing modes; new app files are private like Stage 1's payload.
    after = Payload(plan.new.order, {
        name: Blob(plan.new.files[name].data, plan.old.files[name].mode if name in plan.old.files else 0o600)
        for name in plan.new.order
    })
    put_snapshot(location / "after", after)
    publish(location / "launcher", plan.launcher, None)
    publish(location / "launcher-after", plan.next_launcher, None)
    publish(location / "original-marker", plan.old_marker, None)
    receipt = {
        "format": FORMAT, "application": str(plan.layout.app.resolve()),
        "before": plan.old.digest(plan.launcher.data),
        "after": after.digest(plan.next_launcher.data),
    }
    publish(location / "receipt.json", Blob((json.dumps(receipt, indent=2) + "\n").encode(), 0o600), None)
    return location


@dataclass(frozen=True)
class Backup:
    path: Path
    before: Payload
    after: Payload
    launcher: Blob
    next_launcher: Blob
    original_marker: Blob

    @classmethod
    def load(cls, layout: Layout, path: Path) -> Backup:
        require(path.is_absolute() and not path.is_symlink(), "Backup must be an absolute, ordinary directory.")
        directory(path.parent)
        directory(path)
        require(path.parent.resolve() == layout.backups.resolve(), "Backup is outside this installation's update directory.")
        receipt_blob = read_blob(path / "receipt.json")
        assert receipt_blob is not None
        receipt = json.loads(receipt_blob.data)
        require(isinstance(receipt, dict) and receipt.get("format") == FORMAT
                and receipt.get("application") == str(layout.app.resolve()), "Unrecognized backup or wrong application path.")
        before, after = Payload.load(path / "before"), Payload.load(path / "after")
        launch = read_blob(path / "launcher")
        next_launch = read_blob(path / "launcher-after", missing=True)
        original = read_blob(path / "original-marker")
        assert launch is not None and original is not None

        # Backups made before launcher updates were supported contain only
        # "launcher". They remain valid because both sides used that launcher.
        if next_launch is None:
            next_launch = launch

        require(before.digest(launch.data) == receipt.get("before") == original.data.strip().decode()
                and after.digest(next_launch.data) == receipt.get("after"), "Backup fingerprint verification failed.")
        require(set(before.order).issubset(after.order), "Unsupported backup: manifest removals.")
        for name in DEPENDENCIES:
            require(before.files[name].data == after.files[name].data, "Backup changes the runtime dependencies.")
        return cls(path, before, after, launch, next_launch, original)

    def next_marker(self) -> Blob:
        return Blob((self.after.digest(self.next_launcher.data) + "\n").encode(), self.original_marker.mode)


def pending_backup(layout: Layout) -> Path | None:
    value = read_blob(layout.pending, missing=True)
    if value is None:
        return None
    record = json.loads(value.data)
    require(isinstance(record, dict) and record.get("format") == FORMAT
            and isinstance(record.get("backup"), str), "Invalid update journal; inspect it before proceeding.")
    return absolute(record["backup"], "Update backup")


def set_pending(layout: Layout, path: Path) -> None:
    data = (json.dumps({"format": FORMAT, "backup": str(path.resolve())}) + "\n").encode()
    publish(layout.pending, Blob(data, 0o600), None)


def clear_pending(layout: Layout, path: Path) -> None:
    require(pending_backup(layout) == path.resolve(), "Update journal changed unexpectedly.")
    layout.pending.unlink()
    sync_directory(layout.base)


def check_recoverable(layout: Layout, backup: Backup) -> None:
    basic_installation(layout)
    current_launcher = read_blob(layout.launcher)
    assert current_launcher is not None
    require(current_launcher in (backup.launcher, backup.next_launcher),
            "Installed launcher differs from BOTH known versions; refusing rollback.")
    require(marker(layout) in (backup.original_marker, backup.next_marker()),
            "Installed marker belongs to a different update; refusing rollback.")
    for name in backup.after.order:
        # No removals during forward updates, so original files may not vanish.
        current = read_blob(descendant(layout.app, name), missing=True) if (layout.app / name).parent.exists() else None
        acceptable = (backup.before.files.get(name), backup.after.files[name])
        require(current in acceptable, f"File differs from BOTH known versions; leaving it alone: {name}")


def clear_bytecode(layout: Layout, names: list[str]) -> None:
    # Timestamp-based pyc validation uses whole seconds and source size. Removing
    # just the changed modules' disposable CPython caches avoids a same-second,
    # same-size edit being mistaken for old code. Never traverse the venv.
    paths = []
    for name in names:
        source = layout.app / name
        if source.suffix != ".py":
            continue
        cache = source.parent / "__pycache__"
        if not os.path.lexists(cache):
            continue
        directory(cache)
        pattern = re.compile(re.escape(source.stem) + r"\.cpython-[0-9]+(?:\.opt-[0-9]+)?\.pyc")
        for path in cache.iterdir():
            if pattern.fullmatch(path.name):
                read_blob(path)  # Refuse unexpected links/ownership before deleting ANY cache.
                paths.append(path)
    for path in paths:
        path.unlink()
        sync_directory(path.parent)


def restore(layout: Layout, backup: Backup) -> None:
    """Called under the shared install lock. Validates ALL files before restoring."""
    check_recoverable(layout, backup)
    for name in backup.after.order:
        path = descendant(layout.app, name, create=True)
        current = read_blob(path, missing=True)
        original = backup.before.files.get(name)
        if current == original:
            continue
        if original is None:
            require(current == backup.after.files[name], f"Added file changed during rollback: {name}")
            path.unlink()
            sync_directory(path.parent)
        else:
            publish(path, original, current)
    clear_bytecode(layout, [name for name in backup.after.order
                            if name not in backup.before.files
                            or backup.before.files[name].data != backup.after.files[name].data])

    current_launcher = read_blob(layout.launcher)
    assert current_launcher is not None
    if current_launcher != backup.launcher:
        publish(layout.launcher, backup.launcher, current_launcher)

    current_marker = marker(layout)
    if current_marker != backup.original_marker:
        publish(layout.app / MARKER, backup.original_marker, current_marker)

    restored_launcher = read_blob(layout.launcher)
    require(restored_launcher == backup.launcher
            and Payload.load(layout.app).digest(backup.launcher.data).encode()
            == backup.original_marker.data.strip(),
            "Rollback verification failed.")
    clear_pending(layout, backup.path)


def note(path: Path, text: str) -> None:
    print(text, flush=True)
    with (path / "update.log").open("a", encoding="utf-8") as stream:
        stream.write(text + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def apply(plan: Plan, runtime: Runtime) -> Path | None:
    with install_lock(plan.layout):
        fresh = build_plan(plan.source, plan.layout)
        require(fresh == plan, "Source or installation changed after preflight. Rerun the updater.")
        runtime.check_idle(plan.layout)
        if not plan.changes and not plan.launcher_changed:
            print("Installed payload and launcher are already identical. No files changed.")
            return None
        location = write_backup(plan)
        note(location, f"Verified application backup: {location}")
        backup = Backup.load(plan.layout, location)
        # Validate BEFORE any installed file changes. Container may be started
        # here by Distrobox; no editor, driver, pip or package-manager is run.
        try:
            note(location, runtime.validate(plan.layout, location / "after").strip())
            runtime.check_idle(plan.layout)
            require(build_plan(plan.source, plan.layout) == plan,
                    "Source or installation changed during staging. No payload was updated.")
        except Exception as error:
            note(location, f"Pre-update validation failed; installed payload unchanged: {error}")
            raise
        set_pending(plan.layout, location)
        try:
            for name in plan.changes:
                path = descendant(plan.layout.app, name, create=True)
                publish(path, backup.after.files[name], backup.before.files.get(name))
                note(location, f"Updated: {name}")

            if backup.next_launcher != backup.launcher:
                current_launcher = read_blob(plan.layout.launcher)
                assert current_launcher is not None
                publish(
                    plan.layout.launcher,
                    backup.next_launcher,
                    current_launcher,
                )
                note(location, "Updated: host launcher")

            installed_launcher = read_blob(plan.layout.launcher)
            require(installed_launcher == backup.next_launcher,
                    "Published launcher failed verification.")
            require(Payload.load(plan.layout.app).digest(backup.next_launcher.data)
                    == backup.after.digest(backup.next_launcher.data),
                    "Published payload failed fingerprint verification.")
            clear_bytecode(plan.layout, plan.changes)
            publish(plan.layout.app / MARKER, backup.next_marker(), backup.original_marker)
            # Commit point: all payload files AND the compatible Stage 1 marker
            # are verified. Pending journal survives SIGKILL/power interruption.
            clear_pending(plan.layout, location)
        except BaseException as error:
            # On ordinary failures or Ctrl+C/SIGTERM, restore without retrying
            # the failed update. An uncatchable exit is recovered via --recover.
            try:
                runtime.check_idle(plan.layout)
                restore(plan.layout, backup)
            except BaseException as rollback_error:
                raise UpdateError(f"Update interrupted and automatic rollback did not finish: {rollback_error}\n"
                                  f"Keep Zombie closed. Run update_app.py --recover. Backup: {location}") from error
            raise UpdateError(f"Update failed; previous application files restored: {error}\nBackup: {location}") from error
        note(location, "Application update completed. Venv, services and live user data were not changed.")
        return location


def rollback(layout: Layout, path: Path, runtime: Runtime, *, recovery: bool) -> None:
    with install_lock(layout):
        pending = pending_backup(layout)
        require(pending == path.resolve() if recovery else pending is None,
                "An update journal exists or changed; use --recover for its recorded backup.")
        backup = Backup.load(layout, path)
        check_recoverable(layout, backup)
        runtime.check_idle(layout)
        if not recovery:
            # A deliberate rollback applies only to a complete, known version.
            current = Payload.load(layout.app)
            current_launcher = read_blob(layout.launcher)
            assert current_launcher is not None
            current_digest = current.digest(
                current_launcher.data
            )
            require(current_digest in (
                backup.before.digest(backup.launcher.data),
                backup.after.digest(backup.next_launcher.data),
            ),
                "Installed payload is incomplete. Use --recover rather than a normal rollback.")
            set_pending(layout, path)
        restore(layout, backup)
        note(path, "Original application payload and launcher restored. Runtime and live user data unchanged.")


def display_plan(plan: Plan) -> None:
    print("G13 Zombie Control Center -- application update plan")
    old_version = (
        payload_version(
            plan.old.files
        )
        or "pre-version"
    )

    new_version = payload_version(
        plan.new.files
    )

    print(
        f"Version:     "
        f"{old_version} -> {new_version}"
    )
    print(f"Source:      {plan.source}\nApplication: {plan.layout.app}\nContainer:   {CONTAINER}")
    print(f"Payload:     {len(plan.old.order)} -> {len(plan.new.order)} files")
    for name in plan.changes:
        print(f"{'replace' if name in plan.old.files else 'add':10s} {name}")
    if plan.launcher_changed:
        print(f"{'replace':10s} host launcher")
    print(f"Backup root: {plan.layout.backups}")
    print("Verified: installed payload/launcher fingerprint; unchanged dependency manifests.")
    print("Unchanged: .venv, live profiles/settings, desktop/session entries, services and device permissions.")
    print("Keep the installed editor and production driver stopped until completion.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Inspect only; no update writes or container starts.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation, not safety checks.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--rollback", type=Path, metavar="BACKUP", help="Restore a verified updater backup.")
    mode.add_argument("--recover", action="store_true", help="Restore the original files from an unfinished update.")
    args = parser.parse_args(argv)
    mask = os.umask(0o077)
    previous_term = signal.getsignal(signal.SIGTERM)
    def interrupted(signum: int, frame: object) -> None:
        raise KeyboardInterrupt("SIGTERM")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        env = dict(os.environ)
        host_guard(env)
        layout = Layout.from_environment(env)
        runtime = Runtime(env)
        if args.rollback or args.recover:
            path = pending_backup(layout) if args.recover else args.rollback
            require(path is not None, "No unfinished update journal exists.")
            backup = Backup.load(layout, path)
            check_recoverable(layout, backup)
            runtime.check_idle(layout)
            print(f"Restore original application payload from:\n{path}")
            print("Venv, launcher, services, desktop/session entries and live user data remain unchanged.")
        else:
            plan = build_plan(Path(__file__).resolve().parent, layout)
            runtime.check_idle(layout)
            display_plan(plan)
        if args.dry_run:
            print("DRY RUN complete. No update files written and no container/application started.")
            return 0
        if not args.yes:
            require(sys.stdin.isatty(), "Interactive confirmation required; use --yes if unattended.")
            if input("Proceed with this application-payload operation only? [y/N] ").strip().lower() != "y":
                print("Cancelled.")
                return 0
        if args.rollback or args.recover:
            rollback(layout, path, runtime, recovery=args.recover)
        else:
            apply(plan, runtime)
        return 0
    except (UpdateError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"update_app.py: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Keep Zombie closed if an update journal remains; use --recover.", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        os.umask(mask)


if __name__ == "__main__":
    raise SystemExit(main())
