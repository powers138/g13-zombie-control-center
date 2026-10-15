from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evdev import ecodes as e


Action = dict[str, Any]
Bindings = dict[int, dict[str, Action]]


def _resolve_key_name(
    key_name: Any,
    description: str,
) -> int:
    if not isinstance(key_name, str):
        raise ValueError(
            f"{description} must be a Linux "
            "key name such as 'KEY_A'."
        )

    keycode = getattr(
        e,
        key_name,
        None,
    )

    if keycode is None:
        raise ValueError(
            f"Unknown Linux key: {key_name}"
        )

    return keycode


def _validate_nonnegative_integer(
    value: Any,
    description: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(
            f"{description} must be a "
            "nonnegative integer."
        )

    return value


def _resolve_macro_step(
    step: Any,
    step_number: int,
) -> Action:
    if not isinstance(step, dict):
        raise ValueError(
            f"Macro step {step_number} "
            "must be an object."
        )

    resolved = dict(step)
    step_type = resolved.get("type")

    key_steps = {
        "key_down",
        "key_up",
        "key_press",
    }

    mouse_steps = {
        "mouse_down",
        "mouse_up",
        "mouse_click",
    }

    if step_type in key_steps:
        resolved["key"] = _resolve_key_name(
            resolved.get("key"),
            f"Macro step {step_number} key",
        )

        if step_type == "key_press":
            resolved["duration_ms"] = (
                _validate_nonnegative_integer(
                    resolved.get(
                        "duration_ms",
                        30,
                    ),
                    (
                        f"Macro step {step_number} "
                        "duration_ms"
                    ),
                )
            )

        return resolved

    if step_type == "delay":
        resolved["milliseconds"] = (
            _validate_nonnegative_integer(
                resolved.get(
                    "milliseconds"
                ),
                (
                    f"Macro step {step_number} "
                    "milliseconds"
                ),
            )
        )

        return resolved

    if step_type in mouse_steps:
        button = resolved.get("button")

        if button not in {
            "left",
            "right",
            "middle",
            "back",
            "forward",
        }:
            raise ValueError(
                f"Macro step {step_number} "
                "contains an unsupported "
                f"mouse button: {button}"
            )

        if step_type == "mouse_click":
            resolved["duration_ms"] = (
                _validate_nonnegative_integer(
                    resolved.get(
                        "duration_ms",
                        30,
                    ),
                    (
                        f"Macro step {step_number} "
                        "duration_ms"
                    ),
                )
            )

        return resolved

    if step_type == "wheel":
        direction = resolved.get(
            "direction"
        )

        if direction not in {
            "vertical",
            "horizontal",
        }:
            raise ValueError(
                f"Macro step {step_number} "
                "contains an unsupported "
                f"wheel direction: {direction}"
            )

        amount = resolved.get("amount")

        if (
            not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount == 0
        ):
            raise ValueError(
                f"Macro step {step_number} "
                "wheel amount must be a "
                "nonzero integer."
            )

        return resolved

    raise ValueError(
        f"Macro step {step_number} has "
        f"unsupported type: {step_type}"
    )


def resolve_action(action: Action) -> Action:
    if not isinstance(action, dict):
        raise ValueError(
            "A binding action must be an object."
        )

    resolved = dict(action)
    action_type = resolved.get("type")

    if not isinstance(
        resolved.get("name"),
        str,
    ):
        raise ValueError(
            "Every action must have a "
            "string 'name'."
        )

    if action_type == "keyboard":
        resolved["key"] = _resolve_key_name(
            resolved.get("key"),
            "Keyboard key",
        )

        modifier_names = resolved.get(
            "modifiers",
            [],
        )

        if modifier_names is None:
            modifier_names = []

        if (
            not isinstance(
                modifier_names,
                list,
            )
            or not all(
                isinstance(name, str)
                for name in modifier_names
            )
        ):
            raise ValueError(
                "Keyboard 'modifiers' must "
                "be a list of Linux key names."
            )

        resolved["modifiers"] = [
            _resolve_key_name(
                modifier_name,
                "Keyboard modifier",
            )
            for modifier_name
            in modifier_names
        ]

    elif action_type == "mouse":
        button = resolved.get("button")

        if button not in {
            "left",
            "right",
            "middle",
            "back",
            "forward",
        }:
            raise ValueError(
                f"Unsupported mouse button: "
                f"{button}"
            )

    elif action_type == "wheel":
        direction = resolved.get(
            "direction"
        )

        if direction not in {
            "vertical",
            "horizontal",
        }:
            raise ValueError(
                "Unsupported wheel direction: "
                f"{direction}"
            )

        amount = resolved.get("amount")

        if (
            not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount == 0
        ):
            raise ValueError(
                "Wheel amount must be a "
                "nonzero integer."
            )

    elif action_type == "macro":
        raw_steps = resolved.get("steps")

        if (
            not isinstance(raw_steps, list)
            or not raw_steps
        ):
            raise ValueError(
                "Macros must contain a "
                "non-empty 'steps' list."
            )

        resolved["steps"] = [
            _resolve_macro_step(
                step,
                step_number,
            )
            for step_number, step
            in enumerate(
                raw_steps,
                start=1,
            )
        ]

        allow_overlap = resolved.get(
            "allow_overlap",
            False,
        )

        if not isinstance(
            allow_overlap,
            bool,
        ):
            raise ValueError(
                "'allow_overlap' must be "
                "true or false."
            )

        resolved["allow_overlap"] = (
            allow_overlap
        )

        execution_mode = resolved.get(
            "execution_mode",
            "once",
        )

        if execution_mode not in {
            "once",
            "while_held",
            "toggle",
        }:
            raise ValueError(
                "Macro execution mode must be "
                "'once', 'while_held', or 'toggle'."
            )

        resolved["execution_mode"] = (
            execution_mode
        )

        repeat_delay_ms = resolved.get(
            "repeat_delay_ms",
            25,
        )

        if (
            not isinstance(
                repeat_delay_ms,
                int,
            )
            or isinstance(
                repeat_delay_ms,
                bool,
            )
            or repeat_delay_ms < 1
        ):
            raise ValueError(
                "'repeat_delay_ms' must be "
                "a positive integer."
            )

        resolved["repeat_delay_ms"] = (
            repeat_delay_ms
        )

    else:
        raise ValueError(
            f"Unsupported action type: "
            f"{action_type}"
        )

    return resolved


def load_profile_data(
    profile: dict[str, Any],
) -> tuple[dict[str, Any], Bindings]:
    """
    Validate profile data and return:

        1. The original profile data.
        2. Resolved runtime bindings containing evdev keycodes.
    """
    if not isinstance(
        profile,
        dict,
    ):
        raise ValueError(
            "The profile's top level must be an object."
        )

    raw_commands = profile.get(
        "commands",
        {},
    )

    if not isinstance(
        raw_commands,
        dict,
    ):
        raise ValueError(
            "The profile 'commands' value "
            "must be an object."
        )

    for command_id, raw_command in (
        raw_commands.items()
    ):
        if (
            not isinstance(
                command_id,
                str,
            )
            or not command_id.strip()
        ):
            raise ValueError(
                "Profile command IDs must be "
                "non-empty strings."
            )

        try:
            resolve_action(
                raw_command
            )

        except ValueError as error:
            raise ValueError(
                f"Invalid profile command "
                f"{command_id!r}: {error}"
            ) from error

    raw_layers = profile.get(
        "layers"
    )

    if not isinstance(
        raw_layers,
        dict,
    ):
        raise ValueError(
            "The profile must contain "
            "a 'layers' object."
        )

    loaded: Bindings = {
        1: {},
        2: {},
        3: {},
    }

    for layer_text, raw_bindings in (
        raw_layers.items()
    ):
        try:
            layer = int(
                layer_text
            )

        except (
            TypeError,
            ValueError,
        ) as error:
            raise ValueError(
                f"Invalid layer number: "
                f"{layer_text}"
            ) from error

        if layer not in loaded:
            raise ValueError(
                f"Unsupported layer: {layer}. "
                "Only layers 1, 2, and 3 "
                "are supported."
            )

        if not isinstance(
            raw_bindings,
            dict,
        ):
            raise ValueError(
                f"Layer {layer} must contain "
                "an object of bindings."
            )

        for button, action in (
            raw_bindings.items()
        ):
            if not isinstance(
                button,
                str,
            ):
                raise ValueError(
                    f"Layer {layer} contains "
                    "a non-string button name."
                )

            try:
                loaded[
                    layer
                ][
                    button
                ] = resolve_action(
                    action
                )

            except ValueError as error:
                raise ValueError(
                    f"Invalid binding for "
                    f"{button} in layer "
                    f"{layer}: {error}"
                ) from error

    return profile, loaded


def load_profile_file(
    profile_file: Path,
) -> tuple[dict[str, Any], Bindings]:
    """
    Load one profile file and resolve its runtime bindings.
    """
    if not profile_file.exists():
        raise FileNotFoundError(
            "Profile file was not found: "
            f"{profile_file}"
        )

    try:
        profile = json.loads(
            profile_file.read_text(
                encoding="utf-8"
            )
        )

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {profile_file}: "
            f"line {error.lineno}, "
            f"column {error.colno}: "
            f"{error.msg}"
        ) from error

    return load_profile_data(
        profile
    )
