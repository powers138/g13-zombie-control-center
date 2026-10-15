"""Regression tests for HID-read errors during driver shutdown.

Run beside g13-test.py:
    python3 test_driver_hid_shutdown.py

This script parses the driver's source and executes ONLY main() in a mocked
namespace. It does not import the driver, hid, decoder, evdev, or other hardware
modules. No real devices, sockets, settings, profiles, or locks are opened.
HIDException and stop requests are simulated; this is not a hardware test.

Optional development argument: --project /path/to/source/copies
"""
from __future__ import annotations

import argparse
import ast
from errno import EINTR
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

MAIN_CODE = None


class SimulatedHIDException(Exception):
    """Same exception interface as the user's hid.HIDException."""


DRIVER_FUNCTIONS = {
    "_suspend_clock_offset",
    "_close_g13_device",
    "_release_inputs_for_reconnect",
    "_reconnect_g13_hardware",
    "main",
}


def load_main(project: Path) -> None:
    global MAIN_CODE

    path = project / "g13-test.py"
    tree = ast.parse(
        path.read_text(encoding="utf-8"),
        str(path),
    )

    candidates = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in DRIVER_FUNCTIONS
        )
    ]

    found = {
        node.name
        for node in candidates
    }

    if found != DRIVER_FUNCTIONS:
        missing = sorted(
            DRIVER_FUNCTIONS - found
        )
        raise RuntimeError(
            f"Missing driver functions: {missing}"
        )

    MAIN_CODE = compile(
        ast.Module(
            body=candidates,
            type_ignores=[],
        ),
        str(path),
        "exec",
    )


class HIDShutdownTests(unittest.TestCase):
    def runtime(
        self,
        *,
        error=None,
        request_stop=False,
        reason="SIGTERM",
        packet=b"packet",
        suspend_offsets=None,
    ):
        guard = SimpleNamespace(stop_requested=False, stop_reason=reason)
        events = []
        turns = 0

        offset_values = list(
            suspend_offsets
            if suspend_offsets is not None
            else [0.0]
        )

        def suspend_offset():
            if len(offset_values) > 1:
                return offset_values.pop(0)

            return offset_values[0]

        def record(name):
            def run(*args, **kwargs):
                events.append(name)
            return run

        def pending():
            nonlocal turns
            turns += 1
            # Stop after one full iteration for successful/empty reads.
            # Also prevent a faulty retry loop from hanging a regression test.
            if turns > 1:
                guard.stop_requested = True

        def read(*args, **kwargs):
            events.append("read")
            if request_stop:
                guard.stop_requested = True
            if error is not None:
                raise error
            return packet

        dev = Mock()
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
        manager = Mock()
        manager.get_backlight_colors.return_value = {1: (1, 2, 3)}
        manager.get_joystick_settings.return_value = {}
        controller = Mock()
        controller.get_status.return_value = SimpleNamespace(active_profile="default.json")
        pointer = Mock()
        pointer.close.side_effect = record("pointer_close")
        commands = Mock()
        commands.close.side_effect = record("command_close")
        publisher = Mock()
        publisher.close.side_effect = record("event_close")

        ns = {
            "HIDException": SimulatedHIDException, "EINTR": EINTR, "os": os,
            "G13_RESUME_GAP_SECONDS": 0.5,
            "driver_lifecycle": guard, "print": Mock(),
            "time": SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _: None),
            "open_g13": Mock(return_value=dev), "G13LCD": Mock(return_value=lcd),
            "LCDRuntimeUI": Mock(return_value=Mock()), "profile_manager": manager,
            "refresh_onboard_runtime_profiles": Mock(),
            "runtime_profile_controller": controller,
            "publish_runtime_profile_status": Mock(), "reload_lua_profile": Mock(),
            "event_publisher": publisher, "command_listener": commands,
            "get_layer": lambda: 1, "apply_backlight": Mock(), "M_LED_MASKS": {1: 1},
            "apply_indicator_leds": Mock(), "JoystickController": Mock(return_value=joystick),
            "G13Decoder": Mock(return_value=decoder), "handle_button": Mock(),
            "refresh_lcd_status": Mock(), "process_pending_lua_events": pending,
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

        reconnect = Mock(
            return_value=(
                dev,
                lcd,
                joystick,
                decoder,
            )
        )

        ns["_suspend_clock_offset"] = (
            suspend_offset
        )
        ns["_reconnect_g13_hardware"] = (
            reconnect
        )
        ns["_reconnect_mock"] = reconnect

        return ns, events, dev, decoder

    def assert_cleaned(self, ns, events):
        for name in (
            "lua_stop", "action_release", "macros_stop", "keys_release",
            "buttons_release", "joystick_stop", "leds_off", "lcd_clear",
            "device_close", "pointer_close", "command_close", "event_close",
        ):
            self.assertEqual(events.count(name), 1, (name, events))
        self.assertEqual(ns["pressed_actions"], {})
        self.assertEqual(ns["pressed_button_layers"], {})
        self.assertIsNone(ns["g13_device"])
        self.assertLess(events.index("lcd_clear"), events.index("device_close"))

    def stopped_interruption(self, reason):
        ns, events, dev, decoder = self.runtime(
            error=SimulatedHIDException(os.strerror(EINTR)),
            request_stop=True, reason=reason,
        )
        self.assertIsNone(ns["main"]())
        dev.read.assert_called_once_with(8, timeout=50)
        self.assertNotIn("commands", events)
        decoder.decode.assert_not_called()
        self.assert_cleaned(ns, events)
        ns["print"].assert_any_call(f"\nStopping G13 driver ({reason})...")

    def assert_runtime_hid_error_reconnects(
        self,
        error,
    ):
        ns, events, dev, decoder = (
            self.runtime(
                error=error
            )
        )

        ns["main"]()

        dev.read.assert_called_once_with(
            8,
            timeout=50,
        )

        ns[
            "_reconnect_mock"
        ].assert_called_once()

        decoder.decode.assert_not_called()

        self.assert_cleaned(
            ns,
            events,
        )

    def test_sigterm_interrupted_read_exits_cleanly_without_retry(self):
        self.stopped_interruption("SIGTERM")

    def test_sigint_interrupted_read_exits_cleanly_without_retry(self):
        self.stopped_interruption("SIGINT")

    def assert_error_propagates(self, error, *, request_stop):
        ns, events, dev, decoder = self.runtime(error=error, request_stop=request_stop)
        with self.assertRaises(type(error)) as caught:
            ns["main"]()
        self.assertIs(caught.exception, error)
        dev.read.assert_called_once_with(8, timeout=50)
        self.assertNotIn("commands", events)
        decoder.decode.assert_not_called()
        self.assert_cleaned(ns, events)

    def test_interruption_without_stop_request_reconnects(self):
        self.assert_runtime_hid_error_reconnects(
            SimulatedHIDException(
                os.strerror(EINTR)
            )
        )

    def test_other_hid_error_during_shutdown_is_not_hidden(self):
        self.assert_error_propagates(
            SimulatedHIDException("No such device"), request_stop=True
        )

    def test_other_hid_error_while_running_reconnects(self):
        self.assert_runtime_hid_error_reconnects(
            SimulatedHIDException(
                "Input/output error"
            )
        )

    def test_unrelated_exception_with_same_message_is_not_hidden(self):
        self.assert_error_propagates(RuntimeError(os.strerror(EINTR)), request_stop=True)

    def test_suspend_gap_reconnects_before_stale_hid_read(self):
        ns, events, dev, decoder = self.runtime(
            suspend_offsets=[
                0.0,
                1.0,
            ]
        )

        ns["main"]()

        dev.read.assert_not_called()

        ns[
            "_reconnect_mock"
        ].assert_called_once()

        decoder.decode.assert_not_called()

        self.assert_cleaned(
            ns,
            events,
        )

    def test_reconnect_helper_refreshes_onboard_profiles(self):
        path = Path(__file__).resolve().with_name(
            "g13-test.py"
        )

        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            str(path),
        )

        reconnect = next(
            node
            for node in tree.body
            if (
                isinstance(node, ast.FunctionDef)
                and node.name
                == "_reconnect_g13_hardware"
            )
        )

        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(
                    node.func,
                    ast.Name,
                )
                and node.func.id
                == "refresh_onboard_runtime_profiles"
                for node in ast.walk(reconnect)
            )
        )

        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(
                    node.func,
                    ast.Name,
                )
                and node.func.id
                == "publish_runtime_profile_status"
                for node in ast.walk(reconnect)
            )
        )

        self.assertTrue(
            any(
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name)
                    and target.id
                    == "runtime_profile_action_pending"
                    for target in node.targets
                )
                and isinstance(
                    node.value,
                    ast.Constant,
                )
                and node.value.value is True
                for node in ast.walk(reconnect)
            )
        )

    def test_successful_packet_keeps_normal_command_and_decode_path(self):
        ns, events, dev, decoder = self.runtime()
        ns["main"]()
        dev.read.assert_called_once_with(8, timeout=50)
        decoder.decode.assert_called_once_with(b"packet")
        self.assertEqual(events.count("commands"), 1)
        self.assertLess(events.index("commands"), events.index("decode"))
        self.assert_cleaned(ns, events)

    def test_empty_timeout_keeps_command_path_without_decode(self):
        ns, events, dev, decoder = self.runtime(packet=b"")
        ns["main"]()
        dev.read.assert_called_once_with(8, timeout=50)
        decoder.decode.assert_not_called()
        self.assertEqual(events.count("commands"), 1)
        self.assert_cleaned(ns, events)

    def test_stop_after_successful_read_still_skips_commands_and_decode(self):
        ns, events, dev, decoder = self.runtime(request_stop=True)
        ns["main"]()
        dev.read.assert_called_once_with(8, timeout=50)
        decoder.decode.assert_not_called()
        self.assertNotIn("commands", events)
        self.assert_cleaned(ns, events)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    load_main(args.project.resolve())
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HIDShutdownTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
