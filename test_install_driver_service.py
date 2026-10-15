"""Hardware-free production supervisor/service preparation tests.

Requires install_driver_service.py, update_app.py and the patched
driver_service.py beside the existing driver_lifecycle.py. Tests use temporary
files, mocked service/container commands and harmless stub drivers. They never
import the real g13-test.py or start/stop an actual service/container.
One optional test uses systemd-analyze --user verify on a temporary unit only.
"""
from __future__ import annotations

import contextlib
from dataclasses import replace
import fcntl
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import driver_service as supervisor
import install_driver_service as service
import update_app as payload

PROJECT = Path(__file__).resolve().parent


def fake_container():
    return [{"Config": {"Image": payload.IMAGE,
                        "Labels": {"manager": "distrobox", "io.zombiecc.installer": payload.OWNER,
                                   "distrobox.unshare_groups": "0"},
                        "Annotations": {"run.oci.keep_original_groups": "1"}},
             "OCIRuntime": "crun", "State": {"Running": True, "Paused": False}}]


def loaded_properties(layout):
    return {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
            "UnitFileState": "disabled", "FragmentPath": str(layout.unit),
            "Type": "exec", "KillMode": "mixed", "KillSignal": "15", "TimeoutStopUSec": "1min 30s",
            "TimeoutStopFailureMode": "kill", "Restart": "no", "StandardInput": "null",
            "NeedDaemonReload": "no", "PartOf": "graphical-session.target",
            "After": "graphical-session-pre.target basic.target",
            "ExecStart": f"{{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -s -u {layout.payload.app}/driver_service.py --container zombiecc --isolated-runtime ; }}"}


class FakeTools(service.Tools):
    def __init__(self, layout):
        self.layout = layout
        self.events = []
        self.fail_verify = False
        self.fail_reload = False
        self.state_override = {}
        self.native_on_verify = None
        self.loaded = False
        self.distrobox = "/test/bin/distrobox"

    def check_container(self):
        self.events.append("inspect")

    def show(self):
        if self.loaded or self.layout.unit.exists():
            data = loaded_properties(self.layout)
        else:
            data = {"LoadState": "not-found", "ActiveState": "inactive", "UnitFileState": "", "FragmentPath": ""}
        return data | self.state_override

    def verify(self, stage):
        self.events.append("verify")
        assert (stage / service.UNIT).read_bytes() == service.unit_contents(self.layout, self.distrobox)
        assert (stage / (service.UNIT + ".d") / service.DROPIN).read_bytes() == service.dropin_contents()
        if self.native_on_verify:
            self.native_on_verify(stage)
        if self.fail_verify:
            raise service.ServiceInstallError("mock verify failed")

    def reload(self):
        self.events.append("daemon-reload")
        if self.fail_reload:
            raise service.ServiceInstallError("mock reload failed")
        self.loaded = True


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zombiecc-service-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        home = self.root / "Home $quoted 'with spaces'"
        home.mkdir()
        self.layout = service.Layout(payload.Layout(home, home / ".local/share", home / ".local/state"), home / ".config")
        self.base = self.layout.payload
        self.base.app.mkdir(parents=True)
        self.base.launcher.parent.mkdir(parents=True)
        self.base.launcher.write_bytes(b"#!/bin/bash\nexit 0\n")
        self.base.launcher.chmod(0o755)
        names = sorted(payload.REQUIRED | {"driver_lifecycle.py"})
        for name in names:
            p = self.base.app / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if name == "app-files.txt":
                data = "\n".join(names).encode() + b"\n"
            elif name == "driver_service.py":
                data = (PROJECT / name).read_bytes()
            elif name.endswith(".json"):
                data = b'{}\n'
            else:
                data = b"# fixture\n"
            p.write_bytes(data)
            p.chmod(0o644)
        (self.base.app / ".venv").mkdir()
        (self.base.app / ".venv/pyvenv.cfg").write_text("include-system-site-packages = true\n")
        # The host must NOT need a working container Python symlink.
        (self.base.app / ".venv/bin").mkdir()
        (self.base.app / ".venv/bin/python").symlink_to("/container-only/python")
        self.refresh_marker()
        self.mock_commands = patch.object(service, "check_host_commands")
        self.mock_commands.start()
        self.addCleanup(self.mock_commands.stop)
        self.tools = FakeTools(self.layout)

    def refresh_marker(self):
        fp = payload.Payload.load(self.base.app).digest(self.base.launcher.read_bytes())
        (self.base.app / payload.MARKER).write_text(fp + "\n")
        (self.base.app / payload.MARKER).chmod(0o600)

    def run_install(self):
        plan = service.plan_installation(self.layout, self.tools)
        with contextlib.redirect_stdout(io.StringIO()):
            service.install(plan, self.tools)
        return plan

    def test_preflight_does_not_create_config_or_lock(self):
        plan = service.plan_installation(self.layout, self.tools)
        self.assertEqual(set(plan.entries), {
        self.layout.unit, self.layout.dropin, self.layout.session_entry})
        self.assertFalse(self.layout.config.exists())
        self.assertFalse((self.base.base / ".install.lock").exists())
        self.assertEqual(self.tools.events, ["inspect"])

    def test_installs_exact_unit_and_dropin_without_enable_link(self):
        self.run_install()
        self.assertEqual(self.layout.unit.read_bytes(), service.unit_contents(self.layout, self.tools.distrobox))
        self.assertEqual(self.layout.dropin.read_bytes(), service.dropin_contents())
        self.assertEqual(self.layout.unit.stat().st_mode & 0o777, 0o644)
        self.assertFalse((self.layout.unit_dir / "graphical-session.target.wants").exists())
        self.assertIn("daemon-reload", self.tools.events)

    def test_repeated_install_keeps_same_inode(self):
        self.run_install()
        before = self.layout.unit.stat().st_ino, self.layout.dropin.stat().st_ino
        self.run_install()
        self.assertEqual(before, (self.layout.unit.stat().st_ino, self.layout.dropin.stat().st_ino))

    def test_verify_failure_does_not_publish(self):
        self.tools.fail_verify = True
        with self.assertRaisesRegex(service.ServiceInstallError, "verify failed"):
            self.run_install()
        self.assertFalse(self.layout.unit.exists())
        self.assertFalse(self.layout.dropin.exists())
        self.assertNotIn("daemon-reload", self.tools.events)

    def test_reload_failure_keeps_unenabled_files_for_retry(self):
        self.tools.fail_reload = True
        with self.assertRaisesRegex(service.ServiceInstallError, "reload failed"):
            self.run_install()
        self.assertTrue(self.layout.unit.is_file())
        self.assertFalse((self.layout.unit_dir / "graphical-session.target.wants").exists())
        self.tools.fail_reload = False
        self.run_install()

    def test_partial_publication_can_retry(self):
        real = payload.publish
        def broken(path, blob, expected):
            if path == self.layout.unit:
                raise OSError("mock publish failed")
            return real(path, blob, expected)
        with patch.object(payload, "publish", broken), self.assertRaisesRegex(OSError, "publish failed"):
            self.run_install()
        self.assertTrue(self.layout.dropin.exists())
        self.assertFalse(self.layout.unit.exists())
        self.run_install()

    def test_unknown_main_unit_is_preserved(self):
        self.layout.unit_dir.mkdir(parents=True)
        self.layout.unit.write_text("do not overwrite\n")
        with self.assertRaisesRegex(service.ServiceInstallError, "Different existing"):
            self.run_install()
        self.assertEqual(self.layout.unit.read_text(), "do not overwrite\n")
        self.assertFalse(self.layout.dropin.exists())

    def test_unknown_dropin_is_preserved_before_main_written(self):
        self.layout.dropin.parent.mkdir(parents=True)
        self.layout.dropin.write_text("do not overwrite\n")
        with self.assertRaisesRegex(service.ServiceInstallError, "Different existing"):
            self.run_install()
        self.assertFalse(self.layout.unit.exists())

    def test_other_service_specific_override_is_refused(self):
        self.layout.dropin.parent.mkdir(parents=True)
        (self.layout.dropin.parent / "99-user.conf").write_text("[Service]\nRestart=always\n")
        with self.assertRaisesRegex(service.ServiceInstallError, "overrides need review"):
            self.run_install()

    def test_symlinked_unit_is_refused(self):
        self.layout.unit_dir.mkdir(parents=True)
        target = self.root / "protected"
        target.write_text("untouched")
        self.layout.unit.symlink_to(target)
        with self.assertRaises(OSError):
            self.run_install()
        self.assertEqual(target.read_text(), "untouched")

    def test_symlinked_dropin_directory_is_refused(self):
        self.layout.unit_dir.mkdir(parents=True)
        target = self.root / "protected"
        target.mkdir()
        self.layout.dropin.parent.symlink_to(target)
        with self.assertRaises(payload.UpdateError):
            self.run_install()
        self.assertEqual(list(target.iterdir()), [])

    def test_symlinked_systemd_directory_is_refused(self):
        self.layout.config.mkdir()
        (self.layout.config / "systemd").symlink_to(self.root)
        with self.assertRaises(payload.UpdateError):
            self.run_install()

    def test_shared_installer_lock_refuses_second_install(self):
        with payload.install_lock(self.base):
            with self.assertRaisesRegex(payload.UpdateError, "holds the installation lock"):
                self.run_install()
        self.assertFalse(self.layout.unit.exists())

    def test_lock_descriptor_is_not_inherited(self):
        def verify(stage):
            script = ('import fcntl,os,sys\n'
                      'fd=os.open(sys.argv[1],os.O_WRONLY)\n'
                      'try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
                      'except BlockingIOError: pass\n'
                      'else: raise RuntimeError("parent did not retain lock")\n'
                      'for n in os.listdir("/proc/self/fd"):\n'
                      ' try: p=os.readlink("/proc/self/fd/"+n)\n'
                      ' except OSError: continue\n'
                      ' if p==sys.argv[1] and int(n)!=fd: raise RuntimeError("inherited lock")\n')
            subprocess.run([sys.executable, '-c', script, str(self.base.base / '.install.lock')], check=True, close_fds=False)
        self.tools.native_on_verify = verify
        self.run_install()

    def test_installed_payload_modification_is_refused(self):
        (self.base.app / "editor.py").write_text("modified")
        with self.assertRaisesRegex(service.ServiceInstallError, "fingerprint differs"):
            self.run_install()

    def test_pending_update_is_refused(self):
        self.base.pending.write_text("{}")
        with self.assertRaisesRegex(service.ServiceInstallError, "unfinished"):
            self.run_install()

    def test_missing_supervisor_capability_refuses_old_install(self):
        (self.base.app / "driver_service.py").write_text("def run_service(): pass\n")
        self.refresh_marker()
        with self.assertRaisesRegex(service.ServiceInstallError, "production-supervisor update"):
            self.run_install()

    def test_missing_venv_is_refused(self):
        shutil.rmtree(self.base.app / ".venv")
        with self.assertRaises(OSError):
            self.run_install()

    def test_broken_host_python_symlink_is_not_checked(self):
        self.run_install()

    def test_production_service_active_is_refused(self):
        self.tools.state_override = {"ActiveState": "active"}
        with self.assertRaisesRegex(service.ServiceInstallError, "not inactive"):
            self.run_install()

    def test_production_service_already_enabled_is_refused(self):
        self.tools.state_override = {"UnitFileState": "enabled"}
        with self.assertRaisesRegex(service.ServiceInstallError, "already enabled"):
            self.run_install()

    def test_masked_service_is_not_unmasked(self):
        self.tools.state_override = {"LoadState": "masked"}
        with self.assertRaisesRegex(service.ServiceInstallError, "masked"):
            self.run_install()

    def test_other_loaded_fragment_is_refused(self):
        self.tools.state_override = {"FragmentPath": "/etc/systemd/user/unrelated.service"}
        with self.assertRaisesRegex(service.ServiceInstallError, "different unit"):
            self.run_install()

    def test_effective_abort_override_is_detected(self):
        self.tools.state_override = {"TimeoutStopFailureMode": "abort"}
        with self.assertRaisesRegex(service.ServiceInstallError, "Effective unit settings differ"):
            self.run_install()

    def test_effective_kill_mode_override_is_detected(self):
        self.tools.state_override = {"KillMode": "control-group"}
        with self.assertRaisesRegex(service.ServiceInstallError, "KillMode"):
            self.run_install()

    def test_effective_restart_override_is_detected(self):
        self.tools.state_override = {"Restart": "always"}
        with self.assertRaisesRegex(service.ServiceInstallError, "Restart"):
            self.run_install()

    def test_effective_stop_timeout_override_is_detected(self):
        self.tools.state_override = {"TimeoutStopUSec": "10s"}
        with self.assertRaisesRegex(service.ServiceInstallError, "not 90 seconds"):
            self.run_install()

    def test_changed_application_during_verify_is_refused(self):
        def mutate(_):
            (self.base.app / "editor.py").write_text("# changed\n")
        self.tools.native_on_verify = mutate
        with self.assertRaisesRegex(service.ServiceInstallError, "fingerprint differs"):
            self.run_install()
        self.assertFalse(self.layout.unit.exists())

    def test_development_service_and_live_data_are_preserved(self):
        dev = self.layout.unit_dir / "g13-driver.service"
        dev.parent.mkdir(parents=True)
        dev.write_text("development service\n")
        profile = self.base.base / "profiles/Mine.json"
        settings = self.layout.config / "zombiecc/settings.json"
        for p in (profile, settings):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("keep mine\n")
        before = {p: p.read_bytes() for p in (dev, profile, settings, self.base.launcher, self.base.app / payload.MARKER)}
        self.run_install()
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_unit_does_not_stop_development_or_pin_a_desktop(self):
        text = service.unit_contents(self.layout, self.tools.distrobox).decode()
        self.assertNotIn("Conflicts=", text)
        self.assertNotIn("g13-fedora", text)
        self.assertNotIn("ExecStop=", text)
        self.assertNotIn("XDG_CURRENT_DESKTOP=", text)
        self.assertNotIn("DISPLAY=", text)
        self.assertIn("--container zombiecc --isolated-runtime", text)
        self.assertIn("Environment=\"XDG_RUNTIME_DIR=%t\"", text)
        self.assertIn("WantedBy=graphical-session.target", text)
        self.assertNotIn("default.target", text)

    def test_session_bridge_entry(self):
        text = service.session_entry_contents(self.layout).decode()
        self.assertIn("OnlyShowIn=XFCE;X-Cinnamon;", text)
        self.assertIn("driver_session_bridge.py", text)
        self.assertIn(f"X-ZombieCC-Managed={service.SESSION_MANAGED}", text)
        self.assertNotIn("OnlyShowIn=KDE;", text)

    def test_previous_xfce_session_entry_is_migrated(self):
        self.layout.autostart_dir.mkdir(parents=True)

        legacy = service.legacy_session_entry_variants(self.layout)[0]
        self.layout.session_entry.write_bytes(legacy)

        self.run_install()

        self.assertEqual(
            self.layout.session_entry.read_bytes(),
            service.session_entry_contents(self.layout),
        )
        self.assertIn(
            b"OnlyShowIn=XFCE;X-Cinnamon;",
            self.layout.session_entry.read_bytes(),
        )

    def test_unknown_session_entry_is_preserved(self):
        self.layout.autostart_dir.mkdir(parents=True)
        self.layout.session_entry.write_text("do not overwrite\n")

        with self.assertRaisesRegex(
            service.ServiceInstallError,
            "Different existing managed file",
        ):
            self.run_install()

        self.assertEqual(
            self.layout.session_entry.read_text(),
            "do not overwrite\n",
        )
        self.assertFalse(self.layout.unit.exists())

    def test_previous_managed_units_are_accepted_for_migration(self):
        self.layout.unit.parent.mkdir(parents=True, exist_ok=True)
        for content in service.legacy_unit_variants(
                self.layout, self.tools.distrobox):
            self.layout.unit.write_bytes(content)
            entries = service.check_destinations(
                self.layout, self.tools.distrobox)
            self.assertIn(self.layout.unit, entries)

    def test_custom_xdg_and_unit_escaping(self):
        text = service.unit_contents(self.layout, self.tools.distrobox).decode()
        self.assertIn("ExecStart=:/usr/bin/python3", text)
        self.assertIn('Home $quoted', text)
        self.assertEqual(service.unit_string('a% $q "\\z'), '"a%% $q \\"\\\\z"')
        with self.assertRaises(service.ServiceInstallError):
            service.unit_string("path\nExecStart=/bad")

    def test_relative_xdg_uses_defaults(self):
        layout = service.Layout.from_environment({"HOME": str(self.base.home), "XDG_CONFIG_HOME": "relative"})
        self.assertEqual(layout.config, self.base.home / ".config")

    def test_dry_run_main_has_no_writes_or_reload(self):
        env = {"HOME": str(self.base.home)}
        with patch.dict(os.environ, env, clear=True), patch.object(payload, "host_guard"), \
             patch.object(service, "Tools", return_value=self.tools), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(service.main(["--dry-run"]), 0)
        self.assertFalse(self.layout.unit.exists())
        self.assertNotIn("daemon-reload", self.tools.events)

    def test_noninteractive_run_requires_confirmation(self):
        with patch.dict(os.environ, {"HOME": str(self.base.home)}, clear=True), \
             patch.object(payload, "host_guard"), patch.object(service, "Tools", return_value=self.tools), \
             patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(service.main([]), 1)
        self.assertFalse(self.layout.unit.exists())

    def test_native_systemd_user_verifier_accepts_unit(self):
        analyze = shutil.which("systemd-analyze")
        if analyze is None:
            self.skipTest("systemd-analyze unavailable")
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        stage = self.root / "units"
        stage.mkdir()
        # % in SYSTEMD_UNIT_PATH is literal; ':' is the path-list delimiter.
        strange = replace(self.layout, payload=replace(self.base, data=self.base.data / 'literal %d "quote" \\ $VAR'))
        strange.payload.app.mkdir(parents=True)
        (stage / service.UNIT).write_bytes(service.unit_contents(strange, self.tools.distrobox))
        (stage / (service.UNIT + ".d")).mkdir()
        (stage / (service.UNIT + ".d") / service.DROPIN).write_bytes(service.dropin_contents())
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), SYSTEMD_UNIT_PATH=str(stage) + ":", SYSTEMD_LOG_LEVEL="debug")
        result = subprocess.run([analyze, '--user', '--man=no', 'verify', str(stage / service.UNIT)],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Unknown key", result.stderr)
        self.assertNotIn("Failed to parse", result.stderr)
        # Native parser prints the effective string containing literal percent,
        # dollars, quotes and backslash: no substitution into a different path.
        self.assertIn(str(strange.payload.app), result.stdout + result.stderr)


class BoundaryTests(unittest.TestCase):
    def tools(self):
        with patch('shutil.which', side_effect=lambda name, **_: '/usr/bin/' + name):
            return service.Tools({'PATH': '/usr/bin', 'SYSTEMD_UNIT_PATH': '/untrusted'})

    def test_subprocess_boundary_closes_fds_and_does_not_use_shell(self):
        tools = self.tools()
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            tools.run(['read-only'])
        self.assertTrue(run.call_args.kwargs['close_fds'])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertNotIn('SYSTEMD_UNIT_PATH', run.call_args.kwargs['env'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_container_inspect_accepts_running_or_stopped_without_entering(self):
        tools = self.tools()
        for running in (False, True):
            records = fake_container()
            records[0]['State']['Running'] = running
            with patch.object(tools, 'run', side_effect=['true false', json.dumps(records)]) as run:
                tools.check_container()
            commands = [c.args[0] for c in run.call_args_list]
            self.assertFalse(any('top' in c or 'enter' in c or 'start' in c for c in commands))

    def test_rootful_or_remote_container_engine_is_refused(self):
        tools = self.tools()
        for mode in ('false false', 'true true'):
            with patch.object(tools, 'run', return_value=mode), self.assertRaises(service.ServiceInstallError):
                tools.check_container()

    def test_wrong_container_metadata_is_refused(self):
        tools = self.tools()
        for modify in (
            lambda x: x['Config'].update(Image='different'),
            lambda x: x.update(OCIRuntime='runc'),
            lambda x: x['Config']['Labels'].update({'manager':'unrelated'}),
            lambda x: x['Config']['Labels'].update({'io.zombiecc.installer':'unrelated'}),
            lambda x: x['Config']['Annotations'].clear(),
            lambda x: x['State'].update(Paused=True),
        ):
            records = fake_container()
            modify(records[0])
            with patch.object(tools, 'run', side_effect=['true false', json.dumps(records)]), \
                 self.assertRaises(service.ServiceInstallError):
                tools.check_container()

    def test_show_accepts_notfound_but_not_bus_failure(self):
        tools = self.tools()
        with patch.object(tools, 'call', return_value=subprocess.CompletedProcess([],4,'LoadState=not-found\nActiveState=inactive\n','')):
            self.assertEqual(tools.show()['LoadState'], 'not-found')
        with patch.object(tools, 'call', return_value=subprocess.CompletedProcess([],1,'','No bus')), \
             self.assertRaisesRegex(service.ServiceInstallError, 'Cannot query'):
            tools.show()

    def test_reload_is_only_service_mutation(self):
        tools = self.tools()
        with patch.object(tools, 'run', return_value='') as run:
            tools.reload()
        self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', '--user', 'daemon-reload'])


class ProductionSupervisorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="zombiecc isolated 'test' ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').symlink_to(sys.executable)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.env = dict(os.environ, HOME=str(self.root), XDG_RUNTIME_DIR=str(self.runtime),
                        XDG_DATA_HOME=str(self.root / "my $data"), XDG_CONFIG_HOME=str(self.root / 'my "config"'),
                        XDG_STATE_HOME=str(self.root / 'my %state'), DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(self.runtime / 'bus'))
        self.children = []
        self.addCleanup(self.cleanup_children)

    def cleanup_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=4)

    def test_environment_values_are_reapplied_after_login_shell(self):
        expected = self.env.copy()
        command = supervisor._isolated_runtime_command(self.root, expected)
        (self.root / 'g13-test.py').write_text(
            'import json,os,site,sys\nprint(json.dumps({"env":dict(os.environ),"site":site.ENABLE_USER_SITE,"argv":sys.argv}))\n')
        # Simulate a login script clobbering the values before the actual command.
        clobber = 'export PYTHONNOUSERSITE=0 PYTHONPATH=/wrong PYTHONHOME=/wrong XDG_DATA_HOME=/wrong; '
        clobber += 'export XDG_CONFIG_HOME=/wrong XDG_STATE_HOME=/wrong XDG_RUNTIME_DIR=/wrong G13_DRIVER_SERVICE_CONTROL=wrong; '
        completed = subprocess.run(['/bin/bash','-c',clobber + command[2],*command[3:]],
                                   env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertFalse(data['site'])
        for name in ('PYTHONHOME', 'PYTHONPATH'):
            self.assertNotIn(name, data['env'])
        for name in ('XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_STATE_HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'):
            self.assertEqual(data['env'][name], self.env[name])
        self.assertEqual(data['env']['G13_DRIVER_SERVICE_CONTROL'], 'stdin-pipe-v1')

    def test_outer_environment_is_sanitized_and_forces_podman(self):
        env = self.env | {'PYTHONPATH':'bad','PYTHONHOME':'bad','DBX_CONTAINER_MANAGER':'docker'}
        supervisor._isolated_runtime_command(self.root, env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertNotIn('PYTHONHOME', env)
        self.assertEqual(env['PYTHONNOUSERSITE'], '1')
        self.assertEqual(env['DBX_CONTAINER_MANAGER'], 'podman')

    def test_relative_xdg_defaults_and_invalid_runtime(self):
        env = self.env | {'XDG_DATA_HOME':'relative','XDG_CONFIG_HOME':'','XDG_STATE_HOME':'relative'}
        supervisor._isolated_runtime_command(self.root, env)
        self.assertEqual(env['XDG_DATA_HOME'], str(self.root / '.local/share'))
        self.assertEqual(env['XDG_CONFIG_HOME'], str(self.root / '.config'))
        with self.assertRaisesRegex(ValueError, 'XDG_RUNTIME_DIR'):
            supervisor._isolated_runtime_command(self.root, env | {'XDG_RUNTIME_DIR':'relative'})
        with self.assertRaisesRegex(ValueError, 'HOME'):
            supervisor._isolated_runtime_command(self.root, env | {'HOME':''})

    def spawn_stub(self):
        for name in ('driver_service.py','driver_lifecycle.py'):
            shutil.copy2(PROJECT / name, self.root / name)
        (self.root / 'g13-test.py').write_text('''import os,site,time
from pathlib import Path
from driver_lifecycle import DriverLifecycle
root=Path.cwd()
assert site.ENABLE_USER_SITE is False
assert 'PYTHONPATH' not in os.environ
assert os.environ['G13_DRIVER_SERVICE_CONTROL']=='stdin-pipe-v1'
guard=DriverLifecycle()
guard.start()
(root/'ready').write_text('ready')
try:
    while not guard.stop_requested: time.sleep(.01)
finally:
    (root/'cleaned').write_text(guard.stop_reason)
''')
        fake = self.root / 'fake-distrobox'
        # distrobox prefix is: enter --name zombiecc --no-tty -- (5 args)
        fake.write_text('#!/bin/sh\n[ "$1" = enter ] || exit 81\n[ "$3" = zombiecc ] || exit 82\nshift 5\nexec "$@"\n')
        fake.chmod(0o700)
        wrapper = self.root / 'supervise.py'
        wrapper.write_text('from pathlib import Path\nfrom driver_service import run_service\n'
                           f'raise SystemExit(run_service(Path({str(self.root)!r}), container_name="zombiecc", distrobox={str(fake)!r}, isolated_runtime=True))\n')
        self.output = self.root / 'output.txt'
        with self.output.open('w') as out:
            child = subprocess.Popen([sys.executable,'-u',str(wrapper)],env=self.env,
                                     stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        self.children.append(child)
        self.wait_file('ready', child)
        return child

    def wait_file(self, name, child=None):
        limit = time.monotonic() + 6
        while not (self.root / name).exists():
            if time.monotonic() > limit or (child and child.poll() is not None):
                self.fail('Stub did not write '+name+':\n'+self.output.read_text())
            time.sleep(.02)
        return (self.root / name).read_text()

    def test_isolated_sigterm_retains_pipe_cleanup(self):
        child=self.spawn_stub()
        child.terminate()
        self.assertEqual(child.wait(timeout=7),0,self.output.read_text())
        self.assertEqual(self.wait_file('cleaned'),'stop requested')

    def test_isolated_sigint_retains_pipe_cleanup(self):
        child=self.spawn_stub()
        child.send_signal(signal.SIGINT)
        self.assertEqual(child.wait(timeout=7),0,self.output.read_text())
        self.assertEqual(self.wait_file('cleaned'),'stop requested')

    def test_isolated_forced_supervisor_exit_releases_pipe(self):
        child=self.spawn_stub()
        child.kill()
        child.wait(timeout=7)
        self.assertEqual(self.wait_file('cleaned'),'stop requested')

    def test_cli_flag_defaults_to_development_behavior(self):
        with patch.object(sys,'argv',['driver_service.py']), patch.object(Path,'exists',return_value=False), \
             patch.dict(os.environ,{},clear=True), patch.object(supervisor,'run_service',return_value=0) as run:
            self.assertEqual(supervisor.main(),0)
        self.assertEqual(run.call_args.args[1],'g13-fedora')
        self.assertFalse(run.call_args.kwargs['isolated_runtime'])
        with patch.object(sys,'argv',['driver_service.py','--container','zombiecc','--isolated-runtime']), \
             patch.object(Path,'exists',return_value=False), patch.dict(os.environ,{},clear=True), \
             patch.object(supervisor,'run_service',return_value=0) as run:
            self.assertEqual(supervisor.main(),0)
        self.assertEqual(run.call_args.args[1],'zombiecc')
        self.assertTrue(run.call_args.kwargs['isolated_runtime'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
