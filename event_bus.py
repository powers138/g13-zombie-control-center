from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any


def _event_socket_path() -> Path:
    runtime_directory = Path(
        os.environ.get(
            "XDG_RUNTIME_DIR",
            "/tmp",
        )
    )

    return (
        runtime_directory
        / f"g13-driver-events-{os.getuid()}.sock"
    )


EVENT_SOCKET_PATH = _event_socket_path()

def _command_socket_path() -> Path:
    runtime_directory = Path(
        os.environ.get(
            "XDG_RUNTIME_DIR",
            "/tmp",
        )
    )

    return (
        runtime_directory
        / f"g13-driver-commands-{os.getuid()}.sock"
    )


COMMAND_SOCKET_PATH = _command_socket_path()


class G13EventPublisher:
    """
    Sends driver events to the GUI when the GUI is running.

    Events are silently discarded when no editor is listening.
    """

    def __init__(self):
        self._socket = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )

    def publish_button(
        self,
        button: str,
        pressed: bool,
        layer: int,
    ) -> None:
        self._send(
            {
                "type": "button",
                "button": button,
                "pressed": pressed,
                "layer": layer,
            }
        )

    def publish_layer(
        self,
        layer: int,
    ) -> None:
        self._send(
            {
                "type": "layer",
                "layer": layer,
            }
        )

    def publish_runtime_profile(
        self,
        active_profile: str,
        source: str,
        locked_profile: str | None,
        window_class: str,
    ) -> None:
        self._send(
            {
                "type": "runtime_profile",
                "active_profile": active_profile,
                "source": source,
                "locked_profile": locked_profile,
                "window_class": window_class,
            }
        )

    def publish_onboard_profiles_updated(
        self,
    ) -> None:
        self._send(
            {
                "type": (
                    "onboard_profiles_updated"
                ),
            }
        )

    def publish_lcd_selection(
        self,
        selected_profile: str | None,
    ) -> None:
        self._send(
            {
                "type": "lcd_selection",
                "selected_profile": selected_profile,
            }
        )

    def _send(
        self,
        event: dict[str, Any],
    ) -> None:
        try:
            payload = json.dumps(
                event,
                separators=(",", ":"),
            ).encode("utf-8")

            self._socket.sendto(
                payload,
                str(EVENT_SOCKET_PATH),
            )

        except (
            FileNotFoundError,
            ConnectionRefusedError,
            OSError,
        ):
            # The editor is not running or its socket has
            # disappeared. Driver operation continues normally.
            return

    def close(self) -> None:
        self._socket.close()

class G13CommandPublisher:
    """
    Sends GUI commands to the running G13 driver.

    Commands are silently discarded when the driver is not
    running.
    """

    def __init__(self):
        self._socket = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )

    def publish_macro_recording(
        self,
        active: bool,
    ) -> None:
        self._send(
            {
                "type": "macro_recording",
                "active": active,
            }
        )

    def publish_request_onboard_profiles(
        self,
    ) -> None:
        self._send(
            {
                "type": (
                    "request_onboard_profiles"
                ),
            }
        )


    def publish_reload_onboard_profiles_cache(
        self,
    ) -> None:
        self._send(
            {
                "type": (
                    "reload_onboard_profiles_cache"
                ),
            }
        )

    def publish_lcd_test(
        self,
    ) -> None:
        self._send(
            {
                "type": "test_lcd",
            }
        )

    def publish_refresh_onboard_profiles(
        self,
    ) -> None:
        self._send(
            {
                "type": (
                    "refresh_onboard_profiles"
                ),
            }
        )

    def _send(
        self,
        command: dict[str, Any],
    ) -> None:
        try:
            payload = json.dumps(
                command,
                separators=(",", ":"),
            ).encode("utf-8")

            self._socket.sendto(
                payload,
                str(COMMAND_SOCKET_PATH),
            )

        except (
            FileNotFoundError,
            ConnectionRefusedError,
            OSError,
        ):
            return

    def publish_led_test(
        self,
    ) -> None:
        self._send(
            {
                "type": "test_leds",
            }
        )

    def close(self) -> None:
        self._socket.close()


class _UnixDatagramListener:
    def __init__(
        self,
        socket_path,
    ):
        self._socket_path = socket_path

        self._socket_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Remove a socket left behind by a process
        # that did not exit normally.
        self._socket_path.unlink(
            missing_ok=True
        )

        self._socket = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )

        self._socket.bind(
            str(
                self._socket_path
            )
        )

        self._socket.setblocking(False)

    def receive_all(
        self,
    ) -> list[dict[str, Any]]:
        messages: list[
            dict[str, Any]
        ] = []

        while True:
            try:
                payload = self._socket.recv(
                    65535
                )

            except BlockingIOError:
                break

            except OSError:
                break

            try:
                message = json.loads(
                    payload.decode("utf-8")
                )

            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
            ):
                continue

            if isinstance(message, dict):
                messages.append(
                    message
                )

        return messages

    def close(self) -> None:
        try:
            self._socket.close()

        finally:
            self._socket_path.unlink(
                missing_ok=True
            )


class G13EventListener(
    _UnixDatagramListener
):
    """
    Receives nonblocking driver events for the GUI.
    """

    def __init__(self):
        super().__init__(
            EVENT_SOCKET_PATH
        )


class G13CommandListener(
    _UnixDatagramListener
):
    """
    Receives nonblocking GUI commands for the G13 driver.
    """

    def __init__(self):
        super().__init__(
            COMMAND_SOCKET_PATH
        )
