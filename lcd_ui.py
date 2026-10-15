from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from bindings import load_profile_file
from lcd import G13LCD
from profile_manager import ProfileManager
from runtime_profile_controller import (
    RuntimeProfileStatus,
)


MAX_PROFILE_NAME_LENGTH = 23
PROFILE_ROW_Y = (
    1,
    11,
    21,
)


def _fit_text(
    text: str,
    maximum: int,
) -> str:
    cleaned = (
        text
        .replace("_", " ")
        .strip()
        .upper()
    )

    if len(cleaned) <= maximum:
        return cleaned

    if maximum <= 3:
        return cleaned[:maximum]

    return (
        cleaned[:maximum - 3]
        + "..."
    )

def _fit_profile_text(
    text: str,
    maximum: int,
) -> str:
    cleaned = (
        text
        .replace("_", " ")
        .strip()
        .upper()
    )

    suffix = " (G13)"

    if not cleaned.endswith(
        suffix
    ):
        return _fit_text(
            cleaned,
            maximum,
        )

    if len(cleaned) <= maximum:
        return cleaned

    base = cleaned[
        :-len(suffix)
    ].rstrip()

    base_maximum = (
        maximum
        - len(suffix)
    )

    fitted_base = _fit_text(
        base,
        base_maximum,
    )

    return (
        fitted_base
        + suffix
    )

class LCDRuntimeUI:
    def __init__(
        self,
        lcd: G13LCD,
        profile_manager: ProfileManager,
    ):
        self.lcd = lcd
        self.profile_manager = profile_manager

        self.selected_profile: str | None = None
        self.browsing = False

        self.onboard_profiles: dict[
            str,
            dict[str, Any],
        ] = {}

    def set_onboard_profiles(
        self,
        profiles: dict[
            str,
            dict[str, Any],
        ],
    ) -> None:
        self.onboard_profiles = {
            identifier: copy.deepcopy(
                profile_data
            )
            for identifier, profile_data
            in profiles.items()
        }

        available_profiles = (
            self._profile_identifiers()
        )

        if (
            self.selected_profile
            not in available_profiles
        ):
            self.selected_profile = None
            self.browsing = False


    def _profile_identifiers(
        self,
    ) -> list[str]:
        return (
            self.profile_manager.list_profiles()
            + list(
                self.onboard_profiles.keys()
            )
        )

    def follow_runtime_profile(self) -> None:
        """
        Return the menu selection to the actual runtime profile.
        """
        self.browsing = False

    def get_selected_profile(
        self,
        status: RuntimeProfileStatus,
    ) -> str:
        self._ensure_selection(status)

        if self.selected_profile is None:
            raise RuntimeError(
                "No LCD profile is selected."
            )

        return self.selected_profile

    def move_selection(
        self,
        movement: int,
        status: RuntimeProfileStatus,
        layer: int,
    ) -> None:
        profiles = self._profile_identifiers()

        if not profiles:
            return

        self._ensure_selection(status)

        try:
            index = profiles.index(
                self.selected_profile
            )
        except ValueError:
            index = 0

        index = max(
            0,
            min(
                len(profiles) - 1,
                index + movement,
            ),
        )

        self.selected_profile = profiles[index]
        self.browsing = True

        self.show_runtime_status(
            status,
            layer,
        )

    def show_lcd_test(
        self,
    ) -> None:
        self.lcd.clear_buffer()

        # Screen-edge test pattern.
        self.lcd.fill_box(
            0,
            0,
            159,
            1,
            True,
        )

        self.lcd.fill_box(
            0,
            46,
            159,
            47,
            True,
        )

        self.lcd.fill_box(
            0,
            0,
            1,
            47,
            True,
        )

        self.lcd.fill_box(
            158,
            0,
            159,
            47,
            True,
        )

        self.lcd.draw_compact_text(
            "LCD TEST",
            56,
            10,
        )

        self.lcd.draw_compact_text(
            "160 X 48",
            56,
            24,
        )

        self.lcd.update()


    def show_profile_activation(
        self,
        filename: str,
    ) -> None:
        display_name = self._profile_display_name(
            filename
        )

        display_name = _fit_profile_text(
            display_name,
            MAX_PROFILE_NAME_LENGTH,
        )

        title_text = "PROFILE ACTIVATED"

        title_x = max(
            0,
            (
                160
                - len(title_text) * 6
            )
            // 2,
        )

        profile_x = max(
            0,
            (
                160
                - len(display_name) * 6
            )
            // 2,
        )

        self.lcd.clear_buffer()

        self.lcd.draw_compact_text(
            title_text,
            title_x,
            10,
        )

        self.lcd.draw_compact_text(
            display_name,
            profile_x,
            24,
        )

        self.lcd.update()


    def show_macro_recording(
        self,
    ) -> None:
        self.lcd.clear_buffer()

        self.lcd.draw_compact_text(
            "RECORDING MACRO",
            34,
            10,
        )

        self.lcd.draw_compact_text(
            "PRESS MR TO STOP",
            31,
            24,
        )

        self.lcd.update()

    def show_lua_messages(
        self,
        messages: list[str],
    ) -> None:
        self.lcd.clear_buffer()

        row_y = (
            1,
            11,
            21,
            31,
        )

        for index, message in enumerate(
            messages[-4:]
        ):
            self.lcd.draw_compact_text(
                str(message),
                1,
                row_y[index],
                maximum_width=158,
            )

        self.lcd.update()

    def _draw_runtime_controls(
        self,
        status: RuntimeProfileStatus,
    ) -> None:
        # LCD1: filled Up triangle
        up_x = 20

        self.lcd.set_pixel(
            up_x,
            35,
            True,
        )

        self.lcd.draw_horizontal_line(
            36,
            up_x - 1,
            up_x + 1,
            True,
        )

        self.lcd.draw_horizontal_line(
            37,
            up_x - 2,
            up_x + 2,
            True,
        )

        self.lcd.draw_horizontal_line(
            38,
            up_x - 3,
            up_x + 3,
            True,
        )

        # LCD2: filled Down triangle
        down_x = 60

        self.lcd.draw_horizontal_line(
            35,
            down_x - 3,
            down_x + 3,
            True,
        )

        self.lcd.draw_horizontal_line(
            36,
            down_x - 2,
            down_x + 2,
            True,
        )

        self.lcd.draw_horizontal_line(
            37,
            down_x - 1,
            down_x + 1,
            True,
        )

        self.lcd.set_pixel(
            down_x,
            38,
            True,
        )

        # LCD3: Select
        self.lcd.draw_compact_text(
            "SEL",
            94,
            35,
        )

        # LCD4: compact Lock / Unlock
        lock_center_x = 140

        body_left = (
            lock_center_x - 3
        )

        body_right = (
            lock_center_x + 3
        )

        body_top = 38
        body_bottom = 42

        self.lcd.draw_box(
            body_left,
            body_top,
            body_right,
            body_bottom,
            True,
        )

        # Keyhole
        self.lcd.set_pixel(
            lock_center_x,
            40,
            True,
        )

        self.lcd.set_pixel(
            lock_center_x,
            41,
            True,
        )

        selected_is_locked = (
            self.selected_profile is not None
            and self.selected_profile
            == status.locked_profile
        )

        if selected_is_locked:
            # Closed shackle.
            self.lcd.draw_horizontal_line(
                34,
                lock_center_x - 1,
                lock_center_x + 1,
                True,
            )

            self.lcd.draw_vertical_line(
                lock_center_x - 2,
                35,
                38,
                True,
            )

            self.lcd.draw_vertical_line(
                lock_center_x + 2,
                35,
                38,
                True,
            )

        else:
            # Open shackle. Right side stays attached
            # while the free end swings outward.
            attached_x = (
                lock_center_x + 2
            )

            free_x = (
                lock_center_x + 7
            )

            self.lcd.draw_vertical_line(
                attached_x,
                34,
                38,
                True,
            )

            self.lcd.draw_horizontal_line(
                34,
                attached_x,
                free_x,
                True,
            )

            self.lcd.draw_vertical_line(
                free_x,
                35,
                37,
                True,
            )

    def show_runtime_status(
        self,
        status: RuntimeProfileStatus,
        layer: int,
    ) -> None:
        profiles = self._profile_identifiers()

        self.lcd.clear_buffer()

        if not profiles:
            self.lcd.update()
            return

        self._ensure_selection(status)

        try:
            selected_index = profiles.index(
                self.selected_profile
            )
        except ValueError:
            selected_index = 0
            self.selected_profile = profiles[0]

        visible_count = min(
            3,
            len(profiles),
        )

        # Keep the selected profile near the second row when
        # possible, without wrapping past either end.
        if visible_count >= 2:
            first_visible_index = max(
                0,
                selected_index - 1,
            )

            first_visible_index = min(
                first_visible_index,
                len(profiles)
                - visible_count,
            )

        else:
            first_visible_index = (
                selected_index
            )

        for row_number in range(
            visible_count
        ):
            profile_index = (
                first_visible_index
                + row_number
            )

            filename = profiles[
                profile_index
            ]

            self._draw_profile_row(
                filename,
                status,
                y=PROFILE_ROW_Y[
                    row_number
                ],
                selected=(
                    filename
                    == self.selected_profile
                ),
            )

        self._draw_runtime_controls(
            status
        )

        self.lcd.update()

    def _ensure_selection(
        self,
        status: RuntimeProfileStatus,
    ) -> None:
        profiles = self._profile_identifiers()

        if not profiles:
            self.selected_profile = None
            return

        if (
            self.selected_profile not in profiles
            or not self.browsing
        ):
            if status.active_profile in profiles:
                self.selected_profile = (
                    status.active_profile
                )
            else:
                self.selected_profile = profiles[0]

    def _draw_profile_row(
        self,
        filename: str,
        status: RuntimeProfileStatus,
        y: int,
        selected: bool,
    ) -> None:
        active_marker = (
            "*"
            if filename == status.active_profile
            else " "
        )

        locked_marker = (
            "L"
            if filename == status.locked_profile
            else " "
        )

        display_name = self._profile_display_name(
            filename
        )

        row_text = (
            f"{active_marker}"
            f"{locked_marker} "
            f"{_fit_profile_text(
                display_name,
                MAX_PROFILE_NAME_LENGTH,
            )}"
        )

        if selected:
            self.lcd.fill_box(
                0,
                y,
                159,
                y + 7,
                True,
            )

        self.lcd.draw_compact_text(
            row_text,
            2,
            y,
            inverted=selected,
        )

    def _profile_display_name(
        self,
        filename: str,
    ) -> str:
        onboard_profile = (
            self.onboard_profiles.get(
                filename
            )
        )

        if onboard_profile is not None:
            display_name = (
                onboard_profile.get(
                    "name",
                    filename,
                )
            )

            if (
                not isinstance(
                    display_name,
                    str,
                )
                or not display_name.strip()
            ):
                display_name = filename

            return (
                f"{display_name} (G13)"
            )
        profile_path = (
            self.profile_manager.profiles_dir
            / filename
        )

        try:
            profile, _ = load_profile_file(
                profile_path
            )

            display_name = profile.get(
                "name"
            )

            if isinstance(display_name, str):
                return display_name

        except Exception:
            pass

        return Path(filename).stem
