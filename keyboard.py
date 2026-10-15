from collections import defaultdict

from evdev import UInput, ecodes as e


# Automatically advertise every normal Linux keyboard key.
# This includes letters, numbers, function keys, keypad keys,
# media keys, navigation keys, and modifiers.
SUPPORTED_KEYS = sorted({
    code
    for name, code in vars(e).items()
    if (
        name.startswith("KEY_")
        and isinstance(code, int)
        and code > 0
        and name not in {
            "KEY_MAX",
            "KEY_CNT",
        }
    )
})


ui = UInput(
    {
        e.EV_KEY: SUPPORTED_KEYS,
    },
    name="G13 Virtual Keyboard",
)


# Tracks how many bindings currently hold each virtual key.
# This prevents one binding from releasing a key that another
# binding is still holding.
_held_key_counts = defaultdict(int)


def _set_key_state(keycode: int, pressed: bool) -> None:
    if pressed:
        _held_key_counts[keycode] += 1

        # Emit DOWN only for the first holder.
        if _held_key_counts[keycode] == 1:
            ui.write(e.EV_KEY, keycode, 1)
            ui.syn()

        return

    if _held_key_counts[keycode] == 0:
        return

    _held_key_counts[keycode] -= 1

    # Emit UP only after the final holder releases it.
    if _held_key_counts[keycode] == 0:
        ui.write(e.EV_KEY, keycode, 0)
        ui.syn()


def send_key(keycode: int, pressed: bool) -> None:
    _set_key_state(keycode, pressed)


def send_chord(
    keycode: int,
    modifiers: list[int],
    pressed: bool,
) -> None:
    """
    Send a key together with zero or more modifiers.

    Press order:
        modifiers down, then key down

    Release order:
        key up, then modifiers up
    """
    if pressed:
        for modifier in modifiers:
            _set_key_state(modifier, True)

        _set_key_state(keycode, True)
        return

    _set_key_state(keycode, False)

    for modifier in reversed(modifiers):
        _set_key_state(modifier, False)

def is_key_held(
    keycode: int,
) -> bool:
    return (
        _held_key_counts[keycode]
        > 0
    )

def release_all_keys() -> list[int]:
    """
    Release every virtual key still marked as held.

    Returns the keycodes that required a safety release.
    An empty list means the virtual keyboard was already clean.
    """
    held_keycodes = [
        keycode
        for keycode, count in _held_key_counts.items()
        if count > 0
    ]

    for keycode in held_keycodes:
        ui.write(e.EV_KEY, keycode, 0)

    if held_keycodes:
        ui.syn()

    _held_key_counts.clear()

    return held_keycodes
