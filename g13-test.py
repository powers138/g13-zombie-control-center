import os
import time

from driver_lifecycle import (
    DriverAlreadyRunning,
    DriverLifecycle,
)


driver_lifecycle = DriverLifecycle()

if __name__ == "__main__":
    try:
        driver_lifecycle.start()

    except DriverAlreadyRunning as error:
        print(error)
        raise SystemExit(75) from None

    except (OSError, RuntimeError, ValueError) as error:
        print("G13 driver startup refused:", error)
        raise SystemExit(1) from None

    print(
        f"G13 driver process: PID {os.getpid()}"
    )


from decoder import G13Decoder
from errno import EINTR
from hid import HIDException
from evdev import (
    InputDevice,
    ecodes as e,
    list_devices,
)
from pathlib import Path

from device import (
    open_g13,
    set_g13_backlight,
    set_g13_indicator_leds,
)

from lua_runtime import LuaProfileRuntime
from runtime_profile_controller import (
    RuntimeProfileController,
)
from dispatcher import execute_action
from event_bus import (
    G13CommandListener,
    G13EventPublisher,
)
from joystick import JoystickController
from keyboard import (
    is_key_held,
    release_all_keys,
    send_key,
)
from lcd import G13LCD
from lcd_ui import LCDRuntimeUI
from macro_engine import (
    abort_script_macro,
    play_script_macro,
    stop_all_macros,
)
from mouse import (
    is_button_held,
    move_relative,
    release_all_buttons,
    scroll_vertical,
    send_button,
)
from pointer_backend import (
    create_pointer_backend,
    desktop_to_normalized,
    normalized_to_desktop,
)
from profile_manager import ProfileManager
from profiles_to_go import (
    get_cached_onboard_status,
    get_onboard_profiles,
    get_onboard_status,
    write_onboard_cache,
)
from import_logitech_profile import (
    convert_profile_xml,
    linux_key_name,
)

LAYER_BUTTONS = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
}


_current_layer = 1


def set_layer(
    layer: int,
) -> None:
    global _current_layer

    _current_layer = layer


def get_layer() -> int:
    return _current_layer


profile_manager = ProfileManager()

absolute_pointer_backend = (
    create_pointer_backend()
)

print(
    "Absolute pointer backend:",
    absolute_pointer_backend.name,
)

runtime_profile_controller = (
    RuntimeProfileController(
        profile_manager
    )
)

# Remembers the exact action selected when each button went down.
pressed_actions = {}

event_publisher = G13EventPublisher()
command_listener = G13CommandListener()
lua_runtime = LuaProfileRuntime()

macro_recording_lcd_active = False

profile_activation_lcd_profile = None
profile_activation_lcd_until = 0.0
lua_lcd_messages = []

lcd_test_until = 0.0

led_test_until = 0.0

g13_device = None
lcd_runtime_ui = None
runtime_profile_action_pending = False
onboard_runtime_profiles_ready = False

G13_RECONNECT_RETRY_SECONDS = 0.5
G13_RESUME_GAP_SECONDS = 0.5


def _suspend_clock_offset() -> float | None:
    clock_id = getattr(
        time,
        "CLOCK_BOOTTIME",
        None,
    )
    clock_gettime = getattr(
        time,
        "clock_gettime",
        None,
    )

    if (
        clock_id is None
        or not callable(clock_gettime)
    ):
        return None

    return (
        clock_gettime(clock_id)
        - time.monotonic()
    )


def _close_g13_device(device) -> None:
    if device is None:
        return

    close = getattr(
        device,
        "close",
        None,
    )

    if not callable(close):
        return

    try:
        close()
    except Exception as error:
        print(
            "G13 device close failed:",
            error,
        )


def _release_inputs_for_reconnect(
    joystick,
) -> None:
    for button, layer in list(
        pressed_button_layers.items()
    ):
        try:
            event_publisher.publish_button(
                button,
                False,
                layer,
            )
        except Exception as error:
            print(
                "Button release publish failed:",
                error,
            )

    for action, source_id in list(
        pressed_actions.values()
    ):
        try:
            execute_action(
                action,
                False,
                source_id=source_id,
            )
        except Exception as error:
            print(
                "Action release during reconnect failed:",
                error,
            )

    pressed_actions.clear()
    pressed_button_layers.clear()

    stop_all_macros()
    release_all_keys()
    release_all_buttons()

    if joystick is not None:
        joystick.stop()

    lua_runtime.deactivate_profile()


def _reconnect_g13_hardware(
    old_device,
    old_joystick,
    current_joystick_settings,
):
    global g13_device
    global lcd_runtime_ui
    global runtime_profile_action_pending

    _release_inputs_for_reconnect(
        old_joystick
    )

    g13_device = None
    lcd_runtime_ui = None

    _close_g13_device(
        old_device
    )

    print(
        "G13 connection lost; "
        "waiting for device..."
    )

    first_error = True

    while not driver_lifecycle.stop_requested:
        new_device = None

        try:
            new_device = open_g13()

            new_lcd = G13LCD(
                new_device
            )
            new_lcd.initialize()
            new_lcd.show_zombie()

            time.sleep(0.5)

            g13_device = new_device

            lcd_runtime_ui = LCDRuntimeUI(
                new_lcd,
                profile_manager,
            )

            refresh_onboard_runtime_profiles()

            if onboard_runtime_profiles_ready:
                runtime_profile_changed = (
                    runtime_profile_controller
                    .check()
                )

                publish_runtime_profile_status()

                if runtime_profile_changed:
                    runtime_profile_action_pending = True

                    current_joystick_settings = (
                        profile_manager
                        .get_joystick_settings()
                    )

            new_joystick = (
                JoystickController()
            )

            new_decoder = G13Decoder(
                handle_button,
                new_joystick,
                current_joystick_settings,
            )

            apply_backlight(
                get_layer()
            )
            apply_indicator_leds()

            reload_lua_profile()
            refresh_lcd_status()

            event_publisher.publish_layer(
                get_layer()
            )

            print(
                "G13 connection restored."
            )

            return (
                new_device,
                new_lcd,
                new_joystick,
                new_decoder,
            )

        except (
            HIDException,
            OSError,
            RuntimeError,
        ) as error:
            _close_g13_device(
                new_device
            )

            g13_device = None
            lcd_runtime_ui = None

            if first_error:
                print(
                    "G13 reconnect pending:",
                    error,
                )
                first_error = False

            time.sleep(
                G13_RECONNECT_RETRY_SECONDS
            )

    return (
        None,
        None,
        None,
        None,
    )

M_LED_MASKS = {
    1: 0x01,
    2: 0x02,
    3: 0x04,
}

active_m_led_mask = 0x01
mr_led_active = False

current_backlight_colors = (
    profile_manager.get_backlight_colors()
)

LUA_MOUSE_BUTTONS = {
    1: "left",
    2: "middle",
    3: "right",
    4: "back",
    5: "forward",
}


def resolve_lua_mouse_button(
    button: int,
) -> str:
    mouse_button = (
        LUA_MOUSE_BUTTONS.get(
            button
        )
    )

    if mouse_button is None:
        raise ValueError(
            f"Unsupported Logitech "
            f"mouse button: {button}"
        )

    return mouse_button


def press_lua_mouse_button(
    button: int,
) -> None:
    send_button(
        resolve_lua_mouse_button(
            button
        ),
        True,
    )


def release_lua_mouse_button(
    button: int,
) -> None:
    send_button(
        resolve_lua_mouse_button(
            button
        ),
        False,
    )

LOCK_LED_NAMES = {
    "capslock": "capslock",
    "numlock": "numlock",
    "scrolllock": "scrolllock",
}


def is_lua_key_lock_on(
    key_name,
) -> bool:
    if not isinstance(
        key_name,
        str,
    ):
        raise ValueError(
            "Lock-key name must be text."
        )

    normalized_name = (
        key_name
        .strip()
        .lower()
    )

    led_name = (
        LOCK_LED_NAMES.get(
            normalized_name
        )
    )

    if led_name is None:
        raise ValueError(
            f"Unsupported lock key: {key_name}"
        )

    led_root = Path(
        "/sys/class/leds"
    )

    for led_path in led_root.glob(
        f"*::{led_name}"
    ):
        brightness_path = (
            led_path
            / "brightness"
        )

        try:
            brightness = int(
                brightness_path
                .read_text(
                    encoding="utf-8"
                )
                .strip()
            )

        except (
            OSError,
            ValueError,
        ):
            continue

        if brightness > 0:
            return True

    return False

LUA_MODIFIER_KEYS = {
    "lalt": {
        e.KEY_LEFTALT,
    },
    "ralt": {
        e.KEY_RIGHTALT,
    },
    "alt": {
        e.KEY_LEFTALT,
        e.KEY_RIGHTALT,
    },
    "lshift": {
        e.KEY_LEFTSHIFT,
    },
    "rshift": {
        e.KEY_RIGHTSHIFT,
    },
    "shift": {
        e.KEY_LEFTSHIFT,
        e.KEY_RIGHTSHIFT,
    },
    "lctrl": {
        e.KEY_LEFTCTRL,
    },
    "rctrl": {
        e.KEY_RIGHTCTRL,
    },
    "ctrl": {
        e.KEY_LEFTCTRL,
        e.KEY_RIGHTCTRL,
    },
}


LUA_MOUSE_BUTTON_CODES = {
    1: e.BTN_LEFT,
    2: e.BTN_MIDDLE,
    3: e.BTN_RIGHT,
    4: e.BTN_SIDE,
    5: e.BTN_EXTRA,
}


def any_input_code_pressed(
    codes: set[int],
) -> bool:
    for device_path in list_devices():
        device = None

        try:
            device = InputDevice(
                device_path
            )

            active_codes = set(
                device.active_keys()
            )

        except (
            OSError,
            PermissionError,
        ):
            continue

        finally:
            if device is not None:
                try:
                    device.close()

                except OSError:
                    pass

        if active_codes.intersection(
            codes
        ):
            return True

    return False


def is_lua_modifier_pressed(
    key_name,
) -> bool:
    if not isinstance(
        key_name,
        str,
    ):
        raise ValueError(
            "Modifier name must be text."
        )

    normalized_name = (
        key_name
        .strip()
        .lower()
    )

    codes = LUA_MODIFIER_KEYS.get(
        normalized_name
    )

    if codes is None:
        raise ValueError(
            f"Unsupported modifier: "
            f"{key_name}"
        )

    # Our own virtual keyboard state.
    if any(
        is_key_held(code)
        for code in codes
    ):
        return True

    # Physical keyboards and other input
    # devices visible through evdev.
    return any_input_code_pressed(
        codes
    )

def get_lua_mouse_position(
) -> tuple[int, int]:
    x, y = (
        absolute_pointer_backend
        .get_position()
    )

    geometry = (
        absolute_pointer_backend
        .virtual_geometry()
    )

    normalized_x = (
        desktop_to_normalized(
            x,
            geometry.x,
            geometry.width,
        )
    )

    normalized_y = (
        desktop_to_normalized(
            y,
            geometry.y,
            geometry.height,
        )
    )

    return (
        normalized_x,
        normalized_y,
    )



def _move_lua_mouse_to_geometry(
    x,
    y,
    geometry,
) -> None:
    target_x = normalized_to_desktop(
        x,
        geometry.x,
        geometry.width,
    )

    target_y = normalized_to_desktop(
        y,
        geometry.y,
        geometry.height,
    )

    absolute_pointer_backend.move_to(
        target_x,
        target_y,
    )


def move_lua_mouse_to(
    x,
    y,
) -> None:
    _move_lua_mouse_to_geometry(
        x,
        y,
        absolute_pointer_backend.primary_geometry(),
    )


def move_lua_mouse_to_virtual(
    x,
    y,
) -> None:
    _move_lua_mouse_to_geometry(
        x,
        y,
        absolute_pointer_backend.virtual_geometry(),
    )

def is_lua_mouse_button_pressed(
    button: int,
) -> bool:
    button_name = (
        resolve_lua_mouse_button(
            button
        )
    )

    if is_button_held(
        button_name
    ):
        return True

    button_code = (
        LUA_MOUSE_BUTTON_CODES.get(
            button
        )
    )

    if button_code is None:
        return False

    return any_input_code_pressed(
        {
            button_code,
        }
    )

def resolve_lua_key(
    key,
) -> int:
    if not isinstance(
        key,
        str,
    ):
        raise ValueError(
            "Numeric Logitech scan codes "
            "are not supported yet."
        )

    linux_name = linux_key_name(
        key
    )

    keycode = getattr(
        e,
        linux_name,
        None,
    )

    if not isinstance(
        keycode,
        int,
    ):
        raise ValueError(
            f"Unknown Logitech key: {key}"
        )

    return keycode


def press_lua_key(
    key,
) -> None:
    send_key(
        resolve_lua_key(key),
        True,
    )


def release_lua_key(
    key,
) -> None:
    send_key(
        resolve_lua_key(key),
        False,
    )


def play_lua_macro(
    macro_name: str,
) -> str:
    action = (
        profile_manager
        .get_macro_by_name(
            macro_name
        )
    )

    if action is None:
        raise ValueError(
            f"Macro not found in current "
            f"profile: {macro_name}"
        )

    return play_script_macro(
        action
    )


def show_lua_lcd_message(
    text: str,
    timeout_ms: int,
) -> None:
    now = time.monotonic()

    lua_lcd_messages[:] = [
        entry
        for entry in lua_lcd_messages
        if entry[1] > now
    ]

    clean_text = (
        str(text)
        .replace("\r", " ")
        .replace("\n", " ")
    )

    expires_at = (
        now
        + (
            timeout_ms
            / 1000.0
        )
    )

    lua_lcd_messages.append(
        (
            clean_text,
            expires_at,
        )
    )

    if len(lua_lcd_messages) > 4:
        del lua_lcd_messages[:-4]

    refresh_lcd_status()


def clear_lua_lcd() -> None:
    lua_lcd_messages.clear()

    refresh_lcd_status()


def set_lua_backlight_color(
    red: int,
    green: int,
    blue: int,
) -> None:
    if g13_device is None:
        return

    try:
        set_g13_backlight(
            g13_device,
            red,
            green,
            blue,
        )

    except Exception as error:
        print(
            "Lua backlight update failed:",
            error,
        )


def reload_lua_profile() -> None:
    global current_backlight_colors

    lua_runtime.deactivate_profile()

    lua_lcd_messages.clear()

    current_backlight_colors = (
        profile_manager
        .get_backlight_colors()
    )

    apply_backlight(
        get_layer()
    )

    profile_name = (
        profile_manager
        .active_profile_name
    )

    if not profile_name:
        return

    try:
        script = (
            profile_manager
            .get_profile_script()
        )

        lua_runtime.set_m_key_state_callbacks(
            get_layer,
            set_lua_m_key_state,
        )

        lua_runtime.set_keyboard_callbacks(
            press_lua_key,
            release_lua_key,
        )

        lua_runtime.set_macro_callbacks(
            play_lua_macro,
            abort_script_macro,
        )

        lua_runtime.set_input_state_callbacks(
            is_lua_modifier_pressed,
            is_lua_mouse_button_pressed,
        )

        lua_runtime.set_key_lock_state_callback(
            is_lua_key_lock_on
        )

        lua_runtime.set_mouse_callbacks(
            press_lua_mouse_button,
            release_lua_mouse_button,
            move_relative,
            scroll_vertical,
        )

        lua_runtime.set_absolute_mouse_callbacks(
            get_position=(
                get_lua_mouse_position
            ),
            move_primary=(
                move_lua_mouse_to
            ),
            move_virtual=(
                move_lua_mouse_to_virtual
            ),
        )

        lua_runtime.set_g13_callbacks(
            show_lua_lcd_message,
            clear_lua_lcd,
            set_lua_backlight_color,
        )

        lua_runtime.load_profile(
            profile_name,
            script,
        )

    except Exception as error:
        print(
            "Lua profile reload failed:",
            error,
        )

# Remembers the layer in which every physical button went down.
# This lets the GUI release the correct highlighted button even
# if M1/M2/M3 changes before the button is released.
pressed_button_layers = {}

def apply_indicator_leds() -> None:
    if g13_device is None:
        return

    if (
        led_test_until > 0.0
        and time.monotonic()
        < led_test_until
    ):
        mask = 0x0F

    else:
        mask = active_m_led_mask

        if mr_led_active:
            mask |= 0x08

    try:
        set_g13_indicator_leds(
            g13_device,
            mask,
        )

    except Exception as error:
        print(
            "Indicator LED update failed:",
            error,
        )

def apply_backlight(
    layer: int | None = None,
) -> None:
    if g13_device is None:
        return

    if layer is None:
        layer = get_layer()

    color = current_backlight_colors.get(layer)

    if color is None:
        return

    red, green, blue = color

    try:
        set_g13_backlight(
            g13_device,
            red,
            green,
            blue,
        )

    except Exception as error:
        print(
            "Backlight update failed:",
            error,
        )

def _apply_onboard_runtime_status(
    status,
) -> None:
    runtime_profiles = {}

    for onboard_profile in get_onboard_profiles(
        status.current_bank
    ):
        identifier = (
            f"onboard:"
            f"{onboard_profile.slot}"
        )

        if (
            onboard_profile.storage_format
            == "zombie"
        ):
            profile_data = (
                onboard_profile.profile_data
            )

            if profile_data is None:
                raise RuntimeError(
                    f"Onboard slot "
                    f"{onboard_profile.slot} "
                    "has no Zombie profile data."
                )

        elif (
            onboard_profile.storage_format
            == "logitech"
        ):
            if (
                onboard_profile.xml_data
                is None
            ):
                raise RuntimeError(
                    f"Onboard slot "
                    f"{onboard_profile.slot} "
                    "has no Logitech XML data."
                )

            profile_data = convert_profile_xml(
                onboard_profile.xml_data,
                source_name=(
                    f"onboard-slot-"
                    f"{onboard_profile.slot}.xml"
                ),
            )

        else:
            raise RuntimeError(
                "Unsupported onboard profile "
                f"format: "
                f"{onboard_profile.storage_format!r}."
            )

        runtime_profiles[
            identifier
        ] = profile_data

    runtime_profile_controller.set_onboard_profiles(
        runtime_profiles
    )

    if lcd_runtime_ui is not None:
        lcd_runtime_ui.set_onboard_profiles(
            runtime_profiles
        )

    print(
        "Loaded onboard runtime profiles:",
        len(runtime_profiles),
    )


def refresh_onboard_runtime_profiles() -> None:
    global onboard_runtime_profiles_ready

    try:
        status = get_onboard_status()

        write_onboard_cache(
            status.memory_image
        )

        _apply_onboard_runtime_status(
            status
        )

        onboard_runtime_profiles_ready = True

        event_publisher.publish_onboard_profiles_updated()

    except Exception as error:
        onboard_runtime_profiles_ready = False

        print(
            "Onboard runtime profile refresh failed:",
            error,
        )


def reload_onboard_runtime_profiles_from_cache(
) -> None:
    global onboard_runtime_profiles_ready

    try:
        status = get_cached_onboard_status()

        if status is None:
            raise RuntimeError(
                "No onboard profile cache is available."
            )

        _apply_onboard_runtime_status(
            status
        )

        onboard_runtime_profiles_ready = True

        event_publisher.publish_onboard_profiles_updated()

    except Exception as error:
        print(
            "Onboard runtime cache reload failed:",
            error,
        )

def publish_runtime_profile_status() -> None:
    status = (
        runtime_profile_controller
        .get_status()
    )

    event_publisher.publish_runtime_profile(
        active_profile=status.active_profile,
        source=status.source,
        locked_profile=status.locked_profile,
        window_class=status.window_class,
    )

def refresh_lcd_status() -> None:
    if lcd_runtime_ui is None:
        return

    try:
        if macro_recording_lcd_active:
            lcd_runtime_ui.show_macro_recording()
            return

        if (
            lcd_test_until > 0.0
            and time.monotonic()
            < lcd_test_until
        ):
            lcd_runtime_ui.show_lcd_test()

            return

        now = time.monotonic()

        lua_lcd_messages[:] = [
            entry
            for entry in lua_lcd_messages
            if entry[1] > now
        ]

        if lua_lcd_messages:
            lcd_runtime_ui.show_lua_messages(
                [
                    entry[0]
                    for entry
                    in lua_lcd_messages
                ]
            )

            return

        if (
            profile_activation_lcd_profile is not None
            and time.monotonic()
            < profile_activation_lcd_until
        ):
            lcd_runtime_ui.show_profile_activation(
                profile_activation_lcd_profile
            )

            return

        status = (
            runtime_profile_controller
            .get_status()
        )

        lcd_runtime_ui.show_runtime_status(
            status,
            get_layer(),
        )

        event_publisher.publish_lcd_selection(
            lcd_runtime_ui.get_selected_profile(
                status
            )
        )

    except Exception as error:
        print(
            "LCD status update failed:",
            error,
        )

def process_driver_commands() -> None:
    global macro_recording_lcd_active
    global lcd_test_until
    global led_test_until
    global runtime_profile_action_pending

    for command in (
        command_listener.receive_all()
    ):

        command_type = command.get(
            "type"
        )

        if command_type == "macro_recording":
            active = command.get(
                "active"
            )

            if not isinstance(
                active,
                bool,
            ):
                continue

            macro_recording_lcd_active = active

            refresh_lcd_status()

            continue

        if command_type == "test_lcd":
            lcd_test_until = (
                time.monotonic()
                + 2.0
            )

            refresh_lcd_status()

            continue

        if command_type == "test_leds":
            led_test_until = (
                time.monotonic()
                + 2.0
            )

            apply_indicator_leds()

            refresh_lcd_status()

            continue

        if (
            command_type
            == "request_onboard_profiles"
        ):
            if onboard_runtime_profiles_ready:
                event_publisher.publish_onboard_profiles_updated()

            else:
                refresh_onboard_runtime_profiles()

                if onboard_runtime_profiles_ready:
                    if runtime_profile_controller.check():
                        runtime_profile_action_pending = True

                    publish_runtime_profile_status()

                    refresh_lcd_status()

            continue

        if (
            command_type
            == "reload_onboard_profiles_cache"
        ):
            reload_onboard_runtime_profiles_from_cache()

            if runtime_profile_controller.check():
                runtime_profile_action_pending = True

            publish_runtime_profile_status()

            refresh_lcd_status()

            continue

        if (
            command_type
            == "refresh_onboard_profiles"
        ):
            refresh_onboard_runtime_profiles()

            if runtime_profile_controller.check():
                runtime_profile_action_pending = True

            publish_runtime_profile_status()

            refresh_lcd_status()

            continue

def activate_m_state(
    layer: int,
    source: str = "physical",
) -> None:
    global active_m_led_mask

    if layer not in {
        1,
        2,
        3,
    }:
        return

    set_layer(
        layer
    )

    active_m_led_mask = (
        M_LED_MASKS[layer]
    )

    apply_backlight(
        layer
    )

    apply_indicator_leds()

    refresh_lcd_status()

    event_publisher.publish_layer(
        layer
    )

    if source == "lua":
        print(
            f"M{layer} ACTIVE (Lua)"
        )

    else:
        print(
            f"M{layer} ACTIVE"
        )

pending_lua_events = []


def queue_lua_event(
    event: str,
    arg=None,
    family=None,
) -> None:
    pending_lua_events.append(
        (
            event,
            arg,
            family,
        )
    )


def process_pending_lua_events() -> None:
    if not pending_lua_events:
        return

    queued_events = (
        pending_lua_events[:]
    )

    pending_lua_events.clear()

    for (
        event,
        arg,
        family,
    ) in queued_events:
        lua_runtime.dispatch(
            event,
            arg,
            family,
        )



def set_lua_m_key_state(
    layer: int,
) -> None:
    if layer == get_layer():
        return

    activate_m_state(
        layer,
        "lua",
    )

    queue_lua_event(
        "M_PRESSED",
        layer,
        "lhc",
    )



def handle_button(button, pressed):
    global active_m_led_mask
    global mr_led_active
    global runtime_profile_action_pending

    layer = LAYER_BUTTONS.get(button)

    if layer is not None:
        if pressed:
            activate_m_state(
                layer
            )

            lua_runtime.dispatch(
                "M_PRESSED",
                layer,
                "lhc",
            )

        else:
            lua_runtime.dispatch(
                "M_RELEASED",
                layer,
                "lhc",
            )

        return

    if button in {
        "LCD1",
        "LCD2",
        "LCD3",
        "LCD4",
    }:
        # Ignore LCD-button release events.
        if not pressed:
            return

        if lcd_runtime_ui is None:
            return

        status = (
            runtime_profile_controller
            .get_status()
        )

        if button == "LCD1":
            lcd_runtime_ui.move_selection(
                -1,
                status,
                get_layer(),
            )

            event_publisher.publish_lcd_selection(
                lcd_runtime_ui.get_selected_profile(
                    status
                )
            )

        elif button == "LCD2":
            lcd_runtime_ui.move_selection(
                1,
                status,
                get_layer(),
            )

            event_publisher.publish_lcd_selection(
                lcd_runtime_ui.get_selected_profile(
                    status
                )
            )

        elif button == "LCD3":
            selected_profile = (
                lcd_runtime_ui
                .get_selected_profile(status)
            )

            runtime_profile_controller.select_temporary_profile(
                selected_profile
            )

            lcd_runtime_ui.follow_runtime_profile()

            runtime_profile_action_pending = True

            publish_runtime_profile_status()
            refresh_lcd_status()

        elif button == "LCD4":
            selected_profile = (
                lcd_runtime_ui
                .get_selected_profile(status)
            )

            if (
                status.locked_profile
                == selected_profile
            ):
                runtime_profile_controller.unlock_profile()
            else:
                runtime_profile_controller.lock_profile(
                    selected_profile
                )

            runtime_profile_action_pending = True

            publish_runtime_profile_status()
            refresh_lcd_status()

        return

    # MR is reserved for macro recording in the editor.
    if button == "MR":
        active_layer = get_layer()

        if pressed:
            mr_led_active = (
                not mr_led_active
            )

            apply_indicator_leds()

        event_publisher.publish_button(
            "MR",
            pressed,
            active_layer,
        )

        state = (
            "DOWN"
            if pressed
            else "UP"
        )

        print(f"MR {state}")
        return

    if (
        isinstance(button, str)
        and button.startswith("G")
        and button[1:].isdigit()
    ):
        lua_event = (
            "G_PRESSED"
            if pressed
            else "G_RELEASED"
        )

        lua_runtime.dispatch(
            lua_event,
            int(button[1:]),
            "lhc",
        )


    source_id = None

    if pressed:
        active_layer = get_layer()

        pressed_button_layers[button] = (
            active_layer
        )

        event_publisher.publish_button(
            button,
            True,
            active_layer,
        )

        action = profile_manager.get_binding(
            active_layer,
            button,
        )

        source_id = (
            f"M{active_layer}:{button}"
        )

        if action is not None:
            pressed_actions[button] = (
                action,
                source_id,
            )

    else:
        active_layer = pressed_button_layers.pop(
            button,
            get_layer(),
        )

        event_publisher.publish_button(
            button,
            False,
            active_layer,
        )

        pressed_entry = pressed_actions.pop(
            button,
            None,
        )

        if pressed_entry is None:
            action = None
        else:
            action, source_id = pressed_entry

    if action is None:
        state = (
            "DOWN"
            if pressed
            else "UP"
        )

        print(f"{button} {state}")
        return

    state = (
        "DOWN"
        if pressed
        else "UP"
    )

    print(
        f"{button} -> "
        f"{action['name']} "
        f"{state}"
    )

    execute_action(
        action,
        pressed,
        source_id=source_id,
    )


def main():
    global profile_activation_lcd_profile
    global profile_activation_lcd_until
    global lcd_test_until
    global led_test_until
    global g13_device
    global current_backlight_colors
    global active_m_led_mask
    global mr_led_active
    global lcd_runtime_ui
    global runtime_profile_action_pending

    dev = None
    joystick = None
    lcd = None

    try:
        if driver_lifecycle.stop_requested:
            return

        print("Virtual keyboard created.")

# Open the physical G13.
        dev = open_g13()
        g13_device = dev
        lcd = G13LCD(dev)
        lcd.initialize()
        lcd.show_zombie()

        lcd_runtime_ui = LCDRuntimeUI(
            lcd,
            profile_manager,
        )

        print("G13 LCD initialized with ZOMBIE splash.")

        refresh_onboard_runtime_profiles()

        if driver_lifecycle.stop_requested:
            return

        runtime_profile_controller.check()

        publish_runtime_profile_status()
        reload_lua_profile()

        event_publisher.publish_layer(
            get_layer()
        )

# Load and apply the current profile's M1 color.
        current_backlight_colors = (
            profile_manager.get_backlight_colors()
        )

        apply_backlight(get_layer())

        active_m_led_mask = M_LED_MASKS.get(
            get_layer(),
            0x01,
        )

        mr_led_active = False

        apply_indicator_leds()

        joystick = JoystickController()

        current_joystick_settings = (
            profile_manager
            .get_joystick_settings()
        )

        decoder = G13Decoder(
            handle_button,
            joystick,
            current_joystick_settings,
        )

        last_reload_check = 0.0

        last_runtime_status = (
            runtime_profile_controller.get_status()
        )

        last_suspend_offset = (
            _suspend_clock_offset()
        )

        time.sleep(1.2)
        refresh_lcd_status()

        print("G13 FINAL DECODER ACTIVE\n")

        while not driver_lifecycle.stop_requested:
            process_pending_lua_events()

            if driver_lifecycle.stop_requested:
                break

            current_suspend_offset = (
                _suspend_clock_offset()
            )

            if (
                last_suspend_offset is not None
                and current_suspend_offset is not None
                and (
                    current_suspend_offset
                    - last_suspend_offset
                )
                >= G13_RESUME_GAP_SECONDS
            ):
                print(
                    "System resume detected; "
                    "reopening G13."
                )

                (
                    dev,
                    lcd,
                    joystick,
                    decoder,
                ) = _reconnect_g13_hardware(
                    dev,
                    joystick,
                    current_joystick_settings,
                )

                last_suspend_offset = (
                    current_suspend_offset
                )

                if dev is None:
                    break

                continue

            last_suspend_offset = (
                current_suspend_offset
            )

            try:
                data = dev.read(
                    8,
                    timeout=50,
                )

            except HIDException as error:
                # A stop signal can interrupt the native HID read.
                #
                # HIDAPI 0.15 separates read errors from the
                # general hid_error() state. pyhidapi 1.0.9
                # still queries hid_error(), which can surface
                # the interrupted read as the misleading text
                # "Success".
                if driver_lifecycle.stop_requested:
                    if str(error) in {
                        os.strerror(EINTR),
                        "Success",
                    }:
                        break

                    raise

                print(
                    "G13 HID read failed:",
                    error,
                )

                (
                    dev,
                    lcd,
                    joystick,
                    decoder,
                ) = _reconnect_g13_hardware(
                    dev,
                    joystick,
                    current_joystick_settings,
                )

                last_suspend_offset = (
                    _suspend_clock_offset()
                )

                if dev is None:
                    break

                continue

            if driver_lifecycle.stop_requested:
                break

            process_driver_commands()

            if driver_lifecycle.stop_requested:
                break

            now = time.monotonic()

            if lua_lcd_messages:
                message_count = len(
                    lua_lcd_messages
                )

                lua_lcd_messages[:] = [
                    entry
                    for entry
                    in lua_lcd_messages
                    if entry[1] > now
                ]

                if (
                    len(lua_lcd_messages)
                    != message_count
                ):
                    refresh_lcd_status()


            if (
                led_test_until > 0.0
                and now >= led_test_until
            ):
                led_test_until = 0.0

                apply_indicator_leds()

            if (
                lcd_test_until > 0.0
                and now >= lcd_test_until
            ):
                lcd_test_until = 0.0

                refresh_lcd_status()

            if (
                profile_activation_lcd_profile is not None
                and now
                >= profile_activation_lcd_until
            ):
                profile_activation_lcd_profile = None
                profile_activation_lcd_until = 0.0

                refresh_lcd_status()

            if now - last_reload_check >= 0.25:
                last_reload_check = now

                try:
                    files_changed = (
                        profile_manager
                        .reload_if_changed()
                    )

                    if (
                        profile_activation_lcd_profile is not None
                        and not profile_manager
                        .get_show_profile_activation_on_lcd()
                    ):
                        profile_activation_lcd_profile = None
                        profile_activation_lcd_until = 0.0

                        refresh_lcd_status()


                    runtime_profile_changed = (
                        runtime_profile_controller
                        .check()
                    )

                    force_runtime_refresh = (
                        runtime_profile_action_pending
                    )

                    runtime_profile_action_pending = False

                    current_runtime_status = (
                        runtime_profile_controller
                        .get_status()
                    )

                    runtime_status_changed = (
                        current_runtime_status
                        != last_runtime_status
                    )

                    active_profile_changed = (
                        current_runtime_status.active_profile
                        != last_runtime_status.active_profile
                    )

                    if (
                        active_profile_changed
                        and profile_manager
                        .get_show_profile_activation_on_lcd()
                    ):
                        profile_activation_lcd_profile = (
                            current_runtime_status
                            .active_profile
                        )

                        profile_activation_lcd_until = (
                            time.monotonic()
                            + 5.0
                        )

                    if runtime_status_changed:
                        last_runtime_status = (
                            current_runtime_status
                        )

                    publish_runtime_profile_status()

                    event_publisher.publish_layer(
                        get_layer()
                    )

                    # Reload profile-owned hardware/settings only
                    # when the profile or its files changed.
                    if (
                        files_changed
                        or runtime_profile_changed
                        or force_runtime_refresh
                    ):
                        reload_lua_profile()

                        updated_backlight_colors = (
                            profile_manager
                            .get_backlight_colors()
                        )

                        if (
                            updated_backlight_colors
                            != current_backlight_colors
                        ):
                            current_backlight_colors = (
                                updated_backlight_colors
                            )

                            apply_backlight(
                                get_layer()
                            )

                            print(
                                "Backlight colors reloaded."
                            )

                        updated_joystick_settings = (
                            profile_manager
                            .get_joystick_settings()
                        )

                        if (
                            updated_joystick_settings
                            != current_joystick_settings
                        ):
                            decoder.update_joystick_settings(
                                updated_joystick_settings
                            )

                            current_joystick_settings = (
                                updated_joystick_settings
                            )

                            print(
                                "Joystick settings reloaded."
                            )

                    # Refresh for status changes or new contents
                    # applied to the same onboard profile.
                    if (
                        files_changed
                        or runtime_profile_changed
                        or runtime_status_changed
                        or force_runtime_refresh
                    ):
                        refresh_lcd_status()

                except Exception as error:
                    print(
                        "Live reload failed:",
                        error,
                    )

            if not data:
                continue

            decoder.decode(data)

    except KeyboardInterrupt:
        print("\nStopping G13 driver...")

    finally:
        if driver_lifecycle.stop_requested:
            print(
                "\nStopping G13 driver "
                f"({driver_lifecycle.stop_reason})..."
            )

        lua_runtime.deactivate_profile()

        # Release actions using the exact binding selected
        # when each physical button went down.
        for (
            action,
            source_id,
        ) in list(
            pressed_actions.values()
        ):
            execute_action(
                action,
                False,
                source_id=source_id,
            )

        pressed_actions.clear()
        pressed_button_layers.clear()

        stopped_macros = stop_all_macros()

        if stopped_macros:
            print(
                f"Stopped {stopped_macros} "
                f"running macro(s)."
            )

        released_keycodes = release_all_keys()

        if released_keycodes:
            print(
                "Safety-released virtual keycodes:",
                released_keycodes,
            )
        else:
            print(
                "Virtual keyboard clean: "
                "no keys held."
            )

        released_buttons = release_all_buttons()

        if released_buttons:
            print(
                "Safety-released virtual "
                "mouse buttons:",
                released_buttons,
            )
        else:
            print(
                "Virtual mouse clean: "
                "no buttons held."
            )

        if joystick is not None:
            joystick.stop()

        if dev is not None:
            try:
                set_g13_indicator_leds(
                    dev,
                    0x00,
                )

            except Exception as error:
                print(
                    "Indicator LED shutdown failed:",
                    error,
                )

        if lcd is not None:
            try:
                lcd.clear()

            except Exception as error:
                print(
                    "LCD shutdown clear failed:",
                    error,
                )

                lcd_runtime_ui = None

        if dev is not None:
            close = getattr(
                dev,
                "close",
                None,
            )

            if callable(close):
                close()

        g13_device = None

        try:
            absolute_pointer_backend.close()

        except Exception as error:
            print(
                "Absolute pointer backend "
                f"shutdown failed: {error}"
            )

        command_listener.close()
        event_publisher.close()

if __name__ == "__main__":
    main()
