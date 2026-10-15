"""Stage-1 installer tests: real file operations, fake Podman/Distrobox.

Run beside install.sh: python3 -m unittest -v test_install
No real container engine, device, service, or user-data directory is used.
The host guards are neutralized ONLY in a private copy so tests can run in CI
containers; separate tests retain the container/root checks.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import unittest

INSTALLER = Path(__file__).resolve().with_name('install.sh')
FILES = (
    'VERSION', 'app_version.py', 'editor.py', 'g13-test.py',
    'driver_service.py', 'profile_manager.py',
    'app_paths.py', 'user_data_bootstrap.py', 'bindings.py',
    'assets/g13_device.png', 'resources/defaults/default-profile.json',
    'resources/defaults/settings.json', 'app-files.txt',
    'container-packages.txt', 'requirements-runtime.txt',
)
CONTAINER_GUARD = 'if [[ -n "${CONTAINER_ID:-}" || -e /run/.containerenv || -e /.dockerenv ]]; then'
MOCK_ENGINE = r'''#!/usr/bin/env -S python3 -S
import json, os, sys
from pathlib import Path
root = Path(os.environ['MOCK_ROOT'])
args = sys.argv[1:]
tool = Path(sys.argv[0]).name

if (
    tool == 'distrobox'
    and os.environ.get(
        'MOCK_ASSERT_FD9_CLOSED'
    ) == '1'
):
    try:
        os.fstat(9)

    except OSError:
        pass

    else:
        print(
            'installer lock fd 9 leaked into distrobox',
            file=sys.stderr,
        )
        sys.exit(86)

with (root / 'commands.jsonl').open('a') as log:
    log.write(json.dumps([tool, args]) + '\n')
statefile = root / 'container.json'
state = json.loads(statefile.read_text()) if statefile.exists() else None
if tool == 'podman':
    if args[0] == 'info':
        print(os.environ.get('MOCK_MODE', 'true false')); sys.exit(0)
    if args[:2] == ['container', 'exists']:
        sys.exit(int(os.environ.get('MOCK_EXISTS_EXIT', '0' if state is not None else '1')))
    if args[:2] == ['container', 'inspect']:
        if state is None: sys.exit(125)
        q = args[args.index('--format')+1]
        values = {
            '{{index .Config.Labels "manager"}}': state.get('manager','distrobox'),
            '{{index .Config.Labels "io.zombiecc.installer"}}': state.get('owner','stage1-v1'),
            '{{.Config.Image}}': state.get('image','quay.io/fedora/fedora:44'),
            '{{.OCIRuntime}}': state.get('runtime','crun'),
            '{{index .Config.Labels "distrobox.unshare_groups"}}': state.get('unshare','0'),
            '{{index .Config.Annotations "run.oci.keep_original_groups"}}': state.get('keep','1'),
            '{{.State.Running}}': state.get('running','false'),
            '{{.State.Paused}}': state.get('paused','false'),
        }
        print(values[q]); sys.exit(0)
    if args[0] == 'top':
        print('COMMAND\n' + state.get('processes','/bin/bash')); sys.exit(0)
    raise SystemExit('Unexpected podman arguments: '+repr(args))
if tool == 'distrobox':
    if args == ['--version']:
        print(
            os.environ.get(
                'MOCK_DISTROBOX_VERSION',
                'distrobox: 1.8.1.2',
            )
        )
        sys.exit(0)

    if args[0] == 'create':
        if os.environ.get('MOCK_FAIL') == 'create': sys.exit(9)
        if state is not None: raise SystemExit('Attempted to recreate an existing container')
        flags = args[args.index('--additional-flags')+1]
        assert '--runtime=crun' in flags
        assert '--label=io.zombiecc.installer=stage1-v1' in flags
        assert '--yes' in args and '--no-entry' in args
        statefile.write_text('{}'); sys.exit(0)
    if args[0] != 'enter': raise SystemExit('Unexpected distrobox action')
    if 'zombiecc-package-setup' in args:
        step='packages'; i=args.index('zombiecc-package-setup')
        script=args[i-1]
        assert 'sudo -n dnf install' in script
    elif 'zombiecc-python-setup' in args:
        step='python'; i=args.index('zombiecc-python-setup')
        script=args[i-1]; app=Path(args[i+1])
        assert '--system-site-packages' in script
        assert '--only-binary=:all:' in script
        assert 'PYTHONNOUSERSITE=1' in script
        assert '--upgrade pip' not in script
        v=app/'.venv'; v.mkdir(exist_ok=True)
        (v/'pyvenv.cfg').write_text('include-system-site-packages = true\nversion = 3.14.7\n')
    elif 'zombiecc-runtime-check' in args:
        step='check'
        code=sys.stdin.read()
        compile(code,'<embedded-runtime-check>','exec')
        assert 'import editor' not in code and 'ProfileManager()' not in code
        (root/'runtime_check.py').write_text(code)
    else: raise SystemExit('Unknown enter command')
    if os.environ.get('MOCK_FAIL') == step: sys.exit(9)
    sys.exit(0)
raise SystemExit('Unexpected mocked command: '+tool)
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zombiecc installer test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source with spaces and 'quotes'"
        self.home = self.root / 'home'
        self.mock = self.root / 'mock'
        self.bin = self.mock / 'bin'
        self.dev = self.root / 'dev'
        self.input_dir = self.dev / 'input'
        self.uinput = self.dev / 'uinput'

        for p in (
            self.source,
            self.home,
            self.bin,
            self.input_dir,
        ):
            p.mkdir(parents=True)

        self.uinput.write_text('')
        (
            self.input_dir
            / 'event0'
        ).write_text('')

        self.original = INSTALLER.read_text()
        self.write_installer()
        for name in FILES:
            p = self.source / name
            p.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            p.write_text(
                'pass\n'
                if name.endswith('.py')
                else '{}\n'
            )

        (
            self.source
            / 'VERSION'
        ).write_text(
            '1.0.0\n'
        )

        (
            self.source
            / 'app-files.txt'
        ).write_text(
            '\n'.join(FILES)
            + '\n'
        )
        (self.source/'container-packages.txt').write_text('python3-pip\npython3-evdev\nhidapi\n')
        (self.source/'requirements-runtime.txt').write_text('PySide6==6.11.1\nhid==1.0.9\nlupa==2.8\n')
        (self.source/'zombiecc').write_text('#!/usr/bin/env bash\nexit 0\n')
        for name in ('podman','distrobox'):
            p=self.bin/name; p.write_text(MOCK_ENGINE); p.chmod(0o755)
        p=self.bin/'crun'
        p.write_text(
            '#!/bin/sh\nexit 0\n'
        )
        p.chmod(0o755)

        p=self.bin/'getsebool'
        p.write_text(
            '#!/bin/sh\n'
            '[ "${MOCK_SELINUX:-on}" != unavailable ] '
            '|| exit 1\n'
            'printf "container_use_devices --> %s\\n" '
            '"${MOCK_SELINUX:-on}"\n'
        )
        p.chmod(0o755)

        self.env=os.environ.copy()
        for key in tuple(self.env):
            if key.startswith(('XDG_', 'DBX_', 'ZOMBIECC_', 'MOCK_')) or key in ('CONTAINER_ID', 'BASH_ENV','ENV','PYTHONHOME','PYTHONPATH'):
                self.env.pop(key,None)
        self.env.update(HOME=str(self.home), MOCK_ROOT=str(self.mock),
                        PATH=str(self.bin)+os.pathsep+os.environ['PATH'])
        self.app=self.home/'.local/share/zombiecc/app'
        self.launcher=self.home/'.local/bin/zombiecc'

    def write_installer(self, keep_root=False):
        # Test fixture only: leave CONTAINER_ID detection active, but not the
        # CI machine's real /.dockerenv; production install.sh is untouched.
        self.assertIn(CONTAINER_GUARD, self.original)
        code=self.original.replace(
            CONTAINER_GUARD,
            'if [[ -n "${CONTAINER_ID:-}" ]]; then',
            1,
        )

        code=code.replace(
            'UINPUT_PATH=/dev/uinput',
            (
                'UINPUT_PATH='
                + shlex.quote(
                    str(self.uinput)
                )
            ),
            1,
        )

        code=code.replace(
            'INPUT_DIR=/dev/input',
            (
                'INPUT_DIR='
                + shlex.quote(
                    str(self.input_dir)
                )
            ),
            1,
        )

        if not keep_root:
            code=code.replace('(( EUID != 0 ))', '(( 1000 != 0 ))',1)
        p=self.source/'install.sh'; p.write_text(code); p.chmod(0o755)

    def run_install(self,*args,**updates):
        env=self.env.copy(); env.update(updates)
        return subprocess.run(['bash',str(self.source/'install.sh'),*args],
                              env=env,cwd=self.root,stdin=subprocess.DEVNULL,
                              capture_output=True,text=True,timeout=30)

    def ok(self,*args,**updates):
        r=self.run_install(*args,**updates)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        return r

    def bad(self,needle,*args,**updates):
        r=self.run_install(*args,**updates)
        self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn(needle,r.stdout+r.stderr)
        return r

    def add_entry(self,text):
        with (self.source/'app-files.txt').open('a') as f: f.write(text+'\n')

    def set_container(self,**state):
        (self.mock/'container.json').write_text(json.dumps(state))

    def commands(self):
        p=self.mock/'commands.jsonl'
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_paused_container_is_refused(self):
        self.set_container(paused='true',running='true')
        self.bad('is paused','--dry-run')

    def test_distrobox_children_do_not_inherit_installer_lock(
        self,
    ):
        self.ok(
            '--yes',
            MOCK_ASSERT_FD9_CLOSED='1',
        )

    def test_second_installer_is_refused_by_lock(self):
        self.app.parent.mkdir(parents=True)
        with (self.app.parent/'.install.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.bad('Another installer','--yes')
        distrobox_calls = [
            args
            for tool, args in self.commands()
            if tool == 'distrobox'
        ]

        self.assertEqual(
            distrobox_calls,
            [['--version']],
        )

    def test_shell_syntax(self):
        subprocess.run(['bash','-n',str(INSTALLER)],check=True)

    def test_minimum_distrobox_version_is_accepted(self):
        self.ok(
            '--dry-run',
            MOCK_DISTROBOX_VERSION='distrobox: 1.8.1.2',
        )

    def test_old_distrobox_version_is_refused(self):
        self.bad(
            'Distrobox 1.8.1.2 or newer is required',
            '--dry-run',
            MOCK_DISTROBOX_VERSION='distrobox: 1.8.1.1',
        )

    def test_unrecognized_distrobox_version_is_refused(self):
        self.bad(
            'Unrecognized Distrobox version',
            '--dry-run',
            MOCK_DISTROBOX_VERSION='development build',
        )

    def test_dry_run_has_no_installation_writes_or_distrobox_actions(self):
        r=self.ok('--dry-run')
        self.assertIn('DRY RUN complete',r.stdout)
        self.assertFalse((self.home/'.local').exists())

        distrobox_calls = [
            args
            for tool, args in self.commands()
            if tool == 'distrobox'
        ]

        self.assertEqual(
            distrobox_calls,
            [['--version']],
        )

    def test_device_preflight_reports_access(self):
        r=self.ok('--dry-run')

        self.assertIn(
            (
                'Host devices: uinput writable; '
                '1/1 input event devices readable'
            ),
            r.stdout,
        )

        self.assertIn(
            (
                'SELinux:      '
                'container_use_devices=on'
            ),
            r.stdout,
        )

    def test_missing_uinput_is_refused(self):
        self.uinput.unlink()

        self.bad(
            'virtual input device is missing',
            '--dry-run',
        )

    def test_selinux_device_access_off_is_refused(self):
        self.bad(
            'container_use_devices is off',
            '--dry-run',
            MOCK_SELINUX='off',
        )

    def test_selinux_check_unavailable_is_not_fatal(self):
        r=self.ok(
            '--dry-run',
            MOCK_SELINUX='unavailable',
        )

        self.assertIn(
            'SELinux:      not applicable',
            r.stdout,
        )

    def test_no_input_events_is_not_fatal(self):
        (
            self.input_dir
            / 'event0'
        ).unlink()

        r=self.ok('--dry-run')

        self.assertIn(
            '0/0 input event devices readable',
            r.stdout,
        )

    def test_complete_install_uses_only_manifest_and_final_venv(self):
        (self.source/'test_personal.py').write_text('do not ship')
        (self.source/'secret.txt').write_text('do not ship')
        self.ok('--yes')
        for name in FILES: self.assertEqual((self.app/name).read_bytes(),(self.source/name).read_bytes())
        self.assertFalse((self.app/'test_personal.py').exists())
        self.assertFalse((self.app/'secret.txt').exists())
        self.assertTrue((self.app/'.venv/pyvenv.cfg').exists())
        self.assertEqual(self.launcher.stat().st_mode & 0o777,0o755)
        self.assertEqual(self.launcher.read_bytes(),(self.source/'zombiecc').read_bytes())
        self.assertFalse(list(self.app.parent.glob('.install-stage.*')))

    def test_user_profiles_settings_and_service_are_preserved(self):
        sentinels=[self.home/'.local/share/zombiecc/profiles/default.json',
                   self.home/'.config/zombiecc/settings.json',
                   self.home/'.config/systemd/user/g13-driver.service']
        for p in sentinels: p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'personal\x00data')
        self.ok('--yes')
        for p in sentinels: self.assertEqual(p.read_bytes(),b'personal\x00data')

    def test_repeated_install_does_not_recreate_container(self):
        self.ok('--yes'); self.ok('--yes')
        creates=[c for c in self.commands() if c[0]=='distrobox' and c[1][0]=='create']
        self.assertEqual(len(creates),1)

    def test_existing_identical_launcher_is_kept(self):
        self.launcher.parent.mkdir(parents=True)
        shutil.copyfile(self.source/'zombiecc',self.launcher)
        inode=self.launcher.stat().st_ino
        self.ok('--yes')
        self.assertEqual(self.launcher.stat().st_ino,inode)

    def test_changed_payload_is_not_an_implicit_upgrade(self):
        self.ok('--yes')
        old=(self.app/'editor.py').read_bytes()
        (self.source/'editor.py').write_text('new code\n')
        self.bad('does not perform upgrades','--yes')
        self.assertEqual((self.app/'editor.py').read_bytes(),old)

    def test_changed_installed_file_is_not_overwritten(self):
        self.ok('--yes'); (self.app/'editor.py').write_text('user edit\n')
        self.bad('changed or removed','--yes')
        self.assertEqual((self.app/'editor.py').read_text(),'user edit\n')

    def test_unmarked_existing_app_is_not_touched(self):
        self.app.mkdir(parents=True); (self.app/'mine').write_text('keep')
        self.bad('not owned by this installer','--yes')
        self.assertEqual((self.app/'mine').read_text(),'keep')

    def test_unrelated_launcher_is_not_touched(self):
        self.launcher.parent.mkdir(parents=True); self.launcher.write_text('mine')
        self.bad('different launcher','--yes')
        self.assertEqual(self.launcher.read_text(),'mine')

    def test_symlinked_launcher_is_not_touched(self):
        self.launcher.parent.mkdir(parents=True)
        target=self.root/'keep'; target.write_text('keep')
        self.launcher.symlink_to(target)
        self.bad('launcher is a symlink','--yes')
        self.assertEqual(target.read_text(),'keep')

    def test_symlinked_app_is_refused(self):
        self.app.parent.mkdir(parents=True); self.app.symlink_to(self.source,target_is_directory=True)
        self.bad('must be separate','--yes')

    def test_source_symlink_is_refused(self):
        p=self.source/'bindings.py'; p.unlink(); p.symlink_to(self.source/'editor.py')
        self.bad('Source symlink','--dry-run')

    def test_missing_manifest_file_is_refused(self):
        (self.source/'bindings.py').unlink()
        self.bad('Missing/unreadable source','--dry-run')

    def test_missing_required_manifest_entry_is_refused(self):
        p=self.source/'app-files.txt'; p.write_text(p.read_text().replace('editor.py\n',''))
        self.bad('Required app manifest entry','--dry-run')

    def test_duplicate_manifest_is_refused(self):
        self.add_entry('editor.py'); self.bad('Duplicate app manifest','--dry-run')

    def test_parent_traversal_is_refused(self):
        self.add_entry('../secret'); self.bad('Invalid manifest path','--dry-run')

    def test_absolute_manifest_path_is_refused(self):
        self.add_entry('/etc/passwd'); self.bad('Invalid manifest path','--dry-run')

    def test_development_file_is_refused_even_when_present(self):
        (self.source/'test_x.py').write_text('pass\n'); self.add_entry('test_x.py')
        self.bad('Non-app file','--dry-run')

    def test_heredoc_wrapper_in_package_manifest_is_refused(self):
        (self.source/'container-packages.txt').write_text("cat > container-packages.txt <<'EOF'\nhidapi\nEOF\n")
        self.bad('Invalid RPM package name','--dry-run')

    def test_requirement_options_are_refused(self):
        with (self.source/'requirements-runtime.txt').open('a') as f: f.write('--target /tmp/elsewhere\n')
        self.bad('Expected a pinned','--dry-run')

    def test_user_evdev_pin_is_refused(self):
        with (self.source/'requirements-runtime.txt').open('a') as f: f.write('evdev==1.9.2\n')
        self.bad('must come from Fedora','--dry-run')

    def test_container_context_is_refused(self):
        self.bad('Run on the host','--dry-run',CONTAINER_ID='g13-fedora')

    def test_root_guard(self):
        if os.geteuid()!=0: self.skipTest('Requires root CI user to exercise this guard.')
        self.write_installer(keep_root=True)
        self.bad('without sudo','--dry-run')

    def test_remote_or_rootful_engine_is_refused(self):
        for mode in ('true true','false false'):
            with self.subTest(mode=mode): self.bad('local rootless','--dry-run',MOCK_MODE=mode)

    def test_podman_storage_error_is_not_treated_as_missing_container(self):
        self.bad('not treated as a missing','--yes',MOCK_EXISTS_EXIT='125')
        distrobox_calls = [
            args
            for tool, args in self.commands()
            if tool == 'distrobox'
        ]

        self.assertEqual(
            distrobox_calls,
            [['--version']],
        )

    def test_unrelated_container_is_not_replaced(self):
        self.set_container(owner='other')
        self.bad('will not be replaced','--yes')
        distrobox_calls = [
            args
            for tool, args in self.commands()
            if tool == 'distrobox'
        ]

        self.assertEqual(
            distrobox_calls,
            [['--version']],
        )

    def test_wrong_runtime_or_groups_or_image_is_refused(self):
        for state in ({'runtime':'runc'},{'keep':'0'},{'unshare':'1'},{'image':'quay.io/fedora/fedora:40'}):
            with self.subTest(state=state):
                self.set_container(**state); self.bad('will not be replaced','--dry-run')

    def test_running_production_driver_blocks_rerun(self):
        self.set_container(running='true',processes='python -u g13-test.py')
        self.bad('Close it and stop its driver','--yes')

    def test_xdg_paths_with_shell_characters_are_passed_as_data(self):
        data=self.home/"data space 'quote' $dollar;literal"
        state=self.home/'state space'
        self.ok('--yes',XDG_DATA_HOME=str(data),XDG_STATE_HOME=str(state))
        self.assertTrue((data/'zombiecc/app/.venv/pyvenv.cfg').is_file())
        self.assertFalse(self.app.exists())

    def test_relative_xdg_uses_default(self):
        self.ok('--dry-run',XDG_DATA_HOME='relative',XDG_STATE_HOME='relative')
        self.assertFalse((self.root/'relative').exists())

    def test_noninteractive_actual_install_requires_yes(self):
        self.bad('Interactive confirmation required')
        self.assertFalse(self.app.exists())

    def test_package_failure_does_not_publish_app_or_launcher(self):
        self.bad('Stage failed','--yes',MOCK_FAIL='packages')
        self.assertFalse(self.app.exists()); self.assertFalse(self.launcher.exists())
        self.assertFalse(list(self.app.parent.glob('.install-stage.*')))
        self.ok('--yes')

    def test_python_failure_retains_payload_and_can_retry(self):
        self.bad('Stage failed','--yes',MOCK_FAIL='python')
        self.assertTrue((self.app/'.zombiecc-stage1').is_file())
        self.assertFalse(self.launcher.exists())
        self.ok('--yes')

    def test_runtime_check_failure_does_not_publish_launcher(self):
        self.bad('Stage failed','--yes',MOCK_FAIL='check')
        self.assertFalse(self.launcher.exists())
        self.assertTrue(list((self.home/'.local/state/zombiecc/logs').glob('install-*.log')))
        self.ok('--yes')

    def test_successful_run_compiles_the_embedded_check(self):
        self.ok('--yes')
        code=(self.mock/'runtime_check.py').read_text()
        compile(code,'<check>','exec')
        self.assertIn("from PySide6 import QtCore, QtGui, QtWidgets",code)
        self.assertIn("from lupa.lua51 import LuaRuntime",code)

    def test_nested_source_destination_is_refused(self):
        self.bad('must be separate','--dry-run',XDG_DATA_HOME=str(self.source/'data'))

    def test_help_has_no_preflight_side_effects(self):
        r=self.ok('--help')
        self.assertIn('Usage:',r.stdout)
        self.assertEqual(self.commands(),[])

    def test_no_real_service_or_device_commands_are_issued(self):
        self.ok('--yes')
        for tool,args in self.commands():
            self.assertNotIn(tool,('systemctl','udevadm','usermod','setsebool'))
            self.assertNotIn('g13-fedora',args)
            self.assertNotIn('zombiecc-minimal',args)


if __name__=='__main__':
    unittest.main()
