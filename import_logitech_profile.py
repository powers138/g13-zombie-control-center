from __future__ import annotations

import argparse
import copy
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


SPECIAL_KEYS = {
    "SPACE": "KEY_SPACE",
    "SPACEBAR": "KEY_SPACE",
    "ESC": "KEY_ESC",
    "ESCAPE": "KEY_ESC",

    "LCTRL": "KEY_LEFTCTRL",
    "RCTRL": "KEY_RIGHTCTRL",
    "LSHIFT": "KEY_LEFTSHIFT",
    "RSHIFT": "KEY_RIGHTSHIFT",
    "LALT": "KEY_LEFTALT",
    "RALT": "KEY_RIGHTALT",
    "LGUI": "KEY_LEFTMETA",
    "RGUI": "KEY_RIGHTMETA",
    "APPKEY": "KEY_MENU",

    "ENTER": "KEY_ENTER",
    "RETURN": "KEY_ENTER",
    "TAB": "KEY_TAB",
    "BACKSPACE": "KEY_BACKSPACE",
    "DELETE": "KEY_DELETE",
    "INSERT": "KEY_INSERT",

    "CAPSLOCK": "KEY_CAPSLOCK",
    "NUMLOCK": "KEY_NUMLOCK",
    "SCROLLLOCK": "KEY_SCROLLLOCK",
    "PRINTSCREEN": "KEY_SYSRQ",
    "PAUSE": "KEY_PAUSE",

    "HOME": "KEY_HOME",
    "END": "KEY_END",
    "PAGEUP": "KEY_PAGEUP",
    "PAGEDOWN": "KEY_PAGEDOWN",

    "UP": "KEY_UP",
    "DOWN": "KEY_DOWN",
    "LEFT": "KEY_LEFT",
    "RIGHT": "KEY_RIGHT",

    "MINUS": "KEY_MINUS",
    "EQUAL": "KEY_EQUAL",
    "QUOTE": "KEY_APOSTROPHE",
    "TILDE": "KEY_GRAVE",
    "COMMA": "KEY_COMMA",
    "PERIOD": "KEY_DOT",
    "SLASH": "KEY_SLASH",
    "SEMICOLON": "KEY_SEMICOLON",
    "LBRACKET": "KEY_LEFTBRACE",
    "RBRACKET": "KEY_RIGHTBRACE",
    "BACKSLASH": "KEY_BACKSLASH",
    "NON_US_SLASH": "KEY_102ND",

    "NUM0": "KEY_KP0",
    "NUM1": "KEY_KP1",
    "NUM2": "KEY_KP2",
    "NUM3": "KEY_KP3",
    "NUM4": "KEY_KP4",
    "NUM5": "KEY_KP5",
    "NUM6": "KEY_KP6",
    "NUM7": "KEY_KP7",
    "NUM8": "KEY_KP8",
    "NUM9": "KEY_KP9",

    "NUMSTAR": "KEY_KPASTERISK",
    "NUMMINUS": "KEY_KPMINUS",
    "NUMPLUS": "KEY_KPPLUS",
    "NUMPERIOD": "KEY_KPDOT",
    "NUMENTER": "KEY_KPENTER",
    "NUMSLASH": "KEY_KPSLASH",
}


def local_name(tag: str) -> str:
    """Remove an XML namespace from a tag."""
    return tag.rsplit("}", 1)[-1]


def linux_key_name(logitech_name: str) -> str:
    """Convert a Logitech key name to an evdev constant name."""
    name = logitech_name.strip().upper()

    special = SPECIAL_KEYS.get(name)
    if special is not None:
        return special

    if len(name) == 1 and name.isalnum():
        return f"KEY_{name}"

    if name.startswith("F") and name[1:].isdigit():
        return f"KEY_{name}"

    raise ValueError(
        f"Unsupported Logitech key name: {logitech_name}"
    )


def first_descendant(
    element: ET.Element,
    wanted_name: str,
) -> ET.Element | None:
    for child in element.iter():
        if local_name(child.tag) == wanted_name:
            return child

    return None


def direct_child(
    element: ET.Element,
    wanted_name: str,
) -> ET.Element | None:
    for child in element:
        if local_name(child.tag) == wanted_name:
            return child

    return None

def parse_logitech_color(
    color_value: str,
) -> list[int]:
    color = color_value.strip()

    if (
        len(color) != 7
        or not color.startswith("#")
    ):
        raise ValueError(
            "Unsupported Logitech backlight color: "
            f"{color_value}"
        )

    try:
        return [
            int(color[1:3], 16),
            int(color[3:5], 16),
            int(color[5:7], 16),
        ]

    except ValueError as exc:
        raise ValueError(
            "Unsupported Logitech backlight color: "
            f"{color_value}"
        ) from exc


def parse_g13_backlight(
    profile_element: ET.Element,
) -> dict[str, list[int]]:
    colors: dict[str, list[int]] = {}

    for element in profile_element.iter():
        if local_name(element.tag) != "backlight":
            continue

        if (
            element.get("devicemodel")
            != "Logitech.Gaming.LeftHandedController.G13"
        ):
            continue

        for mode in element:
            if local_name(mode.tag) != "mode":
                continue

            layer = mode.get("shiftstate")

            if layer not in {"1", "2", "3"}:
                continue

            color_value = mode.get("color")

            if not color_value:
                continue

            colors[layer] = parse_logitech_color(
                color_value
            )

        break

    return colors

MOUSE_TASKS = {
    "leftclick": "left",
    "rightclick": "right",
    "middleclick": "middle",
    "back": "back",
    "forward": "forward",
}

HOTKEY_TASKS = {
    "copy": {
        "key": "KEY_C",
        "modifiers": [
            "KEY_LEFTCTRL",
        ],
    },
    "paste": {
        "key": "KEY_V",
        "modifiers": [
            "KEY_LEFTCTRL",
        ],
    },
    "cut": {
        "key": "KEY_X",
        "modifiers": [
            "KEY_LEFTCTRL",
        ],
    },
}

def parse_macro(macro: ET.Element) -> dict[str, Any] | None:
    macro_name = macro.get("name", "Unnamed")

    keystroke = direct_child(macro, "keystroke")

    if keystroke is not None:
        key_element = first_descendant(keystroke, "key")

        if key_element is None:
            raise ValueError(
                f"Macro '{macro_name}' has no key."
            )

        key_value = key_element.get("value")

        if not key_value:
            raise ValueError(
                f"Macro '{macro_name}' has an empty key."
            )

        modifiers = []

        for element in keystroke.iter():
            if local_name(element.tag) != "modifier":
                continue

            modifier_value = element.get("value")

            if modifier_value:
                modifiers.append(
                    linux_key_name(modifier_value)
                )

        action: dict[str, Any] = {
            "type": "keyboard",
            "name": macro_name,
            "key": linux_key_name(key_value),
        }

        if modifiers:
            action["modifiers"] = modifiers

        return action

    multikey = direct_child(
        macro,
        "multikey",
    )

    if multikey is not None:
        steps: list[dict[str, Any]] = []

        for element in multikey:
            element_name = local_name(
                element.tag
            )

            if element_name == "key":
                direction = (
                    element.get(
                        "direction",
                        "",
                    )
                    .strip()
                    .lower()
                )

                key_value = element.get(
                    "value"
                )

                if not key_value:
                    raise ValueError(
                        f"Multi Key macro "
                        f"'{macro_name}' contains "
                        "a key with no value."
                    )

                if direction == "down":
                    step_type = "key_down"

                elif direction == "up":
                    step_type = "key_up"

                else:
                    raise ValueError(
                        f"Multi Key macro "
                        f"'{macro_name}' has "
                        "unsupported key direction "
                        f"{direction!r}."
                    )

                steps.append(
                    {
                        "type": step_type,
                        "key": linux_key_name(
                            key_value
                        ),
                    }
                )

                continue

            if element_name == "delay":
                milliseconds_text = (
                    element.get(
                        "milliseconds"
                    )
                )

                try:
                    milliseconds = int(
                        milliseconds_text
                    )

                except (
                    TypeError,
                    ValueError,
                ) as error:
                    raise ValueError(
                        f"Multi Key macro "
                        f"'{macro_name}' has an "
                        "invalid delay."
                    ) from error

                if milliseconds < 0:
                    raise ValueError(
                        f"Multi Key macro "
                        f"'{macro_name}' has a "
                        "negative delay."
                    )

                steps.append(
                    {
                        "type": "delay",
                        "milliseconds": milliseconds,
                    }
                )

                continue

            if element_name == "mousewheel":
                delta_text = element.get(
                    "delta"
                )

                try:
                    delta = int(
                        delta_text
                    )

                except (
                    TypeError,
                    ValueError,
                ) as error:
                    raise ValueError(
                        f"Multi Key macro "
                        f"'{macro_name}' has an "
                        "invalid mouse-wheel delta."
                    ) from error

                if delta == 0:
                    raise ValueError(
                        f"Multi Key macro "
                        f"'{macro_name}' has a "
                        "zero mouse-wheel delta."
                    )

                steps.append(
                    {
                        "type": "wheel",
                        "direction": "vertical",
                        "amount": delta,
                    }
                )

                continue

            raise ValueError(
                f"Multi Key macro "
                f"'{macro_name}' contains "
                f"unsupported event "
                f"{element_name!r}."
            )

        if not steps:
            return None

        if (
            macro.get(
                "repeatmode"
            ) is None
            and macro.get(
                "repeatdelay"
            ) is None
            and all(
                step.get(
                    "type"
                )
                == "wheel"
                for step in steps
            )
        ):
            amounts = [
                int(
                    step[
                        "amount"
                    ]
                )
                for step in steps
            ]

            same_direction = (
                all(
                    amount > 0
                    for amount in amounts
                )
                or all(
                    amount < 0
                    for amount in amounts
                )
            )

            total_amount = sum(
                amounts
            )

            if (
                same_direction
                and 1
                <= abs(
                    total_amount
                )
                <= 20
            ):
                return {
                    "type": "wheel",
                    "name": macro_name,
                    "direction": "vertical",
                    "amount": total_amount,
                }

        repeat_mode = macro.get(
            "repeatmode"
        )

        if repeat_mode is None:
            execution_mode = "once"

        elif repeat_mode == "pressed":
            execution_mode = "while_held"

        elif repeat_mode == "toggle":
            execution_mode = "toggle"

        else:
            raise ValueError(
                f"Multi Key macro "
                f"'{macro_name}' has unsupported "
                f"repeat mode {repeat_mode!r}."
            )

        repeat_delay_text = macro.get(
            "repeatdelay",
            "25",
        )

        try:
            repeat_delay_ms = int(
                repeat_delay_text
            )

        except (
            TypeError,
            ValueError,
        ) as error:
            raise ValueError(
                f"Multi Key macro "
                f"'{macro_name}' has an invalid "
                "repeat delay."
            ) from error

        if repeat_delay_ms < 1:
            raise ValueError(
                f"Multi Key macro "
                f"'{macro_name}' has an invalid "
                "repeat delay."
            )

        return {
            "type": "macro",
            "name": macro_name,
            "execution_mode": execution_mode,
            "repeat_delay_ms": repeat_delay_ms,
            "allow_overlap": False,
            "steps": steps,
        }

    hotkeys = direct_child(
        macro,
        "hotkeys",
    )

    if hotkeys is not None:
        do_element = first_descendant(
            hotkeys,
            "do",
        )

        if do_element is None:
            return None

        task = (
            do_element.get(
                "task",
                "",
            )
            .strip()
            .lower()
        )

        shortcut = HOTKEY_TASKS.get(
            task
        )

        if shortcut is None:
            print(
                "Skipping unsupported Logitech "
                f"hotkey task: {task!r}",
                file=sys.stderr,
            )
            return None

        return {
            "type": "keyboard",
            "name": macro_name,
            "key": shortcut["key"],
            "modifiers": list(
                shortcut[
                    "modifiers"
                ]
            ),
        }

    natural = direct_child(
        macro,
        "natural",
    )

    if natural is not None:
        is_original = (
            macro.get(
                "original",
                "",
            )
            .strip()
            .lower()
            == "true"
        )

        if is_original:
            return None

        print(
            "Skipping unsupported Logitech "
            f"Natural macro: {macro_name}",
            file=sys.stderr,
        )
        return None

    mouse_function = direct_child(macro, "mousefunction")

    if mouse_function is not None:
        do_element = first_descendant(mouse_function, "do")

        if do_element is None:
            return None

        task = (
            do_element.get(
                "task",
                "",
            )
            .strip()
            .lower()
        )

        if task == "scrollup":
            return {
                "type": "wheel",
                "name": macro_name,
                "direction": "vertical",
                "amount": 1,
            }

        if task == "scrolldown":
            return {
                "type": "wheel",
                "name": macro_name,
                "direction": "vertical",
                "amount": -1,
            }

        if task == "scrollright":
            return {
                "type": "wheel",
                "name": macro_name,
                "direction": "horizontal",
                "amount": 1,
            }

        if task == "scrollleft":
            return {
                "type": "wheel",
                "name": macro_name,
                "direction": "horizontal",
                "amount": -1,
            }

        button = MOUSE_TASKS.get(
            task
        )

        if button is None:
            return None

        return {
            "type": "mouse",
            "name": macro_name,
            "button": button,
        }

    print(
        f"Skipping unsupported macro type: {macro_name}",
        file=sys.stderr,
    )
    return None


def context_sort_key(item: tuple[str, Any]) -> tuple[int, str]:
    context_id = item[0]

    if context_id.startswith("G") and context_id[1:].isdigit():
        return int(context_id[1:]), context_id

    return 9999, context_id


def _find_profile_element(
    root: ET.Element,
) -> ET.Element:
    profile_element = next(
        (
            element
            for element in root.iter()
            if local_name(
                element.tag
            ) == "profile"
        ),
        None,
    )

    if profile_element is None:
        raise ValueError(
            "No Logitech profile was found."
        )

    return profile_element


def _convert_profile_element(
    profile_element: ET.Element,
    source_name: str,
    fallback_name: str,
) -> tuple[dict[str, Any], int]:
    profile_name = profile_element.get(
        "name",
        fallback_name,
    )

    raw_profile_lock = (
        profile_element.get(
            "lock",
            "0",
        )
        .strip()
        .casefold()
    )

    if raw_profile_lock in {
        "1",
        "true",
        "yes",
    }:
        lock_while_running = True

    elif raw_profile_lock in {
        "",
        "0",
        "false",
        "no",
    }:
        lock_while_running = False

    else:
        print(
            "Unsupported Logitech profile lock value "
            f"{raw_profile_lock!r}; using unlocked.",
            file=sys.stderr,
        )

        lock_while_running = False

    target_element = first_descendant(
        profile_element,
        "target",
    )

    target_path = (
        target_element.get(
            "path",
            "",
        )
        if target_element is not None
        else ""
    )

    script_element = direct_child(
        profile_element,
        "script",
    )

    script_source = (
        script_element.text
        if (
            script_element is not None
            and script_element.text is not None
        )
        else ""
    )

    script_source = script_source.strip()

    backlight = parse_g13_backlight(
        profile_element
    )

    macro_actions: dict[
        str,
        dict[str, Any],
    ] = {}

    commands: dict[
        str,
        dict[str, Any],
    ] = {}

    for element in profile_element.iter():
        if (
            local_name(
                element.tag
            )
            != "macro"
        ):
            continue

        guid = element.get(
            "guid"
        )

        if not guid:
            continue

        action = parse_macro(
            element
        )

        if action is None:
            continue

        command_id = guid.upper()

        # Keep every supported Logitech action available
        # internally so G-key assignments can resolve it.
        macro_actions[
            command_id
        ] = action

        # Logitech marks its built-in command definitions
        # with original="true". User-created profile commands
        # omit that flag, so only preserve those in our
        # profile-level Commands library.
        is_original = (
            element.get(
                "original",
                "",
            )
            .strip()
            .lower()
            == "true"
        )

        if not is_original:
            commands[
                command_id
            ] = action

    layers: dict[
        str,
        dict[
            str,
            dict[str, Any],
        ],
    ] = {
        "1": {},
        "2": {},
        "3": {},
    }

    for assignments in profile_element.iter():
        if (
            local_name(
                assignments.tag
            )
            != "assignments"
        ):
            continue

        if (
            assignments.get(
                "devicecategory"
            )
            != "Logitech.Gaming.LeftHandedController"
        ):
            continue

        for assignment in assignments:
            if (
                local_name(
                    assignment.tag
                )
                != "assignment"
            ):
                continue

            is_backup = (
                assignment.get(
                    "backup",
                    "false",
                )
                .strip()
                .lower()
                == "true"
            )

            if is_backup:
                continue

            layer = assignment.get(
                "shiftstate"
            )

            context_id = assignment.get(
                "contextid"
            )

            macro_guid = assignment.get(
                "macroguid",
                "",
            ).upper()

            if layer not in layers:
                print(
                    "Skipping unsupported "
                    f"shiftstate: {layer}",
                    file=sys.stderr,
                )
                continue

            if not context_id:
                continue

            action = macro_actions.get(
                macro_guid
            )

            if action is None:
                print(
                    f"Skipping {context_id} "
                    f"in layer {layer}: "
                    f"macro {macro_guid} "
                    "was not found or unsupported.",
                    file=sys.stderr,
                )
                continue

            linked_action = copy.deepcopy(
                action
            )

            if macro_guid in commands:
                linked_action[
                    "command_id"
                ] = macro_guid

            layers[
                layer
            ][
                context_id
            ] = linked_action

    for layer in layers:
        layers[layer] = dict(
            sorted(
                layers[
                    layer
                ].items(),
                key=context_sort_key,
            )
        )

    converted = {
        "name": profile_name,
        "lock_while_running": (
            lock_while_running
        ),
        "source": source_name,
        "target": target_path,
        "backlight": backlight,
        "script": {
            "language": "lua",
            "enabled": bool(
                script_source
            ),
            "source": script_source,
        },
        "commands": commands,
        "layers": layers,
    }

    binding_count = sum(
        len(
            layer_bindings
        )
        for layer_bindings in layers.values()
    )

    return (
        converted,
        binding_count,
    )


def convert_profile_xml(
    xml_data: bytes,
    source_name: str = "onboard-profile.xml",
) -> dict[str, Any]:
    root = ET.fromstring(
        xml_data
    )

    profile_element = (
        _find_profile_element(
            root
        )
    )

    fallback_name = (
        Path(
            source_name
        ).stem
    )

    converted, _ = (
        _convert_profile_element(
            profile_element,
            source_name,
            fallback_name,
        )
    )

    return converted


def convert_profile(
    input_file: Path,
    output_file: Path,
) -> None:
    tree = ET.parse(
        input_file
    )

    root = tree.getroot()

    profile_element = (
        _find_profile_element(
            root
        )
    )

    converted, binding_count = (
        _convert_profile_element(
            profile_element,
            input_file.name,
            input_file.stem,
        )
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_text(
        json.dumps(
            converted,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Converted profile: "
        f"{converted['name']}"
    )

    print(
        f"Bindings imported: "
        f"{binding_count}"
    )

    print(
        f"Written to: "
        f"{output_file}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a Logitech Gaming Software XML profile "
            "to the G13 JSON format."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Input Logitech XML profile.",
    )

    parser.add_argument(
        "output",
        type=Path,
        help="Output G13 JSON profile.",
    )

    args = parser.parse_args()

    convert_profile(args.input, args.output)


if __name__ == "__main__":
    main()
