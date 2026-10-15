from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


keyboard = types.ModuleType("keyboard")
keyboard.send_chord = Mock()

macro_engine = types.ModuleType("macro_engine")
macro_engine.handle_macro_event = Mock()

mouse = types.ModuleType("mouse")
mouse.scroll_horizontal = Mock()
mouse.scroll_vertical = Mock()
mouse.send_button = Mock()


with patch.dict(
    sys.modules,
    {
        "keyboard": keyboard,
        "macro_engine": macro_engine,
        "mouse": mouse,
    },
):
    sys.modules.pop(
        "dispatcher",
        None,
    )
    dispatcher = importlib.import_module(
        "dispatcher"
    )


class FakeThread:
    instances = []

    def __init__(
        self,
        target,
        args,
        daemon,
    ):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

        self.instances.append(
            self
        )

    def start(self):
        self.started = True


class ControlledEvent:
    def __init__(
        self,
        wait_results,
    ):
        self.wait_results = iter(
            wait_results
        )
        self.wait_calls = []

    def wait(
        self,
        timeout,
    ):
        self.wait_calls.append(
            timeout
        )

        return next(
            self.wait_results
        )

    def is_set(self):
        return False


class DispatcherWheelTests(unittest.TestCase):
    def setUp(self):
        mouse.scroll_horizontal.reset_mock()
        mouse.scroll_vertical.reset_mock()

        FakeThread.instances.clear()

        dispatcher._wheel_repeat_stops.clear()

    def tearDown(self):
        for stop_event in (
            dispatcher._wheel_repeat_stops.values()
        ):
            stop_event.set()

        dispatcher._wheel_repeat_stops.clear()

    def test_press_sends_immediate_wheel_and_starts_repeat(self):
        action = {
            "type": "wheel",
            "name": "Scroll Left",
            "direction": "horizontal",
            "amount": -1,
        }

        with patch.object(
            dispatcher.threading,
            "Thread",
            FakeThread,
        ):
            dispatcher.execute_action(
                action,
                True,
                source_id="G10",
            )

        mouse.scroll_horizontal.assert_called_once_with(
            -1
        )

        self.assertIn(
            "G10",
            dispatcher._wheel_repeat_stops,
        )

        self.assertEqual(
            len(FakeThread.instances),
            1,
        )

        thread = FakeThread.instances[0]

        self.assertTrue(
            thread.started
        )
        self.assertTrue(
            thread.daemon
        )
        self.assertIs(
            thread.target,
            dispatcher._run_wheel_repeat,
        )
        self.assertIs(
            thread.args[1],
            dispatcher._wheel_repeat_stops[
                "G10"
            ],
        )

    def test_release_stops_repeat_for_source(self):
        action = {
            "type": "wheel",
            "name": "Scroll Right",
            "direction": "horizontal",
            "amount": 1,
        }

        with patch.object(
            dispatcher.threading,
            "Thread",
            FakeThread,
        ):
            dispatcher.execute_action(
                action,
                True,
                source_id="G11",
            )

            stop_event = (
                dispatcher._wheel_repeat_stops[
                    "G11"
                ]
            )

            self.assertFalse(
                stop_event.is_set()
            )

            dispatcher.execute_action(
                action,
                False,
                source_id="G11",
            )

        self.assertTrue(
            stop_event.is_set()
        )
        self.assertNotIn(
            "G11",
            dispatcher._wheel_repeat_stops,
        )

        mouse.scroll_horizontal.assert_called_once_with(
            1
        )

    def test_repeat_worker_repeats_without_initial_delay(self):
        action = {
            "type": "wheel",
            "name": "Scroll Right",
            "direction": "horizontal",
            "amount": 1,
        }

        stop_event = ControlledEvent(
            [
                False,
                True,
            ]
        )

        dispatcher._run_wheel_repeat(
            action,
            stop_event,
        )

        mouse.scroll_horizontal.assert_called_once_with(
            1
        )

        self.assertEqual(
            stop_event.wait_calls,
            [
                0.0,
                dispatcher.WHEEL_REPEAT_INTERVAL,
            ],
        )

    def test_sources_repeat_independently(self):
        left_action = {
            "type": "wheel",
            "name": "Scroll Left",
            "direction": "horizontal",
            "amount": -1,
        }

        right_action = {
            "type": "wheel",
            "name": "Scroll Right",
            "direction": "horizontal",
            "amount": 1,
        }

        with patch.object(
            dispatcher.threading,
            "Thread",
            FakeThread,
        ):
            dispatcher.execute_action(
                left_action,
                True,
                source_id="G10",
            )

            dispatcher.execute_action(
                right_action,
                True,
                source_id="G11",
            )

            left_stop = (
                dispatcher._wheel_repeat_stops[
                    "G10"
                ]
            )
            right_stop = (
                dispatcher._wheel_repeat_stops[
                    "G11"
                ]
            )

            dispatcher.execute_action(
                left_action,
                False,
                source_id="G10",
            )

        self.assertTrue(
            left_stop.is_set()
        )
        self.assertFalse(
            right_stop.is_set()
        )

        self.assertNotIn(
            "G10",
            dispatcher._wheel_repeat_stops,
        )
        self.assertIn(
            "G11",
            dispatcher._wheel_repeat_stops,
        )

    def test_missing_source_id_remains_one_shot(self):
        action = {
            "type": "wheel",
            "name": "Scroll Up",
            "direction": "vertical",
            "amount": 1,
        }

        with patch.object(
            dispatcher.threading,
            "Thread",
            FakeThread,
        ):
            dispatcher.execute_action(
                action,
                True,
            )

            dispatcher.execute_action(
                action,
                False,
            )

        mouse.scroll_vertical.assert_called_once_with(
            1
        )

        self.assertEqual(
            FakeThread.instances,
            [],
        )
        self.assertEqual(
            dispatcher._wheel_repeat_stops,
            {},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
