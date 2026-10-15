from __future__ import annotations

import argparse
import json
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


PROFILE_NAMESPACE = (
    "http://www.logitech.com/"
    "Cassandra/2010.7/Profile"
)

KEYSTROKE_NAMESPACE = (
    "http://www.logitech.com/"
    "Cassandra/2010.1/Macros/Keystroke"
)

MULTIKEY_NAMESPACE = (
    "http://www.logitech.com/"
    "Cassandra/2010.1/Macros/MultiKey"
)

MOUSE_NAMESPACE = (
    "http://www.logitech.com/"
    "Cassandra/2010.1/Macros/MouseFunction"
)

G13_DEVICE_CATEGORY = (
    "Logitech.Gaming.LeftHandedController"
)

G13_DEVICE_MODEL = (
    "Logitech.Gaming.LeftHandedController.G13"
)

DEFAULT_MACRO_COLOR = "4278246655"

GUID_PATTERN = re.compile(
    r"\{?"
    r"([0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12})"
    r"\}?"
)


LINUX_TO_LOGITECH_KEY = {
    "KEY_SPACE": "SPACEBAR",
    "KEY_ESC": "ESCAPE",

    "KEY_LEFTCTRL": "LCTRL",
    "KEY_RIGHTCTRL": "RCTRL",
    "KEY_LEFTSHIFT": "LSHIFT",
    "KEY_RIGHTSHIFT": "RSHIFT",
    "KEY_LEFTALT": "LALT",
    "KEY_RIGHTALT": "RALT",
    "KEY_LEFTMETA": "LGUI",
    "KEY_RIGHTMETA": "RGUI",
    "KEY_MENU": "APPKEY",

    "KEY_ENTER": "ENTER",
    "KEY_TAB": "TAB",
    "KEY_BACKSPACE": "BACKSPACE",
    "KEY_DELETE": "DELETE",
    "KEY_INSERT": "INSERT",

    "KEY_CAPSLOCK": "CAPSLOCK",
    "KEY_NUMLOCK": "NUMLOCK",
    "KEY_SCROLLLOCK": "SCROLLLOCK",
    "KEY_SYSRQ": "PRINTSCREEN",
    "KEY_PAUSE": "PAUSE",

    "KEY_HOME": "HOME",
    "KEY_END": "END",
    "KEY_PAGEUP": "PAGEUP",
    "KEY_PAGEDOWN": "PAGEDOWN",

    "KEY_UP": "UP",
    "KEY_DOWN": "DOWN",
    "KEY_LEFT": "LEFT",
    "KEY_RIGHT": "RIGHT",

    "KEY_MINUS": "MINUS",
    "KEY_EQUAL": "EQUAL",
    "KEY_APOSTROPHE": "QUOTE",
    "KEY_GRAVE": "TILDE",
    "KEY_COMMA": "COMMA",
    "KEY_DOT": "PERIOD",
    "KEY_SLASH": "SLASH",
    "KEY_SEMICOLON": "SEMICOLON",
    "KEY_LEFTBRACE": "LBRACKET",
    "KEY_RIGHTBRACE": "RBRACKET",
    "KEY_BACKSLASH": "BACKSLASH",
    "KEY_102ND": "NON_US_SLASH",

    "KEY_KP0": "NUM0",
    "KEY_KP1": "NUM1",
    "KEY_KP2": "NUM2",
    "KEY_KP3": "NUM3",
    "KEY_KP4": "NUM4",
    "KEY_KP5": "NUM5",
    "KEY_KP6": "NUM6",
    "KEY_KP7": "NUM7",
    "KEY_KP8": "NUM8",
    "KEY_KP9": "NUM9",

    "KEY_KPASTERISK": "NUMSTAR",
    "KEY_KPMINUS": "NUMMINUS",
    "KEY_KPPLUS": "NUMPLUS",
    "KEY_KPDOT": "NUMPERIOD",
    "KEY_KPENTER": "NUMENTER",
    "KEY_KPSLASH": "NUMSLASH",
}


MOUSE_BUTTON_TO_TASK = {
    "left": "leftclick",
    "right": "rightclick",
    "middle": "middleclick",
    "back": "back",
    "forward": "forward",
}


EXECUTION_MODE_TO_LOGITECH = {
    "once": None,
    "while_held": "pressed",
    "toggle": "toggle",
}


def logitech_key_name(
    linux_name: str,
) -> str:
    name = linux_name.strip().upper()

    special = LINUX_TO_LOGITECH_KEY.get(
        name
    )

    if special is not None:
        return special

    if not name.startswith(
        "KEY_"
    ):
        raise ValueError(
            f"Unsupported Linux key name: "
            f"{linux_name}"
        )

    short_name = name[4:]

    if (
        len(short_name) == 1
        and short_name.isalnum()
    ):
        return short_name

    if (
        short_name.startswith("F")
        and short_name[1:].isdigit()
    ):
        return short_name

    raise ValueError(
        f"Unsupported Linux key name: "
        f"{linux_name}"
    )


def normalize_guid(
    value: str | None,
    seed: str,
) -> str:
    if value:
        match = GUID_PATTERN.fullmatch(
            value.strip()
        )

        if match is not None:
            parsed = uuid.UUID(
                match.group(1)
            )

            return (
                "{"
                + str(parsed).upper()
                + "}"
            )

    generated = uuid.uuid5(
        uuid.NAMESPACE_URL,
        seed,
    )

    return (
        "{"
        + str(generated).upper()
        + "}"
    )


def profile_guid(
    profile: dict[str, Any],
    input_file: Path,
) -> str:
    return (
        "{"
        + str(uuid.uuid4()).upper()
        + "}"
    )


def action_fingerprint(
    action: dict[str, Any],
) -> str:
    clean_action = {
        key: value
        for key, value in action.items()
        if key != "command_id"
    }

    return json.dumps(
        clean_action,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    )


def format_backlight_color(
    value: Any,
) -> str:
    if isinstance(
        value,
        str,
    ):
        color = value.strip()

        if re.fullmatch(
            r"#[0-9a-fA-F]{6}",
            color,
        ):
            return color.lower()

    if (
        isinstance(
            value,
            (
                list,
                tuple,
            ),
        )
        and len(value) == 3
    ):
        channels = []

        for channel in value:
            if (
                not isinstance(
                    channel,
                    int,
                )
                or isinstance(
                    channel,
                    bool,
                )
                or not 0 <= channel <= 255
            ):
                break

            channels.append(
                channel
            )

        if len(channels) == 3:
            return (
                "#"
                + "".join(
                    f"{channel:02x}"
                    for channel in channels
                )
            )

    raise ValueError(
        f"Unsupported backlight color: "
        f"{value!r}"
    )


def add_keyboard_macro(
    macro: ET.Element,
    action: dict[str, Any],
) -> None:
    key_name = logitech_key_name(
        str(
            action.get(
                "key",
                "",
            )
        )
    )

    keystroke = ET.SubElement(
        macro,
        "keystroke",
        {
            "xmlns": KEYSTROKE_NAMESPACE,
        },
    )

    ET.SubElement(
        keystroke,
        "key",
        {
            "value": key_name,
        },
    )

    modifiers = action.get(
        "modifiers",
        [],
    )

    if not isinstance(
        modifiers,
        list,
    ):
        raise ValueError(
            "Keyboard modifiers must be a list."
        )

    for modifier in modifiers:
        ET.SubElement(
            keystroke,
            "modifier",
            {
                "value": logitech_key_name(
                    str(modifier)
                ),
            },
        )


def add_multikey_macro(
    macro: ET.Element,
    action: dict[str, Any],
) -> None:
    if bool(
        action.get(
            "allow_overlap",
            False,
        )
    ):
        raise ValueError(
            f"Macro "
            f"{action.get('name', 'Unnamed')!r} "
            "allows overlapping execution, which "
            "Logitech Gaming Software cannot "
            "represent safely."
        )

    execution_mode = str(
        action.get(
            "execution_mode",
            "once",
        )
    )

    if (
        execution_mode
        not in EXECUTION_MODE_TO_LOGITECH
    ):
        raise ValueError(
            f"Unsupported macro execution mode: "
            f"{execution_mode!r}"
        )

    repeat_mode = (
        EXECUTION_MODE_TO_LOGITECH[
            execution_mode
        ]
    )

    repeat_delay = action.get(
        "repeat_delay_ms",
        25,
    )

    if (
        not isinstance(
            repeat_delay,
            int,
        )
        or isinstance(
            repeat_delay,
            bool,
        )
        or repeat_delay < 1
    ):
        raise ValueError(
            "Macro repeat delay must be "
            "a positive integer."
        )

    if repeat_mode is not None:
        macro.set(
            "repeatmode",
            repeat_mode,
        )

    if (
        repeat_mode is not None
        or repeat_delay != 25
    ):
        macro.set(
            "repeatdelay",
            str(repeat_delay),
        )

    multikey = ET.SubElement(
        macro,
        "multikey",
        {
            "xmlns": MULTIKEY_NAMESPACE,
        },
    )

    steps = action.get(
        "steps",
        [],
    )

    if not isinstance(
        steps,
        list,
    ):
        raise ValueError(
            "Macro steps must be a list."
        )

    if not steps:
        raise ValueError(
            f"Macro "
            f"{action.get('name', 'Unnamed')!r} "
            "contains no steps."
        )

    for step in steps:
        if not isinstance(
            step,
            dict,
        ):
            raise ValueError(
                "Macro step must be an object."
            )

        step_type = step.get(
            "type"
        )

        if step_type == "key_press":
            key_value = logitech_key_name(
                str(
                    step.get(
                        "key",
                        "",
                    )
                )
            )

            duration_ms = step.get(
                "duration_ms",
                35,
            )

            if (
                not isinstance(
                    duration_ms,
                    int,
                )
                or isinstance(
                    duration_ms,
                    bool,
                )
                or duration_ms < 0
            ):
                raise ValueError(
                    "Key press duration must be a "
                    "non-negative integer."
                )

            ET.SubElement(
                multikey,
                "key",
                {
                    "direction": "down",
                    "value": key_value,
                },
            )

            ET.SubElement(
                multikey,
                "delay",
                {
                    "milliseconds": str(
                        duration_ms
                    ),
                },
            )

            ET.SubElement(
                multikey,
                "key",
                {
                    "direction": "up",
                    "value": key_value,
                },
            )

            continue

        if step_type in {
            "key_down",
            "key_up",
        }:
            direction = (
                "down"
                if step_type == "key_down"
                else "up"
            )

            ET.SubElement(
                multikey,
                "key",
                {
                    "direction": direction,
                    "value": logitech_key_name(
                        str(
                            step.get(
                                "key",
                                "",
                            )
                        )
                    ),
                },
            )

            continue

        if step_type == "delay":
            milliseconds = step.get(
                "milliseconds"
            )

            if (
                not isinstance(
                    milliseconds,
                    int,
                )
                or isinstance(
                    milliseconds,
                    bool,
                )
                or milliseconds < 0
            ):
                raise ValueError(
                    "Macro delay must be a "
                    "non-negative integer."
                )

            ET.SubElement(
                multikey,
                "delay",
                {
                    "milliseconds": str(
                        milliseconds
                    ),
                },
            )

            continue

        raise ValueError(
            f"Unsupported macro step type: "
            f"{step_type!r}"
        )


def add_mouse_macro(
    macro: ET.Element,
    action: dict[str, Any],
) -> None:
    button = str(
        action.get(
            "button",
            "",
        )
    ).strip().lower()

    task = MOUSE_BUTTON_TO_TASK.get(
        button
    )

    if task is None:
        raise ValueError(
            f"Unsupported Logitech mouse button: "
            f"{button!r}"
        )

    mouse_function = ET.SubElement(
        macro,
        "mousefunction",
        {
            "xmlns": MOUSE_NAMESPACE,
        },
    )

    ET.SubElement(
        mouse_function,
        "do",
        {
            "task": task,
        },
    )

def add_wheel_macro(
    macro: ET.Element,
    action: dict[str, Any],
) -> None:
    direction = str(
        action.get(
            "direction",
            "",
        )
    ).strip().lower()

    amount = action.get(
        "amount"
    )

    if (
        not isinstance(
            amount,
            int,
        )
        or isinstance(
            amount,
            bool,
        )
    ):
        raise ValueError(
            "Wheel amount must be an integer."
        )

    if (
        amount == 0
        or abs(amount) > 20
    ):
        raise ValueError(
            "Wheel amount must be from "
            "-20 through 20, excluding zero."
        )

    if direction not in {
        "vertical",
        "horizontal",
    }:
        raise ValueError(
            "Unsupported Logitech wheel direction: "
            f"{direction!r}"
        )

    if (
        direction == "horizontal"
        and abs(amount) != 1
    ):
        raise ValueError(
            "Logitech export currently supports "
            "horizontal wheel amounts of "
            "+1 or -1 only."
        )

    if (
        direction == "vertical"
        and abs(amount) > 1
    ):
        multikey = ET.SubElement(
            macro,
            "multikey",
            {
                "xmlns": MULTIKEY_NAMESPACE,
            },
        )

        delta = (
            "1"
            if amount > 0
            else "-1"
        )

        for _ in range(
            abs(amount)
        ):
            ET.SubElement(
                multikey,
                "mousewheel",
                {
                    "delta": delta,
                },
            )

        return

    if direction == "vertical":
        task = (
            "scrollup"
            if amount > 0
            else "scrolldown"
        )

    else:
        task = (
            "scrollright"
            if amount > 0
            else "scrollleft"
        )

    mouse_function = ET.SubElement(
        macro,
        "mousefunction",
        {
            "xmlns": MOUSE_NAMESPACE,
        },
    )

    ET.SubElement(
        mouse_function,
        "do",
        {
            "task": task,
        },
    )

def add_macro(
    macros_element: ET.Element,
    guid: str,
    action: dict[str, Any],
    original: bool,
) -> None:
    action_type = action.get(
        "type"
    )

    macro = ET.SubElement(
        macros_element,
        "macro",
        {
            "name": str(
                action.get(
                    "name",
                    "Unnamed",
                )
            ),
            "guid": guid,
            "color": DEFAULT_MACRO_COLOR,
            "hidden": "false",
            "original": (
                "true"
                if original
                else "false"
            ),
        },
    )

    if action_type == "keyboard":
        add_keyboard_macro(
            macro,
            action,
        )

        return

    if action_type == "macro":
        add_multikey_macro(
            macro,
            action,
        )

        return

    if action_type == "mouse":
        add_mouse_macro(
            macro,
            action,
        )

        return

    if action_type == "wheel":
        add_wheel_macro(
            macro,
            action,
        )

        return

    raise ValueError(
        f"Unsupported Logitech export action "
        f"type: {action_type!r}"
    )


def _build_profile_xml(
    profile: dict[str, Any],
    source_file: Path,
) -> tuple[
    bytes,
    str,
    int,
    int,
]:
    profile_name = str(
        profile.get(
            "name",
            source_file.stem,
        )
    )

    root = ET.Element(
        "profiles",
        {
            "xmlns": PROFILE_NAMESPACE,
        },
    )

    profile_element = ET.SubElement(
        root,
        "profile",
        {
            "lock": (
                "1"
                if bool(
                    profile.get(
                        "lock_while_running",
                        False,
                    )
                )
                else "0"
            ),
            "gameid": "",
            "name": profile_name,
            "gpasupported": "0",
            "guid": profile_guid(
                profile,
                source_file,
            ),
            "gkeysdk": "0",
            "launchable": "1",
        },
    )

    ET.SubElement(
        profile_element,
        "description",
    )

    ET.SubElement(
        profile_element,
        "target",
        {
            "path": str(
                profile.get(
                    "target",
                    "",
                )
            ),
        },
    )

    ET.SubElement(
        profile_element,
        "signature",
        {
            "executable": "",
            "name": "",
            "value": "",
            "key": "",
        },
    )

    macros_element = ET.SubElement(
        profile_element,
        "macros",
    )

    commands = profile.get(
        "commands",
        {},
    )

    if not isinstance(
        commands,
        dict,
    ):
        raise ValueError(
            "Profile commands must be an object."
        )

    command_guids = {}

    for command_id, action in commands.items():
        if not isinstance(
            action,
            dict,
        ):
            raise ValueError(
                f"Command {command_id!r} "
                "must be an object."
            )

        exported_guid = normalize_guid(
            command_id,
            (
                "g13-zombie-command:"
                f"{command_id}"
            ),
        )

        command_guids[
            command_id
        ] = exported_guid

        add_macro(
            macros_element,
            exported_guid,
            action,
            False,
        )

    layers = profile.get(
        "layers",
        {},
    )

    if not isinstance(
        layers,
        dict,
    ):
        raise ValueError(
            "Profile layers must be an object."
        )

    assignments_element = ET.SubElement(
        profile_element,
        "assignments",
        {
            "devicecategory": (
                G13_DEVICE_CATEGORY
            ),
        },
    )

    synthetic_macros = {}

    for layer in (
        "1",
        "2",
        "3",
    ):
        bindings = layers.get(
            layer,
            {},
        )

        if not isinstance(
            bindings,
            dict,
        ):
            raise ValueError(
                f"Layer {layer} must be an object."
            )

        def binding_sort_key(
            item: tuple[str, Any],
        ) -> tuple[int, str]:
            context_id = item[0]

            if (
                context_id.startswith("G")
                and context_id[1:].isdigit()
            ):
                return (
                    int(context_id[1:]),
                    context_id,
                )

            return (
                9999,
                context_id,
            )

        for context_id, action in sorted(
            bindings.items(),
            key=binding_sort_key,
        ):
            if not isinstance(
                action,
                dict,
            ):
                raise ValueError(
                    f"{context_id} in layer "
                    f"{layer} must be an object."
                )

            command_id = action.get(
                "command_id"
            )

            if (
                isinstance(
                    command_id,
                    str,
                )
                and command_id
            ):
                exported_guid = (
                    command_guids.get(
                        command_id
                    )
                )

                if exported_guid is None:
                    exported_guid = normalize_guid(
                        command_id,
                        (
                            "g13-zombie-command:"
                            f"{command_id}"
                        ),
                    )

                    command_guids[
                        command_id
                    ] = exported_guid

                    add_macro(
                        macros_element,
                        exported_guid,
                        action,
                        False,
                    )

                assignment_original = False

            else:
                fingerprint = (
                    action_fingerprint(
                        action
                    )
                )

                exported_guid = (
                    synthetic_macros.get(
                        fingerprint
                    )
                )

                if exported_guid is None:
                    exported_guid = (
                        normalize_guid(
                            None,
                            (
                                "g13-zombie-action:"
                                f"{fingerprint}"
                            ),
                        )
                    )

                    synthetic_macros[
                        fingerprint
                    ] = exported_guid

                    add_macro(
                        macros_element,
                        exported_guid,
                        action,
                        True,
                    )

                assignment_original = True

            ET.SubElement(
                assignments_element,
                "assignment",
                {
                    "backup": "false",
                    "contextid": (
                        context_id
                    ),
                    "macroguid": (
                        exported_guid
                    ),
                    "shiftstate": layer,
                    "original": (
                        "true"
                        if assignment_original
                        else "false"
                    ),
                },
            )

    backlight = profile.get(
        "backlight"
    )

    if backlight is None:
        backlight = {
            "1": [
                255,
                255,
                255,
            ],
            "2": [
                255,
                255,
                255,
            ],
            "3": [
                255,
                255,
                255,
            ],
        }

    if not isinstance(
        backlight,
        dict,
    ):
        raise ValueError(
            "Profile backlight must be an object."
        )

    backlight_element = ET.SubElement(
        profile_element,
        "backlight",
        {
            "devicemodel": (
                G13_DEVICE_MODEL
            ),
        },
    )

    for layer in (
        "1",
        "2",
        "3",
    ):
        color = backlight.get(
            layer,
            [
                255,
                255,
                255,
            ],
        )

        ET.SubElement(
            backlight_element,
            "mode",
            {
                "shiftstate": layer,
                "color": (
                    format_backlight_color(
                        color
                    )
                ),
            },
        )

    script = profile.get(
        "script",
        {},
    )

    if not isinstance(
        script,
        dict,
    ):
        raise ValueError(
            "Profile script must be an object."
        )

    script_source = str(
        script.get(
            "source",
            "",
        )
    )

    if not bool(
        script.get(
            "enabled",
            False,
        )
    ):
        script_source = ""

    script_element = ET.SubElement(
        profile_element,
        "script",
    )

    script_element.text = script_source

    ET.indent(
        root,
        space="  ",
    )

    xml_data = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )

    binding_count = sum(
        len(
            layers.get(
                layer,
                {},
            )
        )
        for layer in (
            "1",
            "2",
            "3",
        )
    )

    return (
        xml_data
        + b"\n",
        profile_name,
        len(commands),
        binding_count,
    )


def export_profile_xml(
    profile: dict[str, Any],
    source_name: str = "zombie-profile.json",
) -> bytes:
    if not isinstance(
        profile,
        dict,
    ):
        raise ValueError(
            "Zombie profile must contain "
            "a JSON object."
        )

    xml_data, _, _, _ = (
        _build_profile_xml(
            profile,
            Path(
                source_name
            ),
        )
    )

    return xml_data


def export_profile(
    input_file: Path,
    output_file: Path,
) -> None:
    profile = json.loads(
        input_file.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        profile,
        dict,
    ):
        raise ValueError(
            "Zombie profile must contain "
            "a JSON object."
        )

    (
        xml_data,
        profile_name,
        command_count,
        binding_count,
    ) = _build_profile_xml(
        profile,
        input_file,
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_bytes(
        xml_data
    )

    print(
        f"Exported profile: {profile_name}"
    )

    print(
        f"Commands exported: "
        f"{command_count}"
    )

    print(
        f"Bindings exported: "
        f"{binding_count}"
    )

    print(
        f"Written to: {output_file}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a G13 Zombie Control Center "
            "JSON profile to Logitech Gaming "
            "Software XML."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Zombie JSON profile",
    )

    parser.add_argument(
        "output",
        type=Path,
        help="Logitech XML profile",
    )

    args = parser.parse_args()

    export_profile(
        args.input,
        args.output,
    )


if __name__ == "__main__":
    main()
