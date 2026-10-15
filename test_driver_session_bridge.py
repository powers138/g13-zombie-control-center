from __future__ import annotations

from types import SimpleNamespace
import unittest

import driver_session_bridge as bridge


class FakeRunner:
    def __init__(self, states=()):
        self.states = list(states)
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))

        if arguments[:2] == ["loginctl", "show-session"]:
            if not self.states:
                return SimpleNamespace(returncode=1, stdout="")

            state = self.states.pop(0)

            if state is None:
                return SimpleNamespace(returncode=1, stdout="")

            return SimpleNamespace(returncode=0, stdout=f"{state}\n")

        return SimpleNamespace(returncode=0, stdout="")


class DriverSessionBridgeTests(unittest.TestCase):
    def xfce_environment(self):
        return {
            "HOME": "/home/tester",
            "XDG_SESSION_ID": "c7",
            "XDG_CURRENT_DESKTOP": "XFCE",
            "XDG_SESSION_TYPE": "x11",
            "DESKTOP_SESSION": "xfce",
            "DISPLAY": ":0",
            "XAUTHORITY": "/home/tester/.Xauthority",
        }

    def cinnamon_environment(self):
        return {
            "HOME": "/home/tester",
            "XDG_SESSION_ID": "c9",
            "XDG_CURRENT_DESKTOP": "X-Cinnamon",
            "XDG_SESSION_TYPE": "x11",
            "DESKTOP_SESSION": "lightdm-xsession",
            "DISPLAY": ":0",
            "XAUTHORITY": "/home/tester/.Xauthority",
        }

    def test_unsupported_session_does_nothing(self):
        runner = FakeRunner()

        result = bridge.run_bridge(
            {
                "XDG_CURRENT_DESKTOP": "KDE",
                "XDG_SESSION_TYPE": "wayland",
            },
            run=runner,
        )

        self.assertEqual(result, 0)
        self.assertEqual(runner.calls, [])

    def test_missing_session_id_refuses_start(self):
        runner = FakeRunner()

        environment = self.xfce_environment()
        del environment["XDG_SESSION_ID"]

        result = bridge.run_bridge(
            environment,
            run=runner,
        )

        self.assertEqual(result, 1)
        self.assertEqual(runner.calls, [])

    def test_xfce_session_starts_and_stops_driver(self):
        runner = FakeRunner(["active", "closing"])
        sleeps = []

        result = bridge.run_bridge(
            self.xfce_environment(),
            run=runner,
            sleep=sleeps.append,
        )

        self.assertEqual(result, 0)

        commands = [
            call[0]
            for call in runner.calls
        ]

        self.assertEqual(
            commands[0][:3],
            [
                "systemctl",
                "--user",
                "import-environment",
            ],
        )

        self.assertIn(
            "DISPLAY",
            commands[0],
        )
        self.assertIn(
            "XAUTHORITY",
            commands[0],
        )

        self.assertEqual(
            commands[1],
            [
                "systemctl",
                "--user",
                "start",
                bridge.UNIT,
            ],
        )

        self.assertEqual(
            commands[-1],
            [
                "systemctl",
                "--user",
                "stop",
                bridge.UNIT,
            ],
        )

        self.assertEqual(
            sleeps,
            [bridge.POLL_SECONDS],
        )

    def test_cinnamon_session_starts_and_stops_driver(self):
        runner = FakeRunner(["active", "closing"])
        sleeps = []

        result = bridge.run_bridge(
            self.cinnamon_environment(),
            run=runner,
            sleep=sleeps.append,
        )

        self.assertEqual(result, 0)

        commands = [
            call[0]
            for call in runner.calls
        ]

        self.assertEqual(
            commands[1],
            [
                "systemctl",
                "--user",
                "start",
                bridge.UNIT,
            ],
        )

        self.assertEqual(
            commands[-1],
            [
                "systemctl",
                "--user",
                "stop",
                bridge.UNIT,
            ],
        )

        self.assertEqual(
            sleeps,
            [bridge.POLL_SECONDS],
        )

    def test_disappearing_session_stops_driver(self):
        runner = FakeRunner([None])

        result = bridge.run_bridge(
            self.xfce_environment(),
            run=runner,
            sleep=lambda _: None,
        )

        self.assertEqual(result, 0)

        commands = [
            call[0]
            for call in runner.calls
        ]

        self.assertEqual(
            commands[-1],
            [
                "systemctl",
                "--user",
                "stop",
                bridge.UNIT,
            ],
        )


if __name__ == "__main__":
    unittest.main()
