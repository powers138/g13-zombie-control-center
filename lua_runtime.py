from __future__ import annotations

from typing import Any
import time

from lupa import LuaError
from lupa.lua51 import LuaRuntime
from logitech_scancodes import (
    normalize_logitech_key,
)


class LuaProfileRuntime:
    """
    Runs the enabled Lua script for the current runtime profile.

    This first implementation exposes only OutputLogMessage and
    dispatches profile/G-key events. Hardware-control functions
    will be added separately.
    """

    def __init__(
        self,
    ) -> None:
        self.lua: LuaRuntime | None = None
        self.on_event = None
        self.profile_name: str | None = None
        self.enabled = False
        self._script_started_at = 0.0

        self._get_m_state_callback = None
        self._set_m_state_callback = None
        self._press_key_callback = None
        self._release_key_callback = None
        self._key_lock_state_callback = None
        self._press_mouse_callback = None
        self._release_mouse_callback = None
        self._move_mouse_callback = None
        self._mouse_wheel_callback = None
        self._modifier_state_callback = None
        self._mouse_button_state_callback = None

        self._get_mouse_position_callback = None
        self._move_mouse_to_callback = None
        self._move_mouse_to_virtual_callback = None

        self._lcd_message_callback = None
        self._clear_lcd_callback = None
        self._backlight_callback = None

        self._play_macro_callback = None
        self._abort_macro_callback = None

    def set_m_key_state_callbacks(
        self,
        getter,
        setter,
    ) -> None:
        self._get_m_state_callback = getter
        self._set_m_state_callback = setter

    def set_keyboard_callbacks(
        self,
        press_key,
        release_key,
    ) -> None:
        self._press_key_callback = press_key
        self._release_key_callback = release_key

    def set_macro_callbacks(
        self,
        play_macro,
        abort_macro,
    ) -> None:
        self._play_macro_callback = play_macro
        self._abort_macro_callback = abort_macro

    def set_input_state_callbacks(
        self,
        modifier_pressed,
        mouse_button_pressed,
    ) -> None:
        self._modifier_state_callback = (
            modifier_pressed
        )

        self._mouse_button_state_callback = (
            mouse_button_pressed
        )

    def set_key_lock_state_callback(
        self,
        callback,
    ) -> None:
        self._key_lock_state_callback = callback

    def set_mouse_callbacks(
        self,
        press_button,
        release_button,
        move_relative,
        move_wheel,
    ) -> None:
        self._press_mouse_callback = press_button
        self._release_mouse_callback = release_button
        self._move_mouse_callback = move_relative
        self._mouse_wheel_callback = move_wheel

    def set_absolute_mouse_callbacks(
        self,
        get_position=None,
        move_primary=None,
        move_virtual=None,
    ) -> None:
        self._get_mouse_position_callback = (
            get_position
        )

        self._move_mouse_to_callback = (
            move_primary
        )

        self._move_mouse_to_virtual_callback = (
            move_virtual
        )

    def set_g13_callbacks(
        self,
        output_lcd_message,
        clear_lcd,
        set_backlight_color,
    ) -> None:
        self._lcd_message_callback = (
            output_lcd_message
        )

        self._clear_lcd_callback = (
            clear_lcd
        )

        self._backlight_callback = (
            set_backlight_color
        )

    def deactivate_profile(
        self,
    ) -> None:
        if (
            self.enabled
            and self.on_event is not None
        ):
            self.dispatch(
                "PROFILE_DEACTIVATED",
                None,
                None,
            )

        self.lua = None
        self.on_event = None
        self.profile_name = None
        self.enabled = False
        self._script_started_at = 0.0



    def load_profile(
        self,
        profile_name: str,
        script: dict[str, Any],
    ) -> None:
        self.lua = None
        self.on_event = None
        self.profile_name = profile_name
        self.enabled = False
        self._script_started_at = 0.0

        if not isinstance(
            script,
            dict,
        ):
            return

        if not script.get(
            "enabled",
            False,
        ):
            return

        language = script.get(
            "language",
            "lua",
        )

        if language != "lua":
            print(
                f"[Lua:{profile_name}] "
                f"Unsupported script language: {language}"
            )

            return

        source = script.get(
            "source",
            "",
        )

        if (
            not isinstance(source, str)
            or not source.strip()
        ):
            return

        try:
            lua = LuaRuntime(
                register_eval=False,
                register_builtins=False,
                unpack_returned_tuples=True,
            )

            # Store the runtime before executing profile code so
            # API functions such as GetDate("*t") can construct
            # real Lua tables even when called at script load time.
            self.lua = lua

            self._script_started_at = (
                time.monotonic()
            )

            globals_table = lua.globals()

            globals_table[
                "OutputLogMessage"
            ] = self._output_log_message

            globals_table[
                "OutputDebugMessage"
            ] = self._output_debug_message

            globals_table[
                "ClearLog"
            ] = self._clear_log

            globals_table[
                "GetMKeyState"
            ] = self._get_m_key_state

            globals_table[
                "SetMKeyState"
            ] = self._set_m_key_state

            globals_table[
                "Sleep"
            ] = self._sleep

            globals_table[
                "GetRunningTime"
            ] = self._get_running_time

            globals_table[
                "GetDate"
            ] = self._get_date

            globals_table[
                "PlayMacro"
            ] = self._play_macro

            globals_table[
                "AbortMacro"
            ] = self._abort_macro

            globals_table[
                "IsKeyLockOn"
            ] = self._is_key_lock_on

            globals_table[
                "PressKey"
            ] = self._press_key

            globals_table[
                "ReleaseKey"
            ] = self._release_key

            globals_table[
                "PressAndReleaseKey"
            ] = self._press_and_release_key

            globals_table[
                "IsModifierPressed"
            ] = self._is_modifier_pressed

            globals_table[
                "IsMouseButtonPressed"
            ] = self._is_mouse_button_pressed

            globals_table[
                "GetMousePosition"
            ] = self._get_mouse_position

            globals_table[
                "PressMouseButton"
            ] = self._press_mouse_button

            globals_table[
                "ReleaseMouseButton"
            ] = self._release_mouse_button

            globals_table[
                "PressAndReleaseMouseButton"
            ] = self._press_and_release_mouse_button

            globals_table[
                "MoveMouseRelative"
            ] = self._move_mouse_relative

            globals_table[
                "MoveMouseTo"
            ] = self._move_mouse_to

            globals_table[
                "MoveMouseToVirtual"
            ] = self._move_mouse_to_virtual

            globals_table[
                "MoveMouseWheel"
            ] = self._move_mouse_wheel

            globals_table[
                "OutputLCDMessage"
            ] = self._output_lcd_message

            globals_table[
                "ClearLCD"
            ] = self._clear_lcd

            globals_table[
                "SetBacklightColor"
            ] = self._set_backlight_color

            # Remove standard Lua facilities that would allow a
            # profile script to access the host filesystem,
            # launch processes, load native modules, or reach
            # Python internals.
            lua.execute(
                """
                os = nil
                io = nil
                package = nil
                require = nil
                dofile = nil
                loadfile = nil
                debug = nil
                python = nil
                """
            )

            lua.execute(
                source
            )

            on_event = globals_table[
                "OnEvent"
            ]

            if on_event is None:
                print(
                    f"[Lua:{profile_name}] "
                    "Script does not define OnEvent()."
                )

                return

        except LuaError as error:
            print(
                f"[Lua:{profile_name}] "
                f"load error: {error}"
            )

            return

        except Exception as error:
            print(
                f"[Lua:{profile_name}] "
                f"runtime error: {error}"
            )

            return

        self.lua = lua
        self.on_event = on_event
        self.enabled = True

        print(
            f"[Lua:{profile_name}] loaded."
        )

        self.dispatch(
            "PROFILE_ACTIVATED",
            None,
            None,
        )

    def dispatch(
        self,
        event: str,
        arg: Any = None,
        family: str | None = "lhc",
    ) -> None:
        if (
            not self.enabled
            or self.on_event is None
        ):
            return

        try:
            self.on_event(
                event,
                arg,
                family,
            )

        except LuaError as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"OnEvent error: {error}"
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"callback error: {error}"
            )

    def _is_lhc_family(
        self,
        family,
    ) -> bool:
        if family is None:
            return True

        return (
            str(family).lower()
            == "lhc"
        )

    def _get_m_key_state(
        self,
        family=None,
    ):
        if not self._is_lhc_family(
            family
        ):
            return None

        if (
            self._get_m_state_callback
            is None
        ):
            return None

        try:
            return int(
                self._get_m_state_callback()
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"GetMKeyState error: {error}"
            )

            return None

    def _set_m_key_state(
        self,
        mkey,
        family=None,
    ) -> None:
        if not self._is_lhc_family(
            family
        ):
            return

        if (
            self._set_m_state_callback
            is None
        ):
            return

        try:
            mkey_number = int(
                mkey
            )

        except (
            TypeError,
            ValueError,
        ):
            print(
                f"[Lua:{self.profile_name}] "
                f"invalid M-key state: {mkey}"
            )

            return

        if mkey_number not in {
            1,
            2,
            3,
        }:
            print(
                f"[Lua:{self.profile_name}] "
                f"invalid M-key state: {mkey_number}"
            )

            return

        try:
            self._set_m_state_callback(
                mkey_number
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"SetMKeyState error: {error}"
            )

    def _is_key_lock_on(
        self,
        key_name,
    ) -> bool:
        if (
            self._key_lock_state_callback
            is None
        ):
            return False

        try:
            return bool(
                self._key_lock_state_callback(
                    key_name
                )
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"IsKeyLockOn error: {error}"
            )

            return False


    def _get_running_time(
        self,
    ) -> int:
        if self._script_started_at <= 0.0:
            return 0

        elapsed = (
            time.monotonic()
            - self._script_started_at
        )

        return int(
            elapsed * 1000
        )


    def _sleep(
        self,
        milliseconds,
    ) -> None:
        try:
            milliseconds = float(
                milliseconds
            )

        except (
            TypeError,
            ValueError,
        ):
            print(
                f"[Lua:{self.profile_name}] "
                f"invalid Sleep value: {milliseconds}"
            )

            return

        if milliseconds < 0:
            print(
                f"[Lua:{self.profile_name}] "
                "Sleep value cannot be negative."
            )

            return

        time.sleep(
            milliseconds / 1000.0
        )

    def _press_key(
        self,
        *keys,
    ) -> None:
        if (
            self._press_key_callback
            is None
        ):
            return

        for key in keys:
            try:
                resolved_key = (
                    normalize_logitech_key(
                        key
                    )
                )

                self._press_key_callback(
                    resolved_key
                )

            except Exception as error:
                print(
                    f"[Lua:{self.profile_name}] "
                    f"PressKey error for "
                    f"{key!r}: {error}"
                )

    def _release_key(
        self,
        *keys,
    ) -> None:
        if (
            self._release_key_callback
            is None
        ):
            return

        for key in keys:
            try:
                resolved_key = (
                    normalize_logitech_key(
                        key
                    )
                )

                self._release_key_callback(
                    resolved_key
                )

            except Exception as error:
                print(
                    f"[Lua:{self.profile_name}] "
                    f"ReleaseKey error for "
                    f"{key!r}: {error}"
                )

    def _press_and_release_key(
        self,
        *keys,
    ) -> None:
        if (
            self._press_key_callback
            is None
            or self._release_key_callback
            is None
        ):
            return

        pressed_keys = []

        try:
            for key in keys:
                resolved_key = (
                    normalize_logitech_key(
                        key
                    )
                )

                self._press_key_callback(
                    resolved_key
                )

                pressed_keys.append(
                    resolved_key
                )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"PressAndReleaseKey error: "
                f"{error}"
            )

        finally:
            for key in reversed(
                pressed_keys
            ):
                try:
                    self._release_key_callback(
                        key
                    )

                except Exception as error:
                    print(
                        f"[Lua:{self.profile_name}] "
                        f"key release error for "
                        f"{key!r}: {error}"
                    )

    def _get_date(
        self,
        format_string="%c",
        timestamp=None,
    ):
        """
        Logitech-compatible GetDate().

        Mirrors Lua 5.1 os.date():

        GetDate()
        GetDate("%Y-%m-%d")
        GetDate("!%Y-%m-%d")
        GetDate("*t")
        GetDate("!*t")
        GetDate(format, timestamp)
        """
        try:
            if format_string is None:
                format_string = "%c"

            format_string = str(
                format_string
            )

            use_utc = (
                format_string.startswith("!")
            )

            if use_utc:
                format_string = (
                    format_string[1:]
                )

            if timestamp is None:
                timestamp_value = time.time()

            else:
                timestamp_value = float(
                    timestamp
                )

            if use_utc:
                date_value = time.gmtime(
                    timestamp_value
                )

            else:
                date_value = time.localtime(
                    timestamp_value
                )

            if format_string == "*t":
                if self.lua is None:
                    return None

                # Python tm_wday uses Monday=0.
                # Lua os.date("*t") uses Sunday=1.
                lua_wday = (
                    (
                        date_value.tm_wday
                        + 1
                    )
                    % 7
                ) + 1

                return self.lua.table_from(
                    {
                        "year": date_value.tm_year,
                        "month": date_value.tm_mon,
                        "day": date_value.tm_mday,
                        "hour": date_value.tm_hour,
                        "min": date_value.tm_min,
                        "sec": date_value.tm_sec,
                        "wday": lua_wday,
                        "yday": date_value.tm_yday,
                        "isdst": (
                            date_value.tm_isdst
                            > 0
                        ),
                    }
                )

            return time.strftime(
                format_string,
                date_value,
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"GetDate error: "
                f"{error}"
            )

            return None

    def _get_mouse_position(
        self,
    ):
        if (
            self._get_mouse_position_callback
            is None
        ):
            return (
                None,
                None,
            )

        try:
            position = (
                self._get_mouse_position_callback()
            )

            if (
                not isinstance(
                    position,
                    tuple,
                )
                or len(position) != 2
            ):
                raise ValueError(
                    "Mouse-position callback "
                    "must return x and y."
                )

            return (
                int(
                    position[0]
                ),
                int(
                    position[1]
                ),
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"GetMousePosition error: "
                f"{error}"
            )

            return (
                None,
                None,
            )


    def _mouse_button_number(
        self,
        button,
    ) -> int:
        try:
            button_number = int(
                button
            )

        except (
            TypeError,
            ValueError,
        ):
            raise ValueError(
                f"Invalid mouse button: {button}"
            )

        if button_number not in {
            1,
            2,
            3,
            4,
            5,
        }:
            raise ValueError(
                "Mouse button must be "
                "between 1 and 5."
            )

        return button_number

    def _is_modifier_pressed(
        self,
        key_name,
    ) -> bool:
        if (
            self._modifier_state_callback
            is None
        ):
            return False

        try:
            return bool(
                self._modifier_state_callback(
                    key_name
                )
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"IsModifierPressed error: "
                f"{error}"
            )

            return False

    def _is_mouse_button_pressed(
        self,
        button,
    ) -> bool:
        if (
            self._mouse_button_state_callback
            is None
        ):
            return False

        try:
            button_number = (
                self._mouse_button_number(
                    button
                )
            )

            return bool(
                self._mouse_button_state_callback(
                    button_number
                )
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"IsMouseButtonPressed error: "
                f"{error}"
            )

            return False


    def _press_mouse_button(
        self,
        button,
    ) -> None:
        if (
            self._press_mouse_callback
            is None
        ):
            return

        try:
            self._press_mouse_callback(
                self._mouse_button_number(
                    button
                )
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"PressMouseButton error: {error}"
            )

    def _release_mouse_button(
        self,
        button,
    ) -> None:
        if (
            self._release_mouse_callback
            is None
        ):
            return

        try:
            self._release_mouse_callback(
                self._mouse_button_number(
                    button
                )
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"ReleaseMouseButton error: {error}"
            )

    def _press_and_release_mouse_button(
        self,
        button,
    ) -> None:
        if (
            self._press_mouse_callback
            is None
            or self._release_mouse_callback
            is None
        ):
            return

        try:
            button_number = (
                self._mouse_button_number(
                    button
                )
            )

            self._press_mouse_callback(
                button_number
            )

            self._release_mouse_callback(
                button_number
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"PressAndReleaseMouseButton "
                f"error: {error}"
            )

    def _move_mouse_relative(
        self,
        x,
        y,
    ) -> None:
        if (
            self._move_mouse_callback
            is None
        ):
            return

        try:
            x_value = int(x)
            y_value = int(y)

            self._move_mouse_callback(
                x_value,
                y_value,
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"MoveMouseRelative error: {error}"
            )

    def _move_mouse_to(
        self,
        x,
        y,
    ) -> None:
        if (
            self._move_mouse_to_callback
            is None
        ):
            return

        try:
            self._move_mouse_to_callback(
                x,
                y,
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"MoveMouseTo error: "
                f"{error}"
            )

    def _move_mouse_to_virtual(
        self,
        x,
        y,
    ) -> None:
        if (
            self._move_mouse_to_virtual_callback
            is None
        ):
            return

        try:
            self._move_mouse_to_virtual_callback(
                x,
                y,
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"MoveMouseToVirtual error: "
                f"{error}"
            )

    def _move_mouse_wheel(
        self,
        amount,
    ) -> None:
        if (
            self._mouse_wheel_callback
            is None
        ):
            return

        try:
            amount_value = int(
                amount
            )

            self._mouse_wheel_callback(
                amount_value
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"MoveMouseWheel error: {error}"
            )

    def _output_lcd_message(
        self,
        text,
        timeout=1000,
    ) -> None:
        if (
            self._lcd_message_callback
            is None
        ):
            return

        try:
            timeout_ms = int(
                timeout
            )

        except (
            TypeError,
            ValueError,
        ):
            print(
                f"[Lua:{self.profile_name}] "
                f"invalid LCD timeout: {timeout}"
            )

            return

        if timeout_ms < 0:
            timeout_ms = 0

        try:
            self._lcd_message_callback(
                str(text),
                timeout_ms,
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"OutputLCDMessage error: {error}"
            )

    def _clear_lcd(
        self,
    ) -> None:
        if (
            self._clear_lcd_callback
            is None
        ):
            return

        try:
            self._clear_lcd_callback()

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"ClearLCD error: {error}"
            )

    def _set_backlight_color(
        self,
        red,
        green,
        blue,
        family=None,
    ) -> None:
        if (
            family is not None
            and str(family).lower()
            != "lhc"
        ):
            return

        if (
            self._backlight_callback
            is None
        ):
            return

        try:
            red_value = max(
                0,
                min(
                    255,
                    int(red),
                ),
            )

            green_value = max(
                0,
                min(
                    255,
                    int(green),
                ),
            )

            blue_value = max(
                0,
                min(
                    255,
                    int(blue),
                ),
            )

            self._backlight_callback(
                red_value,
                green_value,
                blue_value,
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"SetBacklightColor error: {error}"
            )

    def _play_macro(
        self,
        macro_name,
    ) -> None:
        if self._play_macro_callback is None:
            return

        if not isinstance(
            macro_name,
            str,
        ):
            print(
                f"[Lua:{self.profile_name}] "
                "PlayMacro requires a macro name."
            )
            return

        name = macro_name.strip()

        if not name:
            print(
                f"[Lua:{self.profile_name}] "
                "PlayMacro requires a non-empty "
                "macro name."
            )
            return

        try:
            self._play_macro_callback(
                name
            )

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"PlayMacro error for "
                f"{name!r}: {error}"
            )

    def _abort_macro(
        self,
    ) -> None:
        if self._abort_macro_callback is None:
            return

        try:
            self._abort_macro_callback()

        except Exception as error:
            print(
                f"[Lua:{self.profile_name}] "
                f"AbortMacro error: {error}"
            )

    def _clear_log(
        self,
    ) -> None:
        """
        Logitech-compatible ClearLog().

        Logitech Gaming Software cleared its dedicated script
        output window. The Linux driver currently writes Lua log
        messages directly to the driver terminal, so clearing that
        terminal would also destroy unrelated driver diagnostics.

        Keep this as a safe no-op until a dedicated Lua output pane
        is implemented.
        """
        return None

    def _output_message(
        self,
        channel: str,
        message,
        *arguments,
    ) -> None:
        text = str(
            message
        )

        if arguments:
            try:
                text = text % tuple(
                    arguments
                )

            except Exception:
                text = " ".join(
                    [
                        text,
                        *(
                            str(argument)
                            for argument
                            in arguments
                        ),
                    ]
                )

        prefix = (
            f"[{channel}:{self.profile_name}] "
        )

        if text.endswith("\n"):
            print(
                prefix + text,
                end="",
            )

        else:
            print(
                prefix + text
            )

    def _output_debug_message(
        self,
        message,
        *arguments,
    ) -> None:
        self._output_message(
            "LuaDebug",
            message,
            *arguments,
        )

    def _output_log_message(
        self,
        message,
        *arguments,
    ) -> None:
        self._output_message(
            "Lua",
            message,
            *arguments,
        )
