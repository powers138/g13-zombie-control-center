from collections import defaultdict

from evdev import UInput, ecodes as e


SUPPORTED_BUTTONS = [
    e.BTN_LEFT,
    e.BTN_MIDDLE,
    e.BTN_RIGHT,
    e.BTN_SIDE,
    e.BTN_EXTRA,
]


BUTTON_MAP = {
    "left": e.BTN_LEFT,
    "middle": e.BTN_MIDDLE,
    "right": e.BTN_RIGHT,
    "back": e.BTN_SIDE,
    "forward": e.BTN_EXTRA,
}


ui = UInput(
    {
        e.EV_KEY: SUPPORTED_BUTTONS,
        e.EV_REL: [
            e.REL_X,
            e.REL_Y,
            e.REL_WHEEL,
            e.REL_HWHEEL,
        ],
    },
    name="G13 Virtual Mouse",
)


# Number of bindings currently holding each mouse button.
_held_button_counts = defaultdict(int)


def send_button(button, pressed):
    if button not in BUTTON_MAP:
        raise ValueError(
            f"Unsupported mouse button: {button}"
        )

    button_code = BUTTON_MAP[button]

    if pressed:
        _held_button_counts[button_code] += 1

        # Send DOWN only for the first holder.
        if _held_button_counts[button_code] == 1:
            ui.write(e.EV_KEY, button_code, 1)
            ui.syn()

        return

    # Ignore duplicate release events.
    if _held_button_counts[button_code] == 0:
        return

    _held_button_counts[button_code] -= 1

    # Send UP only after the final holder releases it.
    if _held_button_counts[button_code] == 0:
        ui.write(e.EV_KEY, button_code, 0)
        ui.syn()

def is_button_held(
    button: str,
) -> bool:
    if button not in BUTTON_MAP:
        raise ValueError(
            f"Unsupported mouse button: {button}"
        )

    button_code = (
        BUTTON_MAP[button]
    )

    return (
        _held_button_counts[
            button_code
        ]
        > 0
    )

def release_all_buttons():
    """
    Release every virtual mouse button still marked as held.

    Returns the names of buttons that required a safety release.
    An empty list means the virtual mouse was already clean.
    """
    code_to_name = {
        code: name
        for name, code in BUTTON_MAP.items()
    }

    held_codes = [
        code
        for code, count in _held_button_counts.items()
        if count > 0
    ]

    for code in held_codes:
        ui.write(e.EV_KEY, code, 0)

    if held_codes:
        ui.syn()

    _held_button_counts.clear()

    return [
        code_to_name.get(code, str(code))
        for code in held_codes
    ]


def scroll_vertical(amount):
    ui.write(e.EV_REL, e.REL_WHEEL, amount)
    ui.syn()


def scroll_horizontal(amount):
    ui.write(e.EV_REL, e.REL_HWHEEL, amount)
    ui.syn()


def move_relative(dx, dy):
    if dx:
        ui.write(e.EV_REL, e.REL_X, dx)

    if dy:
        ui.write(e.EV_REL, e.REL_Y, dy)

    if dx or dy:
        ui.syn()
