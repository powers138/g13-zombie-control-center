from __future__ import annotations

import os


class CursorPositionUnavailable(
    RuntimeError
):
    pass


class CursorPositionProvider:
    name = "unsupported"

    def get_position(
        self,
    ) -> tuple[int, int]:
        raise CursorPositionUnavailable(
            "Cursor position is unavailable."
        )

    def close(
        self,
    ) -> None:
        return None


class UnsupportedCursorPositionProvider(
    CursorPositionProvider
):
    name = "unsupported"

    def __init__(
        self,
        reason: str,
    ) -> None:
        self.reason = reason

    def get_position(
        self,
    ) -> tuple[int, int]:
        raise CursorPositionUnavailable(
            self.reason
        )


def _current_desktop() -> str:
    return (
        os.environ
        .get(
            "XDG_CURRENT_DESKTOP",
            "",
        )
        .strip()
        .lower()
    )


def create_wayland_cursor_position_provider(
) -> CursorPositionProvider:
    """
    Select a compositor-specific global cursor-position provider.

    Wayland has no generic unrestricted global-pointer query, so
    this layer is intentionally separate from the generic
    RemoteDesktop movement backend.
    """
    desktop = _current_desktop()

    if (
        "kde" in desktop
        or "plasma" in desktop
    ):
        try:
            from kwin_cursor_position_provider import (
                KWinCursorPositionProvider,
            )

            return KWinCursorPositionProvider()

        except Exception as error:
            return UnsupportedCursorPositionProvider(
                "KWin cursor-position provider "
                f"is unavailable: {error}"
            )

    return UnsupportedCursorPositionProvider(
        "No global cursor-position provider "
        f"is available for desktop '{desktop or 'unknown'}'."
    )
