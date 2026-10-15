from __future__ import annotations


# Logitech G-series Lua API V3.02, Appendix A.
#
# These are Logitech's numeric scancodes used by:
#
#     PressKey()
#     ReleaseKey()
#     PressAndReleaseKey()
#
# Translate them to Logitech's corresponding key names so the
# existing Linux key-name resolver remains the single place that
# converts key names into Linux input codes.

LOGITECH_SCANCODE_TO_KEYNAME = {
    0x01: "escape",

    0x3B: "f1",
    0x3C: "f2",
    0x3D: "f3",
    0x3E: "f4",
    0x3F: "f5",
    0x40: "f6",
    0x41: "f7",
    0x42: "f8",
    0x43: "f9",
    0x44: "f10",
    0x57: "f11",
    0x58: "f12",
    0x64: "f13",
    0x65: "f14",
    0x66: "f15",
    0x67: "f16",
    0x68: "f17",
    0x69: "f18",
    0x6A: "f19",
    0x6B: "f20",
    0x6C: "f21",
    0x6D: "f22",
    0x6E: "f23",
    0x76: "f24",

    0x137: "printscreen",
    0x46: "scrolllock",
    0x146: "pause",

    0x29: "tilde",
    0x02: "1",
    0x03: "2",
    0x04: "3",
    0x05: "4",
    0x06: "5",
    0x07: "6",
    0x08: "7",
    0x09: "8",
    0x0A: "9",
    0x0B: "0",
    0x0C: "minus",
    0x0D: "equal",
    0x0E: "backspace",
    0x0F: "tab",

    0x10: "q",
    0x11: "w",
    0x12: "e",
    0x13: "r",
    0x14: "t",
    0x15: "y",
    0x16: "u",
    0x17: "i",
    0x18: "o",
    0x19: "p",
    0x1A: "lbracket",
    0x1B: "rbracket",
    0x2B: "backslash",

    0x3A: "capslock",

    0x1E: "a",
    0x1F: "s",
    0x20: "d",
    0x21: "f",
    0x22: "g",
    0x23: "h",
    0x24: "j",
    0x25: "k",
    0x26: "l",
    0x27: "semicolon",
    0x28: "quote",
    0x1C: "enter",

    0x2A: "lshift",
    0x56: "non_us_slash",

    0x2C: "z",
    0x2D: "x",
    0x2E: "c",
    0x2F: "v",
    0x30: "b",
    0x31: "n",
    0x32: "m",
    0x33: "comma",
    0x34: "period",
    0x35: "slash",
    0x36: "rshift",

    0x1D: "lctrl",
    0x15B: "lgui",
    0x38: "lalt",
    0x39: "spacebar",
    0x138: "ralt",
    0x15C: "rgui",
    0x15D: "appkey",
    0x11D: "rctrl",

    0x152: "insert",
    0x147: "home",
    0x149: "pageup",
    0x153: "delete",
    0x14F: "end",
    0x151: "pagedown",
    0x148: "up",
    0x14B: "left",
    0x150: "down",
    0x14D: "right",

    0x45: "numlock",
    0x135: "numslash",
    0x37: "numstar",
    0x4A: "numminus",
    0x47: "num7",
    0x48: "num8",
    0x49: "num9",
    0x4E: "numplus",
    0x4B: "num4",
    0x4C: "num5",
    0x4D: "num6",
    0x4F: "num1",
    0x50: "num2",
    0x51: "num3",
    0x11C: "numenter",
    0x52: "num0",
    0x53: "numperiod",
}


def normalize_logitech_key(
    key,
):
    """
    Convert a Logitech numeric Lua scancode to its documented
    Logitech key name.

    Existing textual key names pass through unchanged.
    """
    if isinstance(key, bool):
        raise ValueError(
            "Keyboard key cannot be a boolean."
        )

    if isinstance(key, str):
        return key

    if isinstance(key, (int, float)):
        if (
            isinstance(key, float)
            and not key.is_integer()
        ):
            raise ValueError(
                "Keyboard scancode must "
                "be a whole number."
            )

        scancode = int(key)

        key_name = (
            LOGITECH_SCANCODE_TO_KEYNAME.get(
                scancode
            )
        )

        if key_name is None:
            raise ValueError(
                "Unsupported Logitech "
                f"scancode: 0x{scancode:X}"
            )

        return key_name

    raise ValueError(
        "Keyboard key must be a Logitech "
        "key name or numeric scancode."
    )
