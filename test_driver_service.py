"""Hardware-free service-control tests.

Run beside driver_service.py and the edited driver_lifecycle.py.
Only temporary stub drivers, pipes, file locks and isolated subprocesses are
used. A fake Distrobox forwards its arguments to a local test process: this
checks our supervision protocol, NOT real Distrobox/systemd integration.
Never imports or launches your g13-test.py, uses the real driver socket, or
changes settings, profiles, onboard memory or user service configuration.
"""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parent
service = None
lifecycle = None


class WatchdogTests(unittest.TestCase):
    def test_manual_launch_does_not_read_stdin(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(service.os, 'fstat') as fstat:
            callback = Mock()
            self.assertIsNone(service.start_service_watchdog(callback))
            fstat.assert_not_called()
            callback.assert_not_called()

    def test_supervised_launch_rejects_non_pipe_stdin(self):
        with patch.dict(os.environ, {service.CONTROL_ENVIRONMENT: service.CONTROL_MODE}), \
             patch.object(service.os, 'fstat', return_value=type('Info', (), {'st_mode': stat.S_IFCHR})()):
            with self.assertRaisesRegex(RuntimeError, 'control pipe'):
                service.start_service_watchdog(Mock())

    def run_pipe(self, payload=b''):
        read_fd, write_fd = os.pipe()
        watched_fd = os.dup(read_fd)
        callback = Mock()
        try:
            with patch.dict(os.environ, {service.CONTROL_ENVIRONMENT: service.CONTROL_MODE}), \
                 patch.object(service.os, 'fstat', return_value=type('Info', (), {'st_mode': stat.S_IFIFO})()), \
                 patch.object(service.os, 'dup', return_value=watched_fd):
                thread = service.start_service_watchdog(callback)
            self.assertTrue(thread.daemon)
            self.assertFalse(os.get_inheritable(watched_fd))
            if payload:
                os.write(write_fd, payload)
            # The pipe staying open must not request shutdown.
            thread.join(0.05)
            self.assertTrue(thread.is_alive())
            callback.assert_not_called()
            os.close(write_fd)
            write_fd = None
            thread.join(2)
            self.assertFalse(thread.is_alive())
            callback.assert_called_once_with()
        finally:
            if write_fd is not None:
                os.close(write_fd)
            os.close(read_fd)

    def test_pipe_stays_live_until_eof(self):
        self.run_pipe()

    def test_bytes_are_not_stop_commands(self):
        self.run_pipe(b'ignored data')

    def test_public_stop_request_only_sets_flag(self):
        guard = lifecycle.DriverLifecycle()
        guard.request_stop()
        self.assertTrue(guard.stop_requested)
        self.assertIsNone(guard.stop_signal)
        self.assertEqual(guard.stop_reason, 'stop requested')

    def test_public_stop_request_preserves_actual_signal(self):
        guard = lifecycle.DriverLifecycle()
        guard._request_stop(signal.SIGINT, None)
        guard.request_stop()
        self.assertEqual(guard.stop_reason, 'SIGINT')

    def test_watchdog_start_failure_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {'XDG_RUNTIME_DIR': temp}), \
             patch.object(lifecycle, 'start_service_watchdog', side_effect=RuntimeError('watchdog failure')):
            guard = lifecycle.DriverLifecycle()
            with self.assertRaisesRegex(RuntimeError, 'watchdog failure'):
                guard.start()
            self.assertIsNone(guard._lock_fd)
            self.assertEqual(guard._previous_handlers, {})


STUB_DRIVER = '''import os
import time
from pathlib import Path
from driver_lifecycle import DriverLifecycle, DriverAlreadyRunning
root = Path.cwd()
guard = DriverLifecycle()
try:
    guard.start()
except DriverAlreadyRunning:
    print("STUB DUPLICATE REJECTED", flush=True)
    raise SystemExit(75)
(root / "driver.pid").write_text(str(os.getpid()))
(root / "ready").write_text("ready")
print("STUB READY", flush=True)
try:
    if os.environ.get("STUB_EXIT_CODE"):
        raise SystemExit(int(os.environ["STUB_EXIT_CODE"]))
    while not guard.stop_requested:
        time.sleep(0.01)
finally:
    print("STUB CLEANUP: " + guard.stop_reason, flush=True)
    (root / "cleaned").write_text(guard.stop_reason)
'''


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='g13 service test ')
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.env = dict(os.environ)
        self.env.pop(service.CONTROL_ENVIRONMENT, None)
        self.env['XDG_RUNTIME_DIR'] = str(self.runtime)
        self.env['PYTHONPATH'] = str(PROJECT)
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').symlink_to(sys.executable)
        (self.root / 'g13-test.py').write_text(STUB_DRIVER)
        self.fake = self.root / 'fake-distrobox'
        fake_script = self.root / 'fake-distrobox.py'

        fake_script.write_text(
            'import os, sys\n'
            'assert sys.argv[1:6] == ["enter", "--name", "g13-fedora", "--no-tty", "--"]\n'
            'args = sys.argv[6:]\n'
            'os.execvp(args[0], args)\n',
            encoding='utf-8',
        )

        # A #! interpreter path cannot contain spaces. Use a shell
        # wrapper with quoted paths, keeping stdin for the control pipe.
        self.fake.write_text(
            '#!/bin/sh\n'
            f'exec {shlex.quote(sys.executable)} '
            f'{shlex.quote(str(fake_script))} "$@"\n',
            encoding='utf-8',
        )
        self.fake.chmod(0o700)
        self.children = []
        self.logs = []
        self.driver_pidfd = None

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
        # A forced-supervisor-exit test can leave an adopted stub briefly alive.
        # This pidfd refers ONLY to the isolated stub created by this test.
        if self.driver_pidfd is not None:
            try:
                signal.pidfd_send_signal(self.driver_pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(self.driver_pidfd)
        for log in self.logs:
            log.close()
        self.temp.cleanup()

    def spawn(self, extra_env=None, fake=None):
        env = self.env | (extra_env or {})
        code = (
            'from pathlib import Path\nfrom driver_service import run_service\n'
            f'raise SystemExit(run_service(Path({str(self.root)!r}), '
            f'distrobox={str(fake or self.fake)!r}))\n'
        )
        log_path = self.root / f'log-{len(self.children)}'
        log = log_path.open('wb')
        self.logs.append(log)
        child = subprocess.Popen(
            [sys.executable, '-B', '-u', '-c', code],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
        self.children.append(child)
        return child, log_path

    def wait_file(self, name):
        deadline = time.monotonic() + 5
        target = self.root / name
        while not target.exists():
            if time.monotonic() > deadline:
                output = '\n'.join(p.read_text() for p in self.root.glob('log-*'))
                self.fail(f'Timed out waiting for {name}:\n{output}')
            time.sleep(0.02)
        return target.read_text()

    def ready(self):
        self.wait_file('ready')
        if self.driver_pidfd is None:
            pid = int(self.wait_file('driver.pid'))
            self.driver_pidfd = os.pidfd_open(pid)

    def test_sigterm_requests_cleanup_and_returns_zero(self):
        child, log = self.spawn()
        self.ready()
        self.assertFalse((self.root / 'cleaned').exists())
        child.terminate()
        self.assertEqual(child.wait(timeout=5), 0, log.read_text())
        self.assertEqual(self.wait_file('cleaned'), 'stop requested')
        self.assertIn('Distrobox driver command exited: 0', log.read_text())

    def test_sigint_requests_cleanup_and_returns_zero(self):
        child, log = self.spawn()
        self.ready()
        child.send_signal(signal.SIGINT)
        self.assertEqual(child.wait(timeout=5), 0, log.read_text())
        self.assertEqual(self.wait_file('cleaned'), 'stop requested')

    def test_forced_supervisor_exit_closes_pipe_and_stops_stub(self):
        child, _ = self.spawn()
        self.ready()
        child.kill()
        child.wait(timeout=5)
        self.assertEqual(self.wait_file('cleaned'), 'stop requested')

    def test_duplicate_service_exits_75_without_stopping_original(self):
        first, _ = self.spawn()
        self.ready()
        second, log = self.spawn()
        self.assertEqual(second.wait(timeout=5), 75, log.read_text())
        self.assertIsNone(first.poll())
        self.assertFalse((self.root / 'cleaned').exists())
        first.terminate()
        self.assertEqual(first.wait(timeout=5), 0)

    def test_stub_failure_status_is_preserved(self):
        child, log = self.spawn({'STUB_EXIT_CODE': '42'})
        self.assertEqual(child.wait(timeout=5), 42, log.read_text())
        self.assertIn('Distrobox driver command exited: 42', log.read_text())

    def test_container_client_failure_status_is_preserved(self):
        fake = self.root / 'failed-client'
        fake.write_text('#!/bin/sh\nexit 19\n')
        fake.chmod(0o700)
        child, log = self.spawn(fake=fake)
        self.assertEqual(child.wait(timeout=5), 19, log.read_text())
        self.assertFalse((self.root / 'ready').exists())

    def test_missing_driver_is_rejected_before_launch(self):
        (self.root / 'g13-test.py').unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing'), \
             patch.object(service.subprocess, 'Popen') as popen:
            service.run_service(self.root, distrobox=str(self.fake))
        popen.assert_not_called()


def main():
    global PROJECT, service, lifecycle
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=PROJECT)
    args = parser.parse_args()
    PROJECT = args.project.resolve()
    sys.path.insert(0, str(PROJECT))
    service = importlib.import_module('driver_service')
    lifecycle = importlib.import_module('driver_lifecycle')
    suite = unittest.TestSuite()
    for cls in (WatchdogTests, SupervisorTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
