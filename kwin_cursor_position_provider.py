from __future__ import annotations

from pathlib import Path
import secrets
import threading

from gi.repository import Gio, GLib

from cursor_position_provider import (
    CursorPositionProvider,
    CursorPositionUnavailable,
)


KWIN_NAME = "org.kde.KWin"

KWIN_SCRIPTING_PATH = (
    "/Scripting"
)

KWIN_SCRIPTING_INTERFACE = (
    "org.kde.kwin.Scripting"
)

KWIN_SCRIPT_INTERFACE = (
    "org.kde.kwin.Script"
)


class KWinCursorPositionProvider(
    CursorPositionProvider
):
    """
    Global Wayland cursor-position provider for KDE Plasma.

    A small temporary KWin JavaScript bridge watches
    workspace.cursorPosChanged and reports the compositor's real
    global cursor coordinates back to this Python process over the
    session D-Bus.

    The script exists only while this provider is alive.
    """

    name = "kwin"

    def __init__(
        self,
    ) -> None:
        self._bus = Gio.bus_get_sync(
            Gio.BusType.SESSION,
            None,
        )

        if self._bus is None:
            raise CursorPositionUnavailable(
                "Could not connect to "
                "the session D-Bus."
            )

        unique_suffix = (
            secrets.token_hex(6)
        )

        self._bus_name = (
            "org.g13.CursorPosition."
            f"p{unique_suffix}"
        )

        self._object_path = (
            "/org/g13/CursorPosition"
        )

        self._interface_name = (
            "org.g13.CursorPosition"
        )

        self._plugin_name = (
            "g13_cursor_position_"
            f"{unique_suffix}"
        )

        self._script_path = Path(
            "/tmp/"
            f"{self._plugin_name}.js"
        )

        self._position: (
            tuple[int, int] | None
        ) = None

        self._position_lock = (
            threading.Lock()
        )

        self._initial_position = (
            threading.Event()
        )

        self._main_loop = (
            GLib.MainLoop()
        )

        self._main_loop_thread: (
            threading.Thread | None
        ) = None

        self._registration_id = 0
        self._script_id: int | None = None

        self._closed = False

        self._register_dbus_service()
        self._start_main_loop()
        self._load_kwin_script()

        if not self._initial_position.wait(
            timeout=2.0
        ):
            self.close()

            raise CursorPositionUnavailable(
                "KWin did not report an "
                "initial cursor position."
            )

    def _register_dbus_service(
        self,
    ) -> None:
        introspection_xml = f"""
        <node>
          <interface name="{self._interface_name}">
            <method name="ReportCursor">
              <arg
                name="x"
                type="s"
                direction="in"
              />
              <arg
                name="y"
                type="s"
                direction="in"
              />
            </method>
          </interface>
        </node>
        """

        node_info = (
            Gio.DBusNodeInfo.new_for_xml(
                introspection_xml
            )
        )

        interface_info = (
            node_info.interfaces[0]
        )

        def method_called(
            _connection,
            _sender,
            _object_path,
            _interface_name,
            method_name,
            parameters,
            invocation,
        ):
            if method_name != "ReportCursor":
                invocation.return_dbus_error(
                    (
                        "org.g13.CursorPosition."
                        "UnknownMethod"
                    ),
                    "Unknown method.",
                )

                return

            x_text, y_text = (
                parameters.unpack()
            )

            try:
                x = int(
                    float(
                        x_text
                    )
                )

                y = int(
                    float(
                        y_text
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                invocation.return_dbus_error(
                    (
                        "org.g13.CursorPosition."
                        "BadCoordinates"
                    ),
                    "Invalid cursor coordinates.",
                )

                return

            with self._position_lock:
                self._position = (
                    x,
                    y,
                )

            self._initial_position.set()

            invocation.return_value(
                GLib.Variant(
                    "()",
                    (),
                )
            )

        self._registration_id = (
            self._bus.register_object(
                self._object_path,
                interface_info,
                method_called,
                None,
                None,
            )
        )

        if not self._registration_id:
            raise CursorPositionUnavailable(
                "Could not register the "
                "cursor-position D-Bus object."
            )

        reply = self._bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "RequestName",
            GLib.Variant(
                "(su)",
                (
                    self._bus_name,
                    0,
                ),
            ),
            GLib.VariantType(
                "(u)"
            ),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

        result = (
            reply.unpack()[0]
        )

        if result not in {
            1,
            4,
        }:
            raise CursorPositionUnavailable(
                "Could not own D-Bus name "
                f"{self._bus_name}."
            )

    def _start_main_loop(
        self,
    ) -> None:
        self._main_loop_thread = (
            threading.Thread(
                target=self._main_loop.run,
                name=(
                    "G13 KWin cursor bridge"
                ),
                daemon=True,
            )
        )

        self._main_loop_thread.start()

    def _script_source(
        self,
    ) -> str:
        return f"""
        function reportCursor() {{
            var point = workspace.cursorPos;

            callDBus(
                "{self._bus_name}",
                "{self._object_path}",
                "{self._interface_name}",
                "ReportCursor",
                String(point.x),
                String(point.y)
            );
        }}

        reportCursor();

        workspace.cursorPosChanged.connect(
            reportCursor
        );
        """

    def _load_kwin_script(
        self,
    ) -> None:
        self._script_path.write_text(
            self._script_source(),
            encoding="utf-8",
        )

        # Clean up a stale script with the same plugin name.
        try:
            self._bus.call_sync(
                KWIN_NAME,
                KWIN_SCRIPTING_PATH,
                KWIN_SCRIPTING_INTERFACE,
                "unloadScript",
                GLib.Variant(
                    "(s)",
                    (
                        self._plugin_name,
                    ),
                ),
                GLib.VariantType(
                    "(b)"
                ),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )

        except Exception:
            pass

        reply = self._bus.call_sync(
            KWIN_NAME,
            KWIN_SCRIPTING_PATH,
            KWIN_SCRIPTING_INTERFACE,
            "loadScript",
            GLib.Variant(
                "(ss)",
                (
                    str(
                        self._script_path
                    ),
                    self._plugin_name,
                ),
            ),
            GLib.VariantType(
                "(i)"
            ),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

        self._script_id = (
            int(
                reply.unpack()[0]
            )
        )

        script_object_path = (
            "/Scripting/"
            f"Script{self._script_id}"
        )

        self._bus.call_sync(
            KWIN_NAME,
            script_object_path,
            KWIN_SCRIPT_INTERFACE,
            "run",
            None,
            GLib.VariantType(
                "()"
            ),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

    def get_position(
        self,
    ) -> tuple[int, int]:
        with self._position_lock:
            position = self._position

        if position is None:
            raise CursorPositionUnavailable(
                "KWin has not reported "
                "a cursor position yet."
            )

        return position

    def close(
        self,
    ) -> None:
        if self._closed:
            return

        self._closed = True

        if self._script_id is not None:
            try:
                self._bus.call_sync(
                    KWIN_NAME,
                    KWIN_SCRIPTING_PATH,
                    KWIN_SCRIPTING_INTERFACE,
                    "unloadScript",
                    GLib.Variant(
                        "(s)",
                        (
                            self._plugin_name,
                        ),
                    ),
                    GLib.VariantType(
                        "(b)"
                    ),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                )

            except Exception:
                pass

            self._script_id = None

        if self._registration_id:
            try:
                self._bus.unregister_object(
                    self._registration_id
                )

            except Exception:
                pass

            self._registration_id = 0

        try:
            self._bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ReleaseName",
                GLib.Variant(
                    "(s)",
                    (
                        self._bus_name,
                    ),
                ),
                GLib.VariantType(
                    "(u)"
                ),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )

        except Exception:
            pass

        try:
            self._main_loop.quit()

        except Exception:
            pass

        if (
            self._main_loop_thread
            is not None
            and self._main_loop_thread.is_alive()
        ):
            self._main_loop_thread.join(
                timeout=1.0
            )

        self._main_loop_thread = None

        self._script_path.unlink(
            missing_ok=True
        )
