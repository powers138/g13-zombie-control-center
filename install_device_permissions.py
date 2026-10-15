#!/usr/bin/env python3
"""Prepare host device permissions required by G13 Zombie Control Center.

Run on the Linux host as the normal user. Existing sufficient permissions are
left unchanged. Privileged changes are made only when required.

The installer can add the current user to the input group, ensure the uinput
kernel module is available across boots, and install narrow udev rules for
/dev/uinput and the Logitech G13 HID interfaces. It does not start the
application, driver, container, or any systemd service.
"""
from __future__ import annotations

import argparse
import grp
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile


UINPUT = Path("/dev/uinput")
UINPUT_SYSFS = Path("/sys/class/misc/uinput")
HIDRAW_SYSFS = Path("/sys/class/hidraw")
ETC_GROUP = Path("/etc/group")

UINPUT_RULE = Path("/etc/udev/rules.d/99-zombiecc-uinput.rules")
G13_RULE = Path("/etc/udev/rules.d/99-zombiecc-g13.rules")
UINPUT_MODULE_FILE = Path("/etc/modules-load.d/zombiecc-uinput.conf")

UINPUT_CONTENT = b'KERNEL=="uinput", GROUP="input", MODE="0660"\n'
UINPUT_MODULE_CONTENT = b"uinput\n"
G13_CONTENT = (
    b'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", '
    b'ATTRS{idVendor}=="046d", ATTRS{idProduct}=="c21c", '
    b'GROUP="input", MODE="0660"\n'
)
REQUIRED_HOST_COMMANDS = (
    "distrobox",
    "podman",
    "crun",
    "python3",
)

class PermissionInstallError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PermissionInstallError(message)

def check_host_prerequisites() -> None:
    missing = [
        command
        for command in REQUIRED_HOST_COMMANDS
        if shutil.which(command) is None
    ]

    require(
        not missing,
        "Required host commands not found: "
        + ", ".join(missing)
        + ". Install them before configuring device permissions.",
    )

def host_guard() -> None:
    require(sys.platform.startswith("linux"), "This installer is for Linux.")
    require(os.geteuid() != 0, "Run as your normal user, without sudo.")
    require(
        not os.environ.get("CONTAINER_ID")
        and not Path("/run/.containerenv").exists()
        and not Path("/.dockerenv").exists(),
        "Run on the host, not inside Distrobox.",
    )

    check_host_prerequisites()

def find_system_command(command: str) -> str | None:
    found = shutil.which(command)

    if found is not None:
        return found

    for directory in (
        "/usr/sbin",
        "/sbin",
        "/usr/bin",
        "/bin",
    ):
        candidate = Path(directory) / command

        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None

def input_group() -> grp.struct_group:
    try:
        return grp.getgrnam("input")
    except KeyError:
        raise PermissionInstallError(
            "The host does not provide an input group."
        ) from None


def current_username() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def input_group_active(group: grp.struct_group) -> bool:
    return group.gr_gid == os.getgid() or group.gr_gid in os.getgroups()


def group_would_grant(path: Path, group_gid: int, required: int) -> bool:
    information = path.stat()
    return (
        information.st_gid == group_gid
        and information.st_mode & required == required
    )


def find_g13_hidraw() -> list[tuple[Path, Path]]:
    devices = []

    if not HIDRAW_SYSFS.is_dir():
        return devices

    for entry in sorted(HIDRAW_SYSFS.glob("hidraw*")):
        current = (entry / "device").resolve()

        while current != current.parent:
            vendor = current / "idVendor"
            product = current / "idProduct"

            if vendor.is_file() and product.is_file():
                try:
                    vendor_id = vendor.read_text().strip().casefold()
                    product_id = product.read_text().strip().casefold()
                except OSError:
                    break

                if vendor_id == "046d" and product_id == "c21c":
                    devices.append(
                        (
                            Path("/dev") / entry.name,
                            entry,
                        )
                    )

                break

            current = current.parent

    return devices


def rule_status(path: Path, expected: bytes) -> str:
    if not path.exists():
        return "missing"

    require(path.is_file() and not path.is_symlink(),
            f"Existing rule is not a regular file: {path}")

    try:
        content = path.read_bytes()
    except OSError as error:
        raise PermissionInstallError(
            f"Cannot inspect existing rule {path}: {error}"
        ) from error

    return "identical" if content == expected else "different"


def run(command: list[str]) -> None:
    result = subprocess.run(command)

    if result.returncode:
        raise PermissionInstallError(
            f"Command failed with exit status {result.returncode}: "
            + " ".join(command)
        )


def sudo_command(*arguments: str) -> None:
    sudo = shutil.which("sudo")
    require(sudo is not None, "sudo is required for this permission change.")
    run([sudo, *arguments])


def install_rule(path: Path, content: bytes) -> None:
    status = rule_status(path, content)

    if status == "identical":
        print(f"Unchanged: {path}")
        return

    require(status == "missing",
            f"Different existing rule; leaving it unchanged: {path}")

    path.parent.mkdir(parents=False, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="zombiecc-udev-",
        suffix=".rules",
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())

        sudo_command(
            "cp",
            "--no-clobber",
            "--",
            str(temporary),
            str(path),
        )

        require(
            path.is_file()
            and not path.is_symlink()
            and path.read_bytes() == content,
            f"Rule destination changed during installation: {path}",
        )

        sudo_command(
            "chmod",
            "0644",
            "--",
            str(path),
        )

        print(f"Installed: {path}")

    finally:
        temporary.unlink(missing_ok=True)

def module_load_status(path: Path, expected: bytes) -> str:
    if not path.exists():
        return "missing"

    require(
        path.is_file() and not path.is_symlink(),
        f"Existing module-load file is not a regular file: {path}",
    )

    try:
        content = path.read_bytes()
    except OSError as error:
        raise PermissionInstallError(
            f"Cannot inspect existing module-load file {path}: {error}"
        ) from error

    return "identical" if content == expected else "different"


def install_module_load(path: Path, content: bytes) -> None:
    status = module_load_status(path, content)

    if status == "identical":
        print(f"Unchanged: {path}")
        return

    require(
        status == "missing",
        f"Different existing module-load file; leaving it unchanged: {path}",
    )

    require(
        path.parent.is_dir(),
        f"System module-load directory is missing: {path.parent}",
    )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="zombiecc-modules-load-",
        suffix=".conf",
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())

        sudo_command(
            "cp",
            "--no-clobber",
            "--",
            str(temporary),
            str(path),
        )

        require(
            path.is_file()
            and not path.is_symlink()
            and path.read_bytes() == content,
            f"Module-load destination changed during installation: {path}",
        )

        sudo_command(
            "chmod",
            "0644",
            "--",
            str(path),
        )

        print(f"Installed: {path}")

    finally:
        temporary.unlink(missing_ok=True)


def load_uinput_module() -> None:
    modprobe = find_system_command("modprobe")

    require(
        modprobe is not None,
        "modprobe is required to load the uinput kernel module.",
    )

    sudo_command(
        modprobe,
        "uinput",
    )

    require(
        UINPUT_SYSFS.exists(),
        f"{UINPUT_SYSFS} did not appear after loading the uinput kernel module.",
    )

    require(
        UINPUT.exists(),
        f"{UINPUT} did not appear after loading the uinput kernel module.",
    )

    print("Loaded: uinput kernel module")

def add_input_group(username: str) -> None:
    ujust = shutil.which("ujust")

    if ujust is not None:
        run([
            ujust,
            "add-user-to-input-group",
            "add",
        ])
        return

    group = input_group()
    local_groups = []

    for line in ETC_GROUP.read_text().splitlines():
        fields = line.split(":")

        if len(fields) < 4:
            continue

        try:
            gid = int(fields[2])
        except ValueError:
            continue

        local_groups.append(
            (fields[0], gid)
        )

    input_is_local = any(
        name == group.gr_name
        for name, _gid in local_groups
    )

    if not input_is_local:
        conflicting_group = next(
            (
                name
                for name, gid in local_groups
                if gid == group.gr_gid
            ),
            None,
        )

        if conflicting_group is not None:
            raise PermissionInstallError(
                "Cannot create a writable input-group "
                f"entry with GID {group.gr_gid}; "
                "/etc/group already uses that GID for "
                f"{conflicting_group}."
            )

        group_entry = (
            f"{group.gr_name}:"
            f"{group.gr_passwd}:"
            f"{group.gr_gid}:"
            f"{','.join(group.gr_mem)}"
        )

        sudo_command(
            "/bin/sh",
            "-c",
            'printf "%s\\n" "$1" >> "$2"',
            "zombiecc-input-group",
            group_entry,
            str(ETC_GROUP),
        )

    sudo_command(
        "usermod",
        "-aG",
        "input",
        username,
    )

    if username not in input_group().gr_mem:
        raise PermissionInstallError(
            f"Failed to add {username} to the input group."
        )


def reload_rules(
    trigger_uinput: bool,
    g13_devices: list[tuple[Path, Path]],
) -> None:
    udevadm = shutil.which("udevadm")
    require(udevadm is not None, "udevadm is required to activate new rules.")

    sudo_command(
        udevadm,
        "control",
        "--reload-rules",
    )

    if trigger_uinput and UINPUT_SYSFS.exists():
        sudo_command(
            udevadm,
            "trigger",
            "--action=change",
            str(UINPUT_SYSFS),
        )

    for _device, sysfs in g13_devices:
        sudo_command(
            udevadm,
            "trigger",
            "--action=change",
            str(sysfs),
        )

    sudo_command(
        udevadm,
        "settle",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect permissions and print the plan; change nothing.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation, not safety checks.",
    )
    args = parser.parse_args(argv)

    try:
        host_guard()

        group = input_group()
        username = current_username()
        group_active = input_group_active(group)

        uinput_exists = UINPUT.exists()
        uinput_module_loaded = UINPUT_SYSFS.exists()

        require(
            not uinput_module_loaded or uinput_exists,
            "The uinput kernel module is loaded but "
            f"{UINPUT} is missing. Check devtmpfs/udev before continuing.",
        )

        need_uinput_module = not uinput_module_loaded

        if need_uinput_module:
            require(
                find_system_command("modprobe") is not None,
                "modprobe is required to load the uinput kernel module.",
            )
            uinput_access = False
            uinput_group_ready = False
        else:
            uinput_access = os.access(UINPUT, os.W_OK)
            uinput_group_ready = group_would_grant(
                UINPUT,
                group.gr_gid,
                stat.S_IWGRP,
            )

        need_uinput_rule = (
            need_uinput_module
            or (
                not uinput_access
                and not uinput_group_ready
            )
        )

        g13_devices = find_g13_hidraw()
        inaccessible_g13 = []
        need_g13_rule = False

        for device, _sysfs in g13_devices:
            accessible = os.access(
                device,
                os.R_OK | os.W_OK,
            )

            group_ready = (
                device.exists()
                and group_would_grant(
                    device,
                    group.gr_gid,
                    stat.S_IRGRP | stat.S_IWGRP,
                )
            )

            if not accessible:
                inaccessible_g13.append(device)

                if not group_ready:
                    need_g13_rule = True

        need_group = not group_active

        print("G13 Zombie Control Center -- device permission plan")
        print(
            "input group:  "
            + (
                "active for current session"
                if group_active
                else "user must be added"
            )
        )
        print(
            f"uinput:       {UINPUT} "
            + (
                "kernel module not loaded"
                if need_uinput_module
                else (
                    "writable"
                    if uinput_access
                    else "not writable"
                )
            )
        )

        if g13_devices:
            for device, _sysfs in g13_devices:
                print(
                    f"G13 hidraw:   {device} "
                    + (
                        "read/write accessible"
                        if os.access(device, os.R_OK | os.W_OK)
                        else "not accessible"
                    )
                )
        else:
            print("G13 hidraw:   G13 not currently detected")

        if need_group:
            print(f"Action:       add {username} to input group")

        if need_uinput_module:
            status = module_load_status(
                UINPUT_MODULE_FILE,
                UINPUT_MODULE_CONTENT,
            )
            require(
                status != "different",
                "Different existing module-load file; "
                f"leaving it unchanged: {UINPUT_MODULE_FILE}",
            )
            print(
                f"Action:       "
                f"{'keep existing' if status == 'identical' else 'install'} "
                f"{UINPUT_MODULE_FILE}"
            )
            print("Action:       load uinput kernel module")

        if need_uinput_rule:
            status = rule_status(UINPUT_RULE, UINPUT_CONTENT)
            require(status != "different",
                    f"Different existing rule; leaving it unchanged: {UINPUT_RULE}")
            print(
                f"Action:       "
                f"{'keep existing' if status == 'identical' else 'install'} "
                f"{UINPUT_RULE}"
            )

        if need_g13_rule:
            status = rule_status(G13_RULE, G13_CONTENT)
            require(status != "different",
                    f"Different existing rule; leaving it unchanged: {G13_RULE}")
            print(
                f"Action:       "
                f"{'keep existing' if status == 'identical' else 'install'} "
                f"{G13_RULE}"
            )

        if (
            not need_group
            and not need_uinput_module
            and not need_uinput_rule
            and not need_g13_rule
        ):
            print("Permissions are already sufficient. No changes required.")
            return 0

        if args.dry_run:
            print(
                "DRY RUN complete. No group membership, kernel modules, "
                "module-load configuration, or udev rules changed."
            )
            return 0

        if not args.yes:
            require(
                sys.stdin.isatty(),
                "Interactive confirmation required; use --yes if unattended.",
            )
            reply = input(
                "Apply only these device-permission changes? [y/N] "
            ).strip().lower()

            if reply != "y":
                print("Cancelled.")
                return 0

        if need_group:
            add_input_group(username)
            print(f"Added: {username} -> input group")

        rules_changed = False

        if need_uinput_rule:
            install_rule(
                UINPUT_RULE,
                UINPUT_CONTENT,
            )
            rules_changed = True

        if need_g13_rule:
            install_rule(
                G13_RULE,
                G13_CONTENT,
            )
            rules_changed = True

        if need_uinput_module:
            install_module_load(
                UINPUT_MODULE_FILE,
                UINPUT_MODULE_CONTENT,
            )
            load_uinput_module()

        if rules_changed or need_uinput_module:
            reload_rules(
                need_uinput_rule,
                g13_devices if need_g13_rule else [],
            )

        if need_group:
            print(
                "A full reboot is recommended before continuing so the "
                "new input-group membership is active."
            )
            print(
                "A logout/login may work on some desktops, but some "
                "session managers can retain stale session state."
            )
        else:
            require(
                os.access(UINPUT, os.W_OK),
                f"{UINPUT} is still not writable after applying the rule.",
            )

            remaining = [
                str(device)
                for device, _sysfs in g13_devices
                if not os.access(device, os.R_OK | os.W_OK)
            ]
            require(
                not remaining,
                "G13 HID access is still unavailable: "
                + ", ".join(remaining),
            )

            print("Device permissions are active.")

        return 0

    except (
        PermissionInstallError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(
            f"install_device_permissions.py: {error}",
            file=sys.stderr,
        )
        return 1

    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
