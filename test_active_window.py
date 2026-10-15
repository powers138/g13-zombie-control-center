from __future__ import annotations

import unittest
from unittest.mock import patch

import active_window


class X11ActiveWindowTests(unittest.TestCase):
    def test_x11_window_properties_are_parsed(self):
        outputs = {
            (
                "-root",
                "_NET_ACTIVE_WINDOW",
            ): (
                "_NET_ACTIVE_WINDOW(WINDOW): "
                "window id # 0x2a00007"
            ),
            (
                "-id",
                "0x2a00007",
                "WM_CLASS",
                "_NET_WM_NAME",
                "WM_NAME",
                "_NET_WM_PID",
            ): "\n".join(
                [
                    'WM_CLASS(STRING) = "Navigator", "firefox"',
                    (
                        '_NET_WM_NAME(UTF8_STRING) = '
                        '"Mozilla Firefox"'
                    ),
                    'WM_NAME(STRING) = "Firefox fallback"',
                    "_NET_WM_PID(CARDINAL) = 4321",
                ]
            ),
        }

        with patch.object(
            active_window,
            "_run_xprop",
            side_effect=lambda *args: outputs[args],
        ):
            window = active_window._get_active_window_x11()

        self.assertIsNotNone(window)
        self.assertEqual(window.window_id, "0x2a00007")
        self.assertEqual(window.window_class, "firefox")
        self.assertEqual(window.title, "Mozilla Firefox")
        self.assertEqual(window.pid, 4321)

    def test_xfce_x11_uses_x11_backend(self):
        expected = active_window.ActiveWindow(
            window_id="0x1",
            window_class="xfce-test",
            pid=123,
            title="XFCE Test",
        )

        environment = {
            "XDG_CURRENT_DESKTOP": "XFCE",
            "XDG_SESSION_TYPE": "x11",
        }

        with patch.dict(
            active_window.os.environ,
            environment,
            clear=True,
        ), patch.object(
            active_window,
            "_get_active_window_gamescope",
            return_value=None,
        ), patch.object(
            active_window,
            "_get_active_window_x11",
            return_value=expected,
        ) as x11_backend, patch.object(
            active_window,
            "_get_active_window_kde",
        ) as kde_backend:
            result = active_window.get_active_window()

        self.assertEqual(result, expected)
        x11_backend.assert_called_once_with()
        kde_backend.assert_not_called()

    def test_kde_keeps_kde_backend(self):
        expected = active_window.ActiveWindow(
            window_id="1",
            window_class="org.mozilla.firefox",
            pid=123,
            title="Firefox",
        )

        environment = {
            "XDG_CURRENT_DESKTOP": "KDE",
            "XDG_SESSION_TYPE": "wayland",
        }

        with patch.dict(
            active_window.os.environ,
            environment,
            clear=True,
        ), patch.object(
            active_window,
            "_get_active_window_gamescope",
            return_value=None,
        ), patch.object(
            active_window,
            "_get_active_window_kde",
            return_value=expected,
        ) as kde_backend, patch.object(
            active_window,
            "_get_active_window_x11",
        ) as x11_backend:
            result = active_window.get_active_window()

        self.assertEqual(result, expected)
        kde_backend.assert_called_once_with()
        x11_backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
