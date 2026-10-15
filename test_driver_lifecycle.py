"""Hardware-free startup/stop tests for the G13 driver's lifecycle guard.

Run beside driver_lifecycle.py and g13-test.py:
    python3 test_driver_lifecycle.py

Uses real Linux file locks and signals in isolated child processes, plus the
actual driver's pre-import guard and main() with simulated dependencies.
NEVER imports/starts the real driver or imports its hardware modules. All lock
files use temporary directories, not the real user's driver runtime directory.
No settings, profiles, onboard memory, sockets, or real input devices are used.

Optional: --project /path/to/source/copies
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parent
lifecycle_module = None
DRIVER_PREFIX = ""
MAIN_CODE = None


def load_sources(project: Path) -> None:
    global lifecycle_module, DRIVER_PREFIX, MAIN_CODE
    module_path = project / "driver_lifecycle.py"
    spec = importlib.util.spec_from_file_location("_lifecycle_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {module_path}")
    lifecycle_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lifecycle_module
    spec.loader.exec_module(lifecycle_module)

    driver_path = project / "g13-test.py"
    tree = ast.parse(driver_path.read_text(encoding="utf-8"), str(driver_path))
    prefix = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "decoder":
            break
        prefix.append(node)
    else:
        raise RuntimeError("Cannot locate the first driver-runtime import.")

    # Prevent the test harness from executing an unexpected import/resource setup.
    allowed_modules = {"os", "time", "driver_lifecycle"}
    for node in ast.walk(ast.Module(body=prefix, type_ignores=[])):
        if isinstance(node, ast.Import):
            if any(alias.name not in allowed_modules for alias in node.names):
                raise RuntimeError("Unexpected import before the driver guard.")
        if isinstance(node, ast.ImportFrom) and node.module not in allowed_modules:
            raise RuntimeError("Unexpected import before the driver guard.")
    if not any(isinstance(node, ast.ImportFrom) and node.module == "driver_lifecycle"
               for node in prefix):
        raise RuntimeError("Driver lifecycle import not found before runtime imports.")
    DRIVER_PREFIX = ast.unparse(ast.Module(body=prefix, type_ignores=[])) + "\n"
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    MAIN_CODE = compile(ast.Module(body=[main], type_ignores=[]), str(driver_path), "exec")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g13-lifecycle-test-")
        self.runtime = Path(self.temp.name)
        self.env_patch = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)})
        self.env_patch.start()
        self.guards = []
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()  # Only an isolated child created by this test.
            child.communicate(timeout=5)
        for guard in reversed(self.guards):
            guard.close()
        self.env_patch.stop()
        self.temp.cleanup()

    def guard(self):
        value = lifecycle_module.DriverLifecycle()
        self.guards.append(value)
        return value

    def spawn(self, suffix):
        code = f"import sys\nsys.path.insert(0, {str(PROJECT)!r})\n" + DRIVER_PREFIX + suffix
        child = subprocess.Popen(
            [sys.executable, "-B", "-u", "-c", code],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ), start_new_session=True,
        )
        self.children.append(child)
        return child

    def wait_for(self, child, marker: bytes):
        data = b""
        deadline = time.monotonic() + 5
        while marker not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail(f"Child did not emit {marker!r}; received {data!r}")
            ready, _, _ = select.select([child.stdout], [], [], remaining)
            if ready:
                chunk = os.read(child.stdout.fileno(), 4096)
                if not chunk:
                    self.fail(f"Child exited before {marker!r}; received {data!r}")
                data += chunk
        return data

    def test_lock_acquires_and_is_not_inherited_by_exec(self):
        guard = self.guard()
        guard.start()
        self.assertIsNotNone(guard._lock_fd)
        self.assertFalse(os.get_inheritable(guard._lock_fd))
        self.assertFalse(guard.stop_requested)

    def test_second_instance_rejected(self):
        first = self.guard()
        first.start()
        with self.assertRaises(lifecycle_module.DriverAlreadyRunning):
            self.guard().start()

    def test_rejected_instance_does_not_release_first_lock(self):
        first = self.guard()
        first.start()
        other = self.guard()
        with self.assertRaises(lifecycle_module.DriverAlreadyRunning):
            other.start()
        other.close()
        with self.assertRaises(lifecycle_module.DriverAlreadyRunning):
            self.guard().start()

    def test_repeated_start_on_same_object_is_rejected(self):
        guard = self.guard()
        guard.start()
        with self.assertRaises(RuntimeError):
            guard.start()

    def test_close_is_idempotent_and_allows_next_start(self):
        guard = self.guard()
        guard.start()
        guard.close()
        guard.close()
        self.guard().start()

    def test_existing_unlocked_file_is_not_treated_as_a_running_driver(self):
        path = self.runtime / f"g13-driver-{os.getuid()}.lock"
        path.write_bytes(b"old marker")
        self.guard().start()
        self.assertEqual(path.read_bytes(), b"old marker")

    def test_lock_path_is_not_unlinked_on_exit(self):
        guard = self.guard()
        guard.start()
        path = self.runtime / f"g13-driver-{os.getuid()}.lock"
        inode = path.stat().st_ino
        guard.close()
        self.guard().start()
        self.assertEqual(path.stat().st_ino, inode)

    def test_relative_runtime_directory_is_rejected(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "relative"}):
            with self.assertRaises(RuntimeError):
                self.guard().start()

    def test_missing_runtime_directory_is_not_created(self):
        missing = self.runtime / "missing"
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(missing)}):
            with self.assertRaises(FileNotFoundError):
                self.guard().start()
        self.assertFalse(missing.exists())

    def test_symlink_lock_is_rejected_without_touching_target(self):
        target = self.runtime / "target"
        target.write_bytes(b"keep")
        (self.runtime / f"g13-driver-{os.getuid()}.lock").symlink_to(target)
        with self.assertRaises(OSError):
            self.guard().start()
        self.assertEqual(target.read_bytes(), b"keep")

    def test_signal_handlers_restore_on_close(self):
        old = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        guard = self.guard()
        guard.start()
        guard.close()
        self.assertEqual(old, {sig: signal.getsignal(sig) for sig in old})

    def test_handlers_only_set_flag_and_keep_first_signal(self):
        guard = self.guard()
        guard.start()
        guard._request_stop(signal.SIGTERM, None)
        guard._request_stop(signal.SIGINT, None)
        self.assertTrue(guard.stop_requested)
        self.assertEqual(guard.stop_reason, "SIGTERM")
        with self.assertRaises(lifecycle_module.DriverAlreadyRunning):
            self.guard().start()

    def test_real_driver_prefix_rejects_duplicate_before_runtime(self):
        first = self.spawn('print("READY", flush=True)\nsys.stdin.readline()\n')
        self.wait_for(first, b"READY\n")
        second = self.spawn('raise AssertionError("Runtime imports would have executed")\n')
        out, err = second.communicate(timeout=5)
        self.assertEqual(second.returncode, 75, (out, err))
        self.assertIn(b"already running", out)
        self.assertNotIn(b"Runtime imports", out + err)
        self.assertIsNone(first.poll())

    def test_normal_process_exit_releases_lock(self):
        child = self.spawn('print("READY", flush=True)\nsys.stdin.readline()\n')
        self.wait_for(child, b"READY\n")
        child.communicate(input=b"done\n", timeout=5)
        self.assertEqual(child.returncode, 0)
        self.guard().start()

    def test_forced_child_exit_does_not_leave_stale_kernel_lock(self):
        child = self.spawn('print("READY", flush=True)\nsys.stdin.readline()\n')
        self.wait_for(child, b"READY\n")
        child.kill()
        child.communicate(timeout=5)
        self.guard().start()

    def exercise_real_signal(self, sig):
        child = self.spawn('''
print("READY", flush=True)
try:
    while not driver_lifecycle.stop_requested:
        time.sleep(0.01)
finally:
    print("CLEANUP " + driver_lifecycle.stop_reason, flush=True)
    sys.stdin.readline()
    print("DONE", flush=True)
''')
        self.wait_for(child, b"READY\n")
        child.send_signal(sig)
        self.wait_for(child, ("CLEANUP " + signal.Signals(sig).name + "\n").encode())
        # It must still own the lock throughout cleanup.
        with self.assertRaises(lifecycle_module.DriverAlreadyRunning):
            self.guard().start()
        child.send_signal(sig)  # Must not abort cleanup on a repeated stop request.
        out, err = child.communicate(input=b"finish\n", timeout=5)
        self.assertEqual(child.returncode, 0, (out, err))
        self.assertIn(b"DONE", out)
        self.guard().start()

    def test_real_sigterm_runs_cleanup_and_retains_lock_until_exit(self):
        self.exercise_real_signal(signal.SIGTERM)

    def test_real_sigint_runs_cleanup_and_retains_lock_until_exit(self):
        self.exercise_real_signal(signal.SIGINT)

    def test_startup_exception_releases_lock(self):
        child = self.spawn('raise RuntimeError("simulated startup failure")\n')
        out, err = child.communicate(timeout=5)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"simulated startup failure", err)
        self.guard().start()


class DriverMainTests(unittest.TestCase):
    def make_runtime(self, stop_at: str):
        guard = SimpleNamespace(stop_requested=(stop_at == "before_main"), stop_reason="SIGTERM")
        events = []

        def record(name):
            def run(*args, **kwargs):
                events.append(name)
                if name == stop_at:
                    guard.stop_requested = True
            return run

        dev = Mock()
        def read(*args, **kwargs):
            events.append("read")
            if stop_at == "read":
                guard.stop_requested = True
            return b"packet"
        dev.read.side_effect = read
        dev.close.side_effect = record("device_close")
        lcd = Mock()
        lcd.clear.side_effect = record("lcd_clear")
        joystick = Mock()
        joystick.stop.side_effect = record("joystick_stop")
        decoder = Mock()
        decoder.decode.side_effect = record("decode")
        lua = Mock()
        lua.deactivate_profile.side_effect = record("lua_stop")
        controller = Mock()
        controller.get_status.return_value = SimpleNamespace(active_profile="default.json")
        manager = Mock()
        manager.get_backlight_colors.return_value = {1: (1, 2, 3)}
        manager.get_joystick_settings.return_value = {}
        pointer = Mock()
        pointer.close.side_effect = record("pointer_close")
        commands = Mock()
        commands.close.side_effect = record("command_close")
        publisher = Mock()
        publisher.close.side_effect = record("event_close")
        ns = {
            "driver_lifecycle": guard, "G13_RESUME_GAP_SECONDS": 0.5,
            "_suspend_clock_offset": lambda: 0.0, "print": Mock(),
            "time": SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _: None),
            "open_g13": Mock(return_value=dev), "G13LCD": Mock(return_value=lcd),
            "LCDRuntimeUI": Mock(return_value=Mock()), "profile_manager": manager,
            "refresh_onboard_runtime_profiles": record("onboard_read"),
            "runtime_profile_controller": controller,
            "publish_runtime_profile_status": Mock(), "reload_lua_profile": Mock(),
            "event_publisher": publisher, "command_listener": commands,
            "get_layer": lambda: 1, "apply_backlight": Mock(), "M_LED_MASKS": {1: 1},
            "apply_indicator_leds": Mock(), "JoystickController": Mock(return_value=joystick),
            "G13Decoder": Mock(return_value=decoder), "handle_button": Mock(),
            "refresh_lcd_status": Mock(), "process_pending_lua_events": record("pending_lua"),
            "process_driver_commands": record("commands"), "lua_runtime": lua,
            "pressed_actions": {"G1": ({"name": "test"}, "M1:G1")},
            "pressed_button_layers": {"G1": 1}, "execute_action": record("action_release"),
            "stop_all_macros": Mock(side_effect=lambda: events.append("macros_stop") or 0),
            "release_all_keys": Mock(side_effect=lambda: events.append("keys_release") or []),
            "release_all_buttons": Mock(side_effect=lambda: events.append("buttons_release") or []),
            "set_g13_indicator_leds": record("leds_off"),
            "absolute_pointer_backend": pointer,
            "lua_lcd_messages": [], "led_test_until": 0.0, "lcd_test_until": 0.0,
            "profile_activation_lcd_profile": None, "runtime_profile_action_pending": False,
        }
        exec(MAIN_CODE, ns)
        ns["main"]()
        return ns, events

    def assert_general_cleanup(self, ns, events):
        for label in ("lua_stop", "action_release", "macros_stop", "keys_release",
                      "buttons_release", "pointer_close", "command_close", "event_close"):
            self.assertEqual(events.count(label), 1, (label, events))
        self.assertEqual(ns["pressed_actions"], {})
        self.assertEqual(ns["pressed_button_layers"], {})
        self.assertIsNone(ns["g13_device"])

    def test_stop_before_main_skips_physical_device_and_runs_cleanup(self):
        ns, events = self.make_runtime("before_main")
        ns["open_g13"].assert_not_called()
        self.assert_general_cleanup(ns, events)

    def test_stop_during_startup_read_does_not_activate_profile(self):
        ns, events = self.make_runtime("onboard_read")
        ns["reload_lua_profile"].assert_not_called()
        ns["JoystickController"].assert_not_called()
        self.assertIn("lcd_clear", events)
        self.assertIn("device_close", events)
        self.assert_general_cleanup(ns, events)

    def test_stop_after_pending_lua_skips_device_read(self):
        ns, events = self.make_runtime("pending_lua")
        self.assertNotIn("read", events)
        self.assertNotIn("commands", events)
        self.assert_general_cleanup(ns, events)

    def test_stop_after_input_read_skips_commands_and_decode(self):
        ns, events = self.make_runtime("read")
        self.assertNotIn("commands", events)
        self.assertNotIn("decode", events)
        for label in ("joystick_stop", "leds_off", "lcd_clear", "device_close"):
            self.assertEqual(events.count(label), 1)
        self.assert_general_cleanup(ns, events)

    def test_stop_after_command_skips_decode(self):
        ns, events = self.make_runtime("commands")
        self.assertNotIn("decode", events)
        self.assert_general_cleanup(ns, events)


def main() -> int:
    global PROJECT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=PROJECT)
    args = parser.parse_args()
    PROJECT = args.project.resolve()
    load_sources(PROJECT)
    suite = unittest.TestSuite()
    for cls in (LifecycleTests, DriverMainTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
