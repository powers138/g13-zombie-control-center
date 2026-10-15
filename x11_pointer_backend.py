from __future__ import annotations

import ctypes
import ctypes.util

from pointer_backend import (
    PointerBackend,
    PointerBackendUnavailable,
    PointerGeometry,
)


class _XRRMonitorInfo(
    ctypes.Structure
):
    _fields_ = [
        (
            "name",
            ctypes.c_ulong,
        ),
        (
            "primary",
            ctypes.c_int,
        ),
        (
            "automatic",
            ctypes.c_int,
        ),
        (
            "noutput",
            ctypes.c_int,
        ),
        (
            "x",
            ctypes.c_int,
        ),
        (
            "y",
            ctypes.c_int,
        ),
        (
            "width",
            ctypes.c_int,
        ),
        (
            "height",
            ctypes.c_int,
        ),
        (
            "mwidth",
            ctypes.c_int,
        ),
        (
            "mheight",
            ctypes.c_int,
        ),
        (
            "outputs",
            ctypes.POINTER(
                ctypes.c_ulong
            ),
        ),
    ]


class X11PointerBackend(
    PointerBackend
):
    """
    Absolute-pointer backend for native X11 sessions.

    libX11 provides pointer querying and pointer warping.

    libXrandr is optional. When available, it is used to identify
    the primary monitor for Logitech MoveMouseTo(). The complete
    X11 root window is used for MoveMouseToVirtual().
    """

    name = "x11"

    def __init__(
        self,
    ) -> None:
        self._x11 = self._load_x11()
        self._xrandr = self._load_xrandr()

        self._configure_x11()
        self._configure_xrandr()

        self._display = (
            self._x11.XOpenDisplay(
                None
            )
        )

        if not self._display:
            raise PointerBackendUnavailable(
                "Could not open the X11 display."
            )

        self._screen = (
            self._x11.XDefaultScreen(
                self._display
            )
        )

        self._root = (
            self._x11.XRootWindow(
                self._display,
                self._screen,
            )
        )

    @staticmethod
    def _load_x11():
        library_name = (
            ctypes.util.find_library(
                "X11"
            )
        )

        if not library_name:
            raise PointerBackendUnavailable(
                "libX11 is not installed."
            )

        try:
            return ctypes.CDLL(
                library_name
            )

        except OSError as error:
            raise PointerBackendUnavailable(
                f"Could not load libX11: "
                f"{error}"
            ) from error

    @staticmethod
    def _load_xrandr():
        library_name = (
            ctypes.util.find_library(
                "Xrandr"
            )
        )

        if not library_name:
            return None

        try:
            library = ctypes.CDLL(
                library_name
            )

        except OSError:
            return None

        if not hasattr(
            library,
            "XRRGetMonitors",
        ):
            return None

        if not hasattr(
            library,
            "XRRFreeMonitors",
        ):
            return None

        return library

    def _configure_x11(
        self,
    ) -> None:
        self._x11.XOpenDisplay.argtypes = [
            ctypes.c_char_p,
        ]

        self._x11.XOpenDisplay.restype = (
            ctypes.c_void_p
        )

        self._x11.XCloseDisplay.argtypes = [
            ctypes.c_void_p,
        ]

        self._x11.XCloseDisplay.restype = (
            ctypes.c_int
        )

        self._x11.XDefaultScreen.argtypes = [
            ctypes.c_void_p,
        ]

        self._x11.XDefaultScreen.restype = (
            ctypes.c_int
        )

        self._x11.XRootWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]

        self._x11.XRootWindow.restype = (
            ctypes.c_ulong
        )

        self._x11.XDisplayWidth.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]

        self._x11.XDisplayWidth.restype = (
            ctypes.c_int
        )

        self._x11.XDisplayHeight.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
        ]

        self._x11.XDisplayHeight.restype = (
            ctypes.c_int
        )

        self._x11.XQueryPointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(
                ctypes.c_ulong
            ),
            ctypes.POINTER(
                ctypes.c_ulong
            ),
            ctypes.POINTER(
                ctypes.c_int
            ),
            ctypes.POINTER(
                ctypes.c_int
            ),
            ctypes.POINTER(
                ctypes.c_int
            ),
            ctypes.POINTER(
                ctypes.c_int
            ),
            ctypes.POINTER(
                ctypes.c_uint
            ),
        ]

        self._x11.XQueryPointer.restype = (
            ctypes.c_int
        )

        self._x11.XWarpPointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
        ]

        self._x11.XWarpPointer.restype = (
            ctypes.c_int
        )

        self._x11.XFlush.argtypes = [
            ctypes.c_void_p,
        ]

        self._x11.XFlush.restype = (
            ctypes.c_int
        )

    def _configure_xrandr(
        self,
    ) -> None:
        if self._xrandr is None:
            return

        self._xrandr.XRRGetMonitors.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.POINTER(
                ctypes.c_int
            ),
        ]

        self._xrandr.XRRGetMonitors.restype = (
            ctypes.POINTER(
                _XRRMonitorInfo
            )
        )

        self._xrandr.XRRFreeMonitors.argtypes = [
            ctypes.POINTER(
                _XRRMonitorInfo
            ),
        ]

        self._xrandr.XRRFreeMonitors.restype = (
            None
        )

    def virtual_geometry(
        self,
    ) -> PointerGeometry:
        width = (
            self._x11.XDisplayWidth(
                self._display,
                self._screen,
            )
        )

        height = (
            self._x11.XDisplayHeight(
                self._display,
                self._screen,
            )
        )

        if width <= 0 or height <= 0:
            raise PointerBackendUnavailable(
                "X11 returned invalid "
                "virtual-screen geometry."
            )

        return PointerGeometry(
            x=0,
            y=0,
            width=width,
            height=height,
        )

    def primary_geometry(
        self,
    ) -> PointerGeometry:
        monitors = (
            self._get_monitors()
        )

        if not monitors:
            return self.virtual_geometry()

        for (
            geometry,
            primary,
        ) in monitors:
            if primary:
                return geometry

        # Some X11 configurations do not explicitly mark an
        # output as primary. Prefer the monitor containing the
        # root origin in that case.
        for (
            geometry,
            _primary,
        ) in monitors:
            if (
                geometry.x <= 0
                <= geometry.right
                and geometry.y <= 0
                <= geometry.bottom
            ):
                return geometry

        return monitors[0][0]

    def _get_monitors(
        self,
    ) -> list[
        tuple[
            PointerGeometry,
            bool,
        ]
    ]:
        if self._xrandr is None:
            return []

        monitor_count = (
            ctypes.c_int()
        )

        monitor_pointer = (
            self._xrandr.XRRGetMonitors(
                self._display,
                self._root,
                1,
                ctypes.byref(
                    monitor_count
                ),
            )
        )

        if not monitor_pointer:
            return []

        monitors: list[
            tuple[
                PointerGeometry,
                bool,
            ]
        ] = []

        try:
            for index in range(
                monitor_count.value
            ):
                monitor = (
                    monitor_pointer[
                        index
                    ]
                )

                if (
                    monitor.width <= 0
                    or monitor.height <= 0
                ):
                    continue

                monitors.append(
                    (
                        PointerGeometry(
                            x=monitor.x,
                            y=monitor.y,
                            width=(
                                monitor.width
                            ),
                            height=(
                                monitor.height
                            ),
                        ),
                        bool(
                            monitor.primary
                        ),
                    )
                )

        finally:
            self._xrandr.XRRFreeMonitors(
                monitor_pointer
            )

        return monitors

    def get_position(
        self,
    ) -> tuple[int, int]:
        root_return = (
            ctypes.c_ulong()
        )

        child_return = (
            ctypes.c_ulong()
        )

        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        window_x = ctypes.c_int()
        window_y = ctypes.c_int()
        mask = ctypes.c_uint()

        success = (
            self._x11.XQueryPointer(
                self._display,
                self._root,
                ctypes.byref(
                    root_return
                ),
                ctypes.byref(
                    child_return
                ),
                ctypes.byref(
                    root_x
                ),
                ctypes.byref(
                    root_y
                ),
                ctypes.byref(
                    window_x
                ),
                ctypes.byref(
                    window_y
                ),
                ctypes.byref(
                    mask
                ),
            )
        )

        if not success:
            raise PointerBackendUnavailable(
                "X11 could not query "
                "the current pointer position."
            )

        return (
            root_x.value,
            root_y.value,
        )

    def move_to(
        self,
        x: int,
        y: int,
    ) -> None:
        geometry = (
            self.virtual_geometry()
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

        self._x11.XWarpPointer(
            self._display,
            0,
            self._root,
            0,
            0,
            0,
            0,
            target_x,
            target_y,
        )

        self._x11.XFlush(
            self._display
        )

    def close(
        self,
    ) -> None:
        # Do not call XCloseDisplay here. If the X server has
        # already disappeared, libX11 invokes its fatal I/O
        # handler and terminates the process. This backend lives
        # for the lifetime of the driver, so the OS will reclaim
        # the X11 connection when the process exits.
        self._display = None
