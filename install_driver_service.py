#!/usr/bin/env python3
"""Prepare (never start/enable) Zombie's production user driver service.

Run on the host beside update_app.py, after deploying the production-supervisor
patch with the controlled updater. Uses its existing fingerprint/lock helpers.
No application import, container entry, package installation, permission changes,
or old-service edits. Unknown or modified existing managed files are never overwritten.

--dry-run only reads files, inspects Podman and queries the user systemd manager.
The actual run stages/verifies the unit, writes it plus one private drop-in,
reloads the user manager, then checks the effective settings. If verification or
reload fails, no service is started; already published identical files are kept
for a retry. This installer is not the switchover/enable step.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping

import update_app as payload_tools

UNIT = "zombie-control-center-driver.service"
DROPIN = "90-zombiecc-shutdown.conf"
SESSION_ENTRY = "zombie-control-center-driver-session.desktop"
SESSION_MANAGED = "driver-session-v1"
HOST_PYTHON = "/usr/bin/python3"
PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "UnitFileState", "FragmentPath",
    "DropInPaths", "Type", "KillMode", "KillSignal", "TimeoutStopUSec",
    "TimeoutStopFailureMode", "Restart", "PartOf", "After", "ExecStart",
    "Environment", "NeedDaemonReload", "StandardInput",
)


class ServiceInstallError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ServiceInstallError(message)


def unit_string(value: str) -> str:
    """Quote a systemd string, protecting literal % specifiers (NOT shlex)."""
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "A unit value contains a control character.")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


@dataclass(frozen=True)
class Layout:
    payload: payload_tools.Layout
    config: Path

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> Layout:
        base = payload_tools.Layout.from_environment(env)
        value = env.get("XDG_CONFIG_HOME", "")
        config = payload_tools.absolute(value, "XDG_CONFIG_HOME") if value.startswith("/") else base.home / ".config"
        return cls(base, config)

    @property
    def unit_dir(self) -> Path:
        return self.config / "systemd/user"

    @property
    def unit(self) -> Path:
        return self.unit_dir / UNIT

    @property
    def dropin(self) -> Path:
        return self.unit_dir / (UNIT + ".d") / DROPIN

    @property
    def autostart_dir(self) -> Path:
        return self.config / "autostart"

    @property
    def session_entry(self) -> Path:
        return self.autostart_dir / SESSION_ENTRY


def working_directory_value(path: Path) -> str:
    # WorkingDirectory= is a single scalar path, not an argv list. Quotes would
    # become part of the path; backslashes/quotes are literal here. Expand only
    # the escaped %% specifier. Reject controls instead of encoding new lines.
    text = str(path)
    require(path.is_absolute() and not any(ord(c) < 32 or ord(c) == 127 for c in text),
            "WorkingDirectory must be an absolute path without control characters.")
    return text.replace("%", "%%")

def desktop_exec_argument(value: str) -> str:
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "An autostart Exec argument contains a control character.")
    quoted = "".join("\\" + c if c in '\\"`$' else c for c in value)
    return '"' + quoted.replace("\\", "\\\\").replace("%", "%%") + '"'

def unit_contents(layout: Layout, distrobox: str) -> bytes:

    # ':' disables ExecStart environment substitution. Quotes plus %% protect
    # paths containing spaces, dollar signs, quotes, backslashes and percents.
    start = ":" + HOST_PYTHON + " -s -u " + unit_string(str(layout.payload.app / "driver_service.py"))
    start += " --container zombiecc --isolated-runtime"
    start += " --distrobox " + unit_string(distrobox)
    lines = [
        "# Managed by ZombieCC install_driver_service.py; service-v1.",
        "[Unit]",
        "Description=G13 Zombie Control Center production driver",
        "PartOf=graphical-session.target",
        "After=graphical-session-pre.target",
        "", "[Service]", "Type=exec",
        "WorkingDirectory=" + working_directory_value(layout.payload.app),
        "ExecStart=" + start,
        'Environment="XDG_RUNTIME_DIR=%t"',
        'Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus"',
    ]
    for key, value in (
        ("XDG_DATA_HOME", layout.payload.data),
        ("XDG_CONFIG_HOME", layout.config),
        ("XDG_STATE_HOME", layout.payload.state),
        ("PYTHONNOUSERSITE", "1"),
        ("DBX_CONTAINER_MANAGER", "podman"),
    ):
        lines.append("Environment=" + unit_string(f"{key}={value}"))
    lines += [
        "UnsetEnvironment=PYTHONHOME PYTHONPATH ZOMBIECC_CONTAINER ZOMBIECC_APP_DIR ZOMBIECC_PYTHON",
        "StandardInput=null", "StandardOutput=journal", "StandardError=journal",
        "SyslogIdentifier=zombiecc-driver", "KillMode=mixed", "KillSignal=SIGTERM",
        "TimeoutStopSec=90", "TimeoutStopFailureMode=kill", "Restart=no",
        "", "[Install]",
        "WantedBy=graphical-session.target",
        "",
    ]
    # No Conflicts=old service: merely starting the new unit must not silently
    # kill the old driver. The unchanged common driver lock rejects duplicates.
    return "\n".join(lines).encode("utf-8")

def legacy_unit_variants(layout: Layout, distrobox: str) -> tuple[bytes, ...]:
    current = unit_contents(layout, distrobox)

    default_target = current.replace(
        b"WantedBy=graphical-session.target\n",
        b"WantedBy=graphical-session.target default.target\n", 1)

    distrobox_argument = (
        " --distrobox " + unit_string(distrobox)).encode("utf-8")
    no_distrobox = current.replace(distrobox_argument, b"", 1)

    require(default_target != current and no_distrobox != current,
            "Cannot construct previous managed service definitions.")
    return default_target, no_distrobox

def session_entry_contents(layout: Layout) -> bytes:
    bridge = layout.payload.app / "driver_session_bridge.py"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=G13 Zombie Driver Session Bridge",
        "Comment=Manage the G13 Zombie driver for this desktop session",
        "Exec=" + " ".join((
            desktop_exec_argument(HOST_PYTHON),
            "-s",
            "-u",
            desktop_exec_argument(str(bridge)),
        )),
        "TryExec=" + HOST_PYTHON,
        "Terminal=false",
        "DBusActivatable=false",
        "OnlyShowIn=XFCE;X-Cinnamon;",
        "X-GNOME-Autostart-enabled=true",
        f"X-ZombieCC-Managed={SESSION_MANAGED}",
        "",
    ]
    return "\n".join(lines).encode("utf-8")

def legacy_session_entry_variants(layout: Layout) -> tuple[bytes, ...]:
    current = session_entry_contents(layout)
    xfce_only = current.replace(
        b"OnlyShowIn=XFCE;X-Cinnamon;\n",
        b"OnlyShowIn=XFCE;\n",
        1,
    )
    require(xfce_only != current, "Cannot construct previous managed session entry.")
    return (xfce_only,)

def dropin_contents() -> bytes:
    return (
        "# Managed by ZombieCC install_driver_service.py; service-v1.\n"
        "# Scope the timeout policy to THIS service; do not change service.d/.\n"
        "[Service]\nTimeoutStopFailureMode=kill\n"
    ).encode("utf-8")


def check_parent_tree(path: Path) -> None:
    """Allow ancestors such as /home aliases; require existing descendants owned.

    The existing chosen XDG root is resolved by the caller; every destination
    component beneath it is checked separately before publication.
    """
    current = path
    while not os.path.lexists(current):
        require(current.parent != current, "No usable destination ancestor.")
        current = current.parent
    payload_tools.directory(current)
    require(os.access(current, os.W_OK | os.X_OK), f"Directory is not writable: {current}")


def check_destinations(layout: Layout, distrobox: str) -> dict[Path, bytes]:
    check_parent_tree(layout.config)
    for path in (layout.config, layout.config / "systemd", layout.unit_dir,
                 layout.dropin.parent, layout.autostart_dir):
        if os.path.lexists(path):
            payload_tools.directory(path)

    if layout.dropin.parent.is_dir():
        extras = [p.name for p in layout.dropin.parent.iterdir()
                  if p.name.endswith(".conf") and p.name != DROPIN]
        require(not extras, "Existing service-specific overrides need review: "
                + ", ".join(sorted(extras)))

    entries = {
        layout.unit: unit_contents(layout, distrobox),
        layout.dropin: dropin_contents(),
        layout.session_entry: session_entry_contents(layout),
    }
    legacy_units = set(legacy_unit_variants(layout, distrobox))
    legacy_session_entries = set(legacy_session_entry_variants(layout))

    for path, content in entries.items():
        current = payload_tools.read_blob(path, missing=True)
        allowed = current is None or current.data == content

        if path == layout.unit and current is not None:
            allowed = allowed or current.data in legacy_units

        if path == layout.session_entry and current is not None:
            allowed = allowed or current.data in legacy_session_entries

        require(allowed, f"Different existing managed file; leaving it unchanged: {path}")

    return entries


def check_supervisor(data: bytes) -> None:
    """Inspect the installed source without importing or running it."""
    tree = ast.parse(data, filename="installed driver_service.py")
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    run = functions.get("run_service")
    main = functions.get("main")
    supported = run is not None and "isolated_runtime" in {a.arg for a in run.args.kwonlyargs}
    supported = supported and "_isolated_runtime_command" in functions and main is not None
    supported = supported and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument" and node.args
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--isolated-runtime"
        for node in ast.walk(main)  # type: ignore[arg-type]
    )
    require(bool(supported), "Installed driver_service.py needs the production-supervisor update first. "
            "Deploy it with update_app.py; do not overwrite the installed file manually.")


def check_host_commands() -> None:
    require(os.path.isfile(HOST_PYTHON) and os.access(HOST_PYTHON, os.X_OK),
            f"Required host executable missing: {HOST_PYTHON}")


def check_installation(layout: Layout) -> str:
    base = layout.payload
    payload_tools.basic_installation(base)
    require(not os.path.lexists(base.pending), "An application update is unfinished; recover that update first.")
    old = payload_tools.Payload.load(base.app)
    launch = payload_tools.read_blob(base.launcher)
    receipt = payload_tools.marker(base)
    assert launch is not None
    digest = old.digest(launch.data)
    require(digest.encode() == receipt.data.strip(),
            "Installed application/launcher fingerprint differs. No service files will be changed.")
    require("driver_lifecycle.py" in old.files, "Installed lifecycle module is missing from the manifest.")
    check_supervisor(old.files["driver_service.py"].data)
    # Never dereference/execute the container venv Python on the host.
    check_host_commands()
    return digest


def properties(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key] = value
    return result


class Tools:
    """All external commands are read-only except verify and daemon-reload.

    verify reads a temporary unit tree; daemon-reload reloads configuration but
    does not start/stop/restart services. No Distrobox entry is performed here.
    """
    def __init__(self, env: Mapping[str, str]):
        self.env = dict(env)
        for name in ("SYSTEMD_UNIT_PATH", "SYSTEMD_GENERATOR_PATH", "SYSTEMD_ENVIRONMENT_GENERATOR_PATH"):
            self.env.pop(name, None)
        self.env["SYSTEMD_PAGER"] = "cat"
        self.env["SYSTEMD_COLORS"] = "0"
        self.podman = self.find("podman")
        self.distrobox = self.find("distrobox")
        self.systemctl = self.find("systemctl")
        self.analyze = self.find("systemd-analyze")

    def find(self, name: str) -> str:
        found = shutil.which(name, path=self.env.get("PATH", os.defpath))
        require(found is not None, f"Required host command missing: {name}")
        return str(found)

    def call(self, command: list[str], *, extra_env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, env=self.env | dict(extra_env or {}), cwd="/",
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=45, close_fds=True)

    def run(self, command: list[str], *, extra_env: Mapping[str, str] | None = None) -> str:
        result = self.call(command, extra_env=extra_env)
        require(result.returncode == 0, f"Command failed ({result.returncode}): {' '.join(command[:3])}\n"
                + result.stdout[-6000:] + result.stderr[-6000:])
        return result.stdout

    def check_container(self) -> None:
        mode = self.run([self.podman, "info", "--format", "{{.Host.Security.Rootless}} {{.Host.ServiceIsRemote}}"])
        require(mode.strip() == "true false", "A local rootless Podman engine is required.")
        records = json.loads(self.run([self.podman, "container", "inspect", "zombiecc"]))
        require(isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict),
                "Unexpected production-container metadata.")
        info = records[0]
        config = info.get("Config") or {}
        labels = config.get("Labels") or {}
        annotations = config.get("Annotations") or {}
        require(config.get("Image") == payload_tools.IMAGE and info.get("OCIRuntime") == "crun"
                and labels.get("manager") == "distrobox"
                and labels.get("io.zombiecc.installer") == payload_tools.OWNER
                and labels.get("distrobox.unshare_groups") == "0"
                and annotations.get("run.oci.keep_original_groups") == "1",
                "The production container does not match Stage 1. It will not be replaced.")
        state = info.get("State") or {}
        require(isinstance(state.get("Running"), bool) and state.get("Paused") is False
                and not state.get("Dead") and not state.get("Restarting"), "Production container is unavailable.")

    def show(self) -> dict[str, str]:
        result = self.call([self.systemctl, "--user", "show", UNIT, "--no-pager",
                            "--property=" + ",".join(PROPERTIES)])
        data = properties(result.stdout)
        # systemctl versions differ in status when a requested unit is absent.
        require(result.returncode == 0 or (result.returncode == 4 and data.get("LoadState") == "not-found"),
                "Cannot query the user service manager.\n" + result.stderr[-4000:])
        require("LoadState" in data and "ActiveState" in data, "Incomplete user-service state.")
        return data

    def check_unstarted(self, layout: Layout) -> dict[str, str]:
        data = self.show()
        require(data["ActiveState"] == "inactive", "Production service is not inactive; no files were changed.")
        require(data["LoadState"] in {"not-found", "loaded"}, "Production service is masked or has a load error.")
        require(data.get("UnitFileState", "") in {"", "disabled"},
                "Production service is already enabled/linked or otherwise managed; review it before installing.")
        fragment = data.get("FragmentPath", "")
        require(not fragment or Path(fragment).resolve() == layout.unit.resolve(),
                "A different unit with the production name is already loaded.")
        return data

    def verify(self, stage: Path) -> None:
        # Trailing ':' appends default user-unit paths for target dependencies.
        self.run([self.analyze, "--user", "--man=no", "verify", str(stage / UNIT)],
                 extra_env={"SYSTEMD_UNIT_PATH": str(stage) + ":"})

    def reload(self) -> None:
        self.run([self.systemctl, "--user", "daemon-reload"])

    def check_loaded(self, layout: Layout) -> None:
        data = self.check_unstarted(layout)
        expected = {"LoadState": "loaded", "UnitFileState": "disabled", "Type": "exec",
                    "KillMode": "mixed", "KillSignal": "15", "TimeoutStopFailureMode": "kill",
                    "Restart": "no", "StandardInput": "null", "NeedDaemonReload": "no"}
        mismatches = [f"{name}={data.get(name)!r} (expected {value!r})"
                      for name, value in expected.items() if data.get(name) != value]
        require(data.get("TimeoutStopUSec") in {"1min 30s", "90s", "90000000"},
                "Effective stop timeout is not 90 seconds. Review service overrides before starting it.")
        require("graphical-session.target" in data.get("PartOf", "").split()
                and "graphical-session-pre.target" in data.get("After", "").split(),
                "Effective session dependencies differ. Review overrides before starting the service.")
        require(not mismatches, "Effective unit settings differ; do not start it:\n" + "\n".join(mismatches))
        require("--isolated-runtime" in data.get("ExecStart", "") and "--container zombiecc" in data.get("ExecStart", ""),
                "Effective ExecStart does not use the isolated production runtime.")
        print("Verified effective service: inactive, disabled, Type=exec, KillMode=mixed, stop=90s/kill, Restart=no.")


@dataclass(frozen=True)
class Plan:
    layout: Layout
    fingerprint: str
    entries: dict[Path, bytes]


def plan_installation(layout: Layout, tools: Tools) -> Plan:
    fingerprint = check_installation(layout)
    entries = check_destinations(layout, tools.distrobox)
    tools.check_container()
    tools.check_unstarted(layout)
    return Plan(layout, fingerprint, entries)


def display_plan(plan: Plan) -> None:
    print("G13 Zombie Control Center -- production driver service preparation")
    print(f"Application: {plan.layout.payload.app}")
    print("Container:   zombiecc (no container will be started by this installer)")
    for path, content in plan.entries.items():
        current = payload_tools.read_blob(path, missing=True)
        action = ("create" if current is None else
                  "keep identical" if current.data == content else
                  "update managed")
        print(f"{action:14s} {path}")
    print("Verified: installed fingerprint and production-only supervisor option.")
    print("Actual install: verify service, publish managed service/session files, daemon-reload, verify loaded settings.")
    print("Unchanged: development driver service/container, enablement, app/venv, profiles/settings, desktop entries, permissions.")
    print("\nGenerated service:\n" + plan.entries[plan.layout.unit].decode())
    print("Generated service-specific override:\n" + plan.entries[plan.layout.dropin].decode())
    print("Generated driver session bridge entry:\n" + plan.entries[plan.layout.session_entry].decode())


def install(plan: Plan, tools: Tools) -> None:
    layout = plan.layout
    with payload_tools.install_lock(layout.payload):
        current = plan_installation(layout, tools)
        require(current == plan, "Installation changed since the preflight. Run the installer again.")
        # No global persistent writes until native verification passes.
        with tempfile.TemporaryDirectory(prefix="zombiecc-unit-check-") as temp:
            stage = Path(temp)
            (stage / UNIT).write_bytes(plan.entries[layout.unit])
            (stage / (UNIT + ".d")).mkdir()
            (stage / (UNIT + ".d") / DROPIN).write_bytes(plan.entries[layout.dropin])
            tools.verify(stage)
            print("OK: staged unit passed systemd-analyze --user verify.")
            require(plan_installation(layout, tools) == plan, "Installation changed during unit validation.")
            # Publish the safety drop-in first so even a partial attempt cannot
            # leave a new main unit lacking its intended timeout policy.
            for path in (layout.dropin, layout.unit, layout.session_entry):
                existing = payload_tools.read_blob(path, missing=True)

                if existing is not None and existing.data == plan.entries[path]:
                    print(f"Unchanged: {path}")
                    continue

                check_parent_tree(layout.config)
                layout.config.mkdir(parents=True, exist_ok=True)
                payload_tools.descendant(
                    layout.config, str(path.relative_to(layout.config)), create=True)
                check_destinations(layout, tools.distrobox)
                payload_tools.publish(
                    path, payload_tools.Blob(plan.entries[path], 0o644), existing)

                action = "Updated" if existing is not None else "Installed"
                print(f"{action}: {path}")
        tools.reload()
        tools.check_loaded(layout)
    print("Production service and driver session bridge prepared, not started and not enabled. Development service unchanged.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Read-only preflight; no files or service changes.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation, not safety checks.")
    args = parser.parse_args(argv)
    old_umask = os.umask(0o077)
    try:
        env = dict(os.environ)
        payload_tools.host_guard(env)
        layout = Layout.from_environment(env)
        tools = Tools(env)
        plan = plan_installation(layout, tools)
        display_plan(plan)
        if args.dry_run:
            print("DRY RUN complete. No files written, no manager reload and no service/container started.")
            return 0
        if not args.yes:
            require(sys.stdin.isatty(), "Interactive confirmation required; use --yes if unattended.")
            if input("Prepare only this disabled production service? [y/N] ").strip().lower() != "y":
                print("Cancelled.")
                return 0
        install(plan, tools)
        return 0
    except (ServiceInstallError, payload_tools.UpdateError, OSError, ValueError, SyntaxError,
            subprocess.SubprocessError) as error:
        print(f"install_driver_service.py: {error}", file=sys.stderr)
        print("No service was started or enabled. Existing differing files were not replaced. "
              "Any completed identical files are retained for retry; do not start the new unit until validation passes.",
              file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. No service was started/enabled; any completed files remain for retry.", file=sys.stderr)
        return 130
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
