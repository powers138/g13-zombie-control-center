from __future__ import annotations

from dataclasses import dataclass
import os


LOGITECH_COORD_MIN = 0
LOGITECH_COORD_MAX = 65535


class PointerBackendError(RuntimeError):
    """
    Base error for absolute-pointer backend failures.
    """


class PointerBackendUnavailable(
    PointerBackendError
):
    """
    Raised when the current desktop session cannot provide the
    requested absolute-pointer operation.
    """


@dataclass(
    frozen=True,
)
class PointerGeometry:
    """
    A rectangular area in desktop logical/pixel coordinates.

    Origins may be negative on multi-monitor layouts.
    """

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return (
            self.x
            + self.width
            - 1
        )

    @property
    def bottom(self) -> int:
        return (
            self.y
            + self.height
            - 1
        )


class PointerBackend:
    """
    Interface used by Logitech-style absolute pointer functions.

    Backends deal only in real desktop coordinates.

    Translation between Logitech's normalized 0..65535
    coordinates and desktop coordinates stays outside the
    platform-specific implementation.
    """

    name = "unsupported"

    def primary_geometry(
        self,
    ) -> PointerGeometry:
        raise PointerBackendUnavailable(
            "Primary-screen geometry is unavailable."
        )

    def virtual_geometry(
        self,
    ) -> PointerGeometry:
        raise PointerBackendUnavailable(
            "Virtual-desktop geometry is unavailable."
        )

    def get_position(
        self,
    ) -> tuple[int, int]:
        raise PointerBackendUnavailable(
            "Pointer position is unavailable."
        )

    def move_to(
        self,
        x: int,
        y: int,
    ) -> None:
        raise PointerBackendUnavailable(
            "Absolute pointer movement is unavailable."
        )

    def close(
        self,
    ) -> None:
        """
        Release backend-owned resources.

        Backends without persistent resources may leave this as a
        no-op.
        """
        return None


class UnsupportedPointerBackend(
    PointerBackend
):
    name = "unsupported"

    def __init__(
        self,
        reason: str,
    ) -> None:
        self.reason = reason

    def _unavailable(
        self,
    ) -> PointerBackendUnavailable:
        return PointerBackendUnavailable(
            self.reason
        )

    def primary_geometry(
        self,
    ) -> PointerGeometry:
        raise self._unavailable()

    def virtual_geometry(
        self,
    ) -> PointerGeometry:
        raise self._unavailable()

    def get_position(
        self,
    ) -> tuple[int, int]:
        raise self._unavailable()

    def move_to(
        self,
        x: int,
        y: int,
    ) -> None:
        raise self._unavailable()


def detect_session_type() -> str:
    """
    Return wayland, x11, or unknown.

    Prefer XDG_SESSION_TYPE when available. The display
    environment variables provide a fallback for sessions where
    it is not exported into the driver environment.
    """
    session_type = (
        os.environ
        .get(
            "XDG_SESSION_TYPE",
            "",
        )
        .strip()
        .lower()
    )

    if session_type in {
        "wayland",
        "x11",
    }:
        return session_type

    if os.environ.get(
        "WAYLAND_DISPLAY"
    ):
        return "wayland"

    if os.environ.get(
        "DISPLAY"
    ):
        return "x11"

    return "unknown"


def clamp_logitech_coordinate(
    value,
) -> int:
    """
    Convert a Logitech normalized coordinate to an integer in
    the valid 0..65535 range.
    """
    coordinate = int(
        round(
            float(value)
        )
    )

    return max(
        LOGITECH_COORD_MIN,
        min(
            LOGITECH_COORD_MAX,
            coordinate,
        ),
    )


def normalized_to_desktop(
    value,
    origin: int,
    length: int,
) -> int:
    """
    Convert Logitech 0..65535 coordinates into one desktop axis.
    """
    if length <= 0:
        raise ValueError(
            "Desktop geometry length must be positive."
        )

    if length == 1:
        return origin

    coordinate = (
        clamp_logitech_coordinate(
            value
        )
    )

    offset = round(
        (
            coordinate
            * (length - 1)
        )
        / LOGITECH_COORD_MAX
    )

    return (
        origin
        + offset
    )


def desktop_to_normalized(
    value,
    origin: int,
    length: int,
) -> int:
    """
    Convert one desktop axis into Logitech's normalized
    0..65535 coordinate range.
    """
    if length <= 0:
        raise ValueError(
            "Desktop geometry length must be positive."
        )

    if length == 1:
        return LOGITECH_COORD_MIN

    minimum = origin
    maximum = (
        origin
        + length
        - 1
    )

    coordinate = max(
        minimum,
        min(
            maximum,
            int(
                round(
                    float(value)
                )
            ),
        ),
    )

    normalized = round(
        (
            (coordinate - origin)
            * LOGITECH_COORD_MAX
        )
        / (length - 1)
    )

    return clamp_logitech_coordinate(
        normalized
    )

def create_pointer_backend(
) -> PointerBackend:
    """
    Select the best available absolute-pointer backend for the
    current desktop session.

    Import platform backends lazily so systems that do not have
    their libraries installed can still use the rest of the G13
    driver.
    """
    session_type = (
        detect_session_type()
    )

    if session_type == "x11":
        try:
            from x11_pointer_backend import (
                X11PointerBackend,
            )

            return X11PointerBackend()

        except Exception as error:
            return UnsupportedPointerBackend(
                f"X11 pointer backend "
                f"is unavailable: {error}"
            )

    if session_type == "wayland":
        try:
            from wayland_pointer_backend import (
                WaylandPointerBackend,
            )

            return WaylandPointerBackend()

        except Exception as error:
            return UnsupportedPointerBackend(
                f"Wayland pointer backend "
                f"is unavailable: {error}"
            )

    return UnsupportedPointerBackend(
        "Could not determine the desktop "
        "session type."
    )
