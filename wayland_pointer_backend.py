from __future__ import annotations

from dataclasses import dataclass
import secrets

from gi.repository import Gio, GLib

from pointer_backend import (
    PointerBackend,
    PointerBackendUnavailable,
    PointerGeometry,
)


PORTAL_NAME = (
    "org.freedesktop.portal.Desktop"
)

PORTAL_PATH = (
    "/org/freedesktop/portal/desktop"
)

REQUEST_INTERFACE = (
    "org.freedesktop.portal.Request"
)

SESSION_INTERFACE = (
    "org.freedesktop.portal.Session"
)

REMOTE_DESKTOP_INTERFACE = (
    "org.freedesktop.portal.RemoteDesktop"
)

SCREENCAST_INTERFACE = (
    "org.freedesktop.portal.ScreenCast"
)


class PortalRequestError(
    PointerBackendUnavailable
):
    pass


@dataclass(
    frozen=True,
)
class WaylandPointerStream:
    node_id: int
    geometry: PointerGeometry


def _token(
    purpose: str,
) -> str:
    return (
        f"g13_{purpose}_"
        f"{secrets.token_hex(8)}"
    )


def _plain(
    value,
):
    if isinstance(
        value,
        GLib.Variant,
    ):
        return _plain(
            value.unpack()
        )

    if isinstance(
        value,
        dict,
    ):
        return {
            key: _plain(item)
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        tuple,
    ):
        return tuple(
            _plain(item)
            for item in value
        )

    if isinstance(
        value,
        list,
    ):
        return [
            _plain(item)
            for item in value
        ]

    return value


class WaylandPointerBackend(
    PointerBackend
):
    """
    Absolute-pointer backend using XDG Desktop Portal.

    The RemoteDesktop portal supplies authorized absolute pointer
    motion. ScreenCast monitor streams provide the logical
    coordinate regions required by that API.

    Portal authorization is lazy: merely starting the G13 driver
    does not display a permission dialog. The dialog appears only
    when an absolute pointer operation is actually requested.

    The authorized portal session is retained for the lifetime of
    this backend.
    """

    name = "wayland-portal"

    def __init__(
        self,
    ) -> None:
        try:
            self._bus = (
                Gio.bus_get_sync(
                    Gio.BusType.SESSION,
                    None,
                )
            )

        except Exception as error:
            raise PointerBackendUnavailable(
                "Could not connect to the "
                f"session D-Bus: {error}"
            ) from error

        if self._bus is None:
            raise PointerBackendUnavailable(
                "Could not connect to the "
                "session D-Bus."
            )

        self._session_handle: (
            str | None
        ) = None

        self._streams: list[
            WaylandPointerStream
        ] = []

        self._authorized = False

        self._cursor_position_provider = None

        # Created lazily only if primary-screen geometry is
        # requested. Keep a reference so Qt does not destroy the
        # application object after the query.
        self._qt_app = None

    def _request_path(
        self,
        token: str,
    ) -> str:
        unique_name = (
            self._bus.get_unique_name()
        )

        if not unique_name:
            raise PortalRequestError(
                "Session bus has no unique name."
            )

        sender = (
            unique_name[1:]
            .replace(
                ".",
                "_",
            )
        )

        return (
            "/org/freedesktop/portal/"
            f"desktop/request/{sender}/{token}"
        )

    def _portal_request(
        self,
        interface: str,
        method: str,
        parameters: GLib.Variant,
        token: str,
    ) -> dict:
        expected_path = (
            self._request_path(
                token
            )
        )

        state = {}
        loop = GLib.MainLoop()

        def response_received(
            _connection,
            _sender_name,
            _object_path,
            _interface_name,
            _signal_name,
            signal_parameters,
            _user_data,
        ):
            response, results = (
                signal_parameters.unpack()
            )

            state["response"] = int(
                response
            )

            state["results"] = (
                _plain(results)
            )

            loop.quit()

        subscription_id = (
            self._bus.signal_subscribe(
                PORTAL_NAME,
                REQUEST_INTERFACE,
                "Response",
                expected_path,
                None,
                Gio.DBusSignalFlags.NONE,
                response_received,
                None,
            )
        )

        try:
            reply = self._bus.call_sync(
                PORTAL_NAME,
                PORTAL_PATH,
                interface,
                method,
                parameters,
                GLib.VariantType(
                    "(o)"
                ),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )

            returned_path = (
                reply.unpack()[0]
            )

            if (
                returned_path
                != expected_path
            ):
                raise PortalRequestError(
                    f"{method} returned "
                    "an unexpected request path."
                )

            loop.run()

        finally:
            self._bus.signal_unsubscribe(
                subscription_id
            )

        response = state.get(
            "response",
            2,
        )

        if response == 1:
            raise PortalRequestError(
                f"{method} was cancelled."
            )

        if response != 0:
            raise PortalRequestError(
                f"{method} failed with "
                f"portal response {response}."
            )

        return state.get(
            "results",
            {},
        )

    def _ensure_session(
        self,
    ) -> None:
        if self._authorized:
            return

        self._create_session()

    def _create_session(
        self,
    ) -> None:
        self._close_portal_session()

        create_token = _token(
            "create"
        )

        session_token = _token(
            "session"
        )

        create_results = (
            self._portal_request(
                REMOTE_DESKTOP_INTERFACE,
                "CreateSession",
                GLib.Variant(
                    "(a{sv})",
                    (
                        {
                            "handle_token": (
                                GLib.Variant(
                                    "s",
                                    create_token,
                                )
                            ),
                            "session_handle_token": (
                                GLib.Variant(
                                    "s",
                                    session_token,
                                )
                            ),
                        },
                    ),
                ),
                create_token,
            )
        )

        session_handle = (
            create_results.get(
                "session_handle"
            )
        )

        if not isinstance(
            session_handle,
            str,
        ):
            raise PortalRequestError(
                "Portal did not return "
                "a session handle."
            )

        self._session_handle = (
            session_handle
        )

        try:
            self._select_pointer()
            self._select_monitors()

            start_results = (
                self._start_session()
            )

            granted_devices = int(
                start_results.get(
                    "devices",
                    0,
                )
            )

            if not (
                granted_devices
                & 2
            ):
                raise PortalRequestError(
                    "Pointer control was not "
                    "granted by the portal."
                )

            self._streams = (
                self._parse_streams(
                    start_results.get(
                        "streams",
                        [],
                    )
                )
            )

            if not self._streams:
                raise PortalRequestError(
                    "Portal returned no usable "
                    "monitor streams."
                )

            self._authorized = True

        except Exception:
            self.close()
            raise

    def _select_pointer(
        self,
    ) -> None:
        if self._session_handle is None:
            raise PortalRequestError(
                "No portal session exists."
            )

        token = _token(
            "devices"
        )

        self._portal_request(
            REMOTE_DESKTOP_INTERFACE,
            "SelectDevices",
            GLib.Variant(
                "(oa{sv})",
                (
                    self._session_handle,
                    {
                        "handle_token": (
                            GLib.Variant(
                                "s",
                                token,
                            )
                        ),
                        "types": GLib.Variant(
                            "u",
                            2,
                        ),
                    },
                ),
            ),
            token,
        )

    def _select_monitors(
        self,
    ) -> None:
        if self._session_handle is None:
            raise PortalRequestError(
                "No portal session exists."
            )

        token = _token(
            "sources"
        )

        self._portal_request(
            SCREENCAST_INTERFACE,
            "SelectSources",
            GLib.Variant(
                "(oa{sv})",
                (
                    self._session_handle,
                    {
                        "handle_token": (
                            GLib.Variant(
                                "s",
                                token,
                            )
                        ),
                        "types": GLib.Variant(
                            "u",
                            1,
                        ),
                        "multiple": GLib.Variant(
                            "b",
                            True,
                        ),
                    },
                ),
            ),
            token,
        )

    def _start_session(
        self,
    ) -> dict:
        if self._session_handle is None:
            raise PortalRequestError(
                "No portal session exists."
            )

        token = _token(
            "start"
        )

        return self._portal_request(
            REMOTE_DESKTOP_INTERFACE,
            "Start",
            GLib.Variant(
                "(osa{sv})",
                (
                    self._session_handle,
                    "",
                    {
                        "handle_token": (
                            GLib.Variant(
                                "s",
                                token,
                            )
                        ),
                    },
                ),
            ),
            token,
        )

    def _parse_streams(
        self,
        streams,
    ) -> list[
        WaylandPointerStream
    ]:
        parsed: list[
            WaylandPointerStream
        ] = []

        for stream in streams:
            try:
                node_id, properties = (
                    stream
                )

                position = (
                    properties.get(
                        "position"
                    )
                )

                size = (
                    properties.get(
                        "size"
                    )
                )

                if (
                    not isinstance(
                        position,
                        tuple,
                    )
                    or len(position) != 2
                    or not isinstance(
                        size,
                        tuple,
                    )
                    or len(size) != 2
                ):
                    continue

                x = int(
                    position[0]
                )

                y = int(
                    position[1]
                )

                width = int(
                    size[0]
                )

                height = int(
                    size[1]
                )

                if (
                    width <= 0
                    or height <= 0
                ):
                    continue

                parsed.append(
                    WaylandPointerStream(
                        node_id=int(
                            node_id
                        ),
                        geometry=(
                            PointerGeometry(
                                x=x,
                                y=y,
                                width=width,
                                height=height,
                            )
                        ),
                    )
                )

            except Exception:
                continue

        return parsed

    def primary_geometry(
        self,
    ) -> PointerGeometry:
        """
        Return the compositor's primary logical screen.

        XDG Desktop Portal provides the coordinate space used for
        absolute movement but does not identify which monitor is
        primary. Qt obtains that information from the Wayland
        compositor without tying this backend to KDE or GNOME.
        """
        try:
            from PySide6.QtGui import (
                QGuiApplication,
            )

        except ImportError as error:
            raise PointerBackendUnavailable(
                "Primary-screen geometry "
                "requires PySide6."
            ) from error

        app = (
            QGuiApplication.instance()
        )

        if app is None:
            try:
                app = QGuiApplication(
                    []
                )

            except Exception as error:
                raise PointerBackendUnavailable(
                    "Could not initialize Qt "
                    "for primary-screen geometry: "
                    f"{error}"
                ) from error

            self._qt_app = app

        screen = (
            QGuiApplication.primaryScreen()
        )

        if screen is None:
            raise PointerBackendUnavailable(
                "Wayland compositor did not "
                "report a primary screen."
            )

        geometry = screen.geometry()

        width = geometry.width()
        height = geometry.height()

        if width <= 0 or height <= 0:
            raise PointerBackendUnavailable(
                "Wayland compositor returned "
                "invalid primary-screen geometry."
            )

        return PointerGeometry(
            x=geometry.x(),
            y=geometry.y(),
            width=width,
            height=height,
        )

    def virtual_geometry(
        self,
    ) -> PointerGeometry:
        """
        Return the complete logical desktop geometry.

        Prefer Qt's compositor-reported screen layout so merely
        querying mouse position does not require RemoteDesktop
        portal authorization.

        Portal stream geometry remains the fallback.
        """
        try:
            from PySide6.QtGui import (
                QGuiApplication,
            )

            app = (
                QGuiApplication.instance()
            )

            if app is None:
                app = QGuiApplication(
                    []
                )

                self._qt_app = app

            screens = (
                QGuiApplication.screens()
            )

            if screens:
                geometries = [
                    screen.geometry()
                    for screen in screens
                ]

                minimum_x = min(
                    geometry.x()
                    for geometry
                    in geometries
                )

                minimum_y = min(
                    geometry.y()
                    for geometry
                    in geometries
                )

                maximum_x = max(
                    (
                        geometry.x()
                        + geometry.width()
                        - 1
                    )
                    for geometry
                    in geometries
                )

                maximum_y = max(
                    (
                        geometry.y()
                        + geometry.height()
                        - 1
                    )
                    for geometry
                    in geometries
                )

                return PointerGeometry(
                    x=minimum_x,
                    y=minimum_y,
                    width=(
                        maximum_x
                        - minimum_x
                        + 1
                    ),
                    height=(
                        maximum_y
                        - minimum_y
                        + 1
                    ),
                )

        except Exception:
            pass

        self._ensure_session()

        minimum_x = min(
            stream.geometry.x
            for stream
            in self._streams
        )

        minimum_y = min(
            stream.geometry.y
            for stream
            in self._streams
        )

        maximum_x = max(
            stream.geometry.right
            for stream
            in self._streams
        )

        maximum_y = max(
            stream.geometry.bottom
            for stream
            in self._streams
        )

        return PointerGeometry(
            x=minimum_x,
            y=minimum_y,
            width=(
                maximum_x
                - minimum_x
                + 1
            ),
            height=(
                maximum_y
                - minimum_y
                + 1
            ),
        )

    def get_position(
        self,
    ) -> tuple[int, int]:
        if (
            self._cursor_position_provider
            is None
        ):
            from cursor_position_provider import (
                create_wayland_cursor_position_provider,
            )

            self._cursor_position_provider = (
                create_wayland_cursor_position_provider()
            )

        return (
            self._cursor_position_provider
            .get_position()
        )

    def _stream_for_position(
        self,
        x: int,
        y: int,
    ) -> WaylandPointerStream:
        for stream in self._streams:
            geometry = (
                stream.geometry
            )

            if (
                geometry.x
                <= x
                <= geometry.right
                and geometry.y
                <= y
                <= geometry.bottom
            ):
                return stream

        # Multi-monitor layouts can contain logical gaps.
        # Choose the stream whose rectangle is closest to the
        # requested point and clamp into it.
        def distance_squared(
            stream: WaylandPointerStream,
        ) -> int:
            geometry = (
                stream.geometry
            )

            nearest_x = max(
                geometry.x,
                min(
                    geometry.right,
                    x,
                ),
            )

            nearest_y = max(
                geometry.y,
                min(
                    geometry.bottom,
                    y,
                ),
            )

            return (
                (x - nearest_x) ** 2
                + (y - nearest_y) ** 2
            )

        return min(
            self._streams,
            key=distance_squared,
        )

    def move_to(
        self,
        x: int,
        y: int,
    ) -> None:
        self._ensure_session()

        stream = (
            self._stream_for_position(
                int(x),
                int(y),
            )
        )

        geometry = (
            stream.geometry
        )

        target_x = max(
            geometry.x,
            min(
                geometry.right,
                int(x),
            ),
        )

        target_y = max(
            geometry.y,
            min(
                geometry.bottom,
                int(y),
            ),
        )

        stream_x = float(
            target_x
            - geometry.x
        )

        stream_y = float(
            target_y
            - geometry.y
        )

        if self._session_handle is None:
            raise PointerBackendUnavailable(
                "Wayland portal session "
                "is not active."
            )

        self._bus.call_sync(
            PORTAL_NAME,
            PORTAL_PATH,
            REMOTE_DESKTOP_INTERFACE,
            "NotifyPointerMotionAbsolute",
            GLib.Variant(
                "(oa{sv}udd)",
                (
                    self._session_handle,
                    {},
                    stream.node_id,
                    stream_x,
                    stream_y,
                ),
            ),
            GLib.VariantType(
                "()"
            ),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

    def _close_portal_session(
        self,
    ) -> None:
        session_handle = self._session_handle

        self._session_handle = None
        self._streams = []
        self._authorized = False

        if session_handle is None:
            return

        try:
            self._bus.call_sync(
                PORTAL_NAME,
                session_handle,
                SESSION_INTERFACE,
                "Close",
                None,
                GLib.VariantType("()"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )

        except Exception:
            pass

    def close(
        self,
    ) -> None:
        provider = (
            self._cursor_position_provider
        )

        self._cursor_position_provider = None

        if provider is not None:
            try:
                provider.close()

            except Exception:
                pass

        self._close_portal_session()
