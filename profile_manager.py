from __future__ import annotations

import copy
import uuid
import json
import re
from pathlib import Path
from typing import Any

from app_paths import (
    PROFILES_DIR,
    SETTINGS_FILE,
)
from user_data_bootstrap import (
    bootstrap_user_data,
)
from bindings import (
    Action,
    Bindings,
    load_profile_data,
    load_profile_file,
    resolve_action,
)


DEFAULT_BACKLIGHT_COLOR = (
    255,
    255,
    255,
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"JSON file was not found: {path}"
        )

    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path}: "
            f"line {error.lineno}, "
            f"column {error.colno}: "
            f"{error.msg}"
        ) from error

    if not isinstance(value, dict):
        raise ValueError(
            f"The top level of {path} must be an object."
        )

    return value


def _write_json_atomic(
    path: Path,
    value: dict[str, Any],
) -> None:
    """
    Write through a temporary file so an interrupted save does
    not leave the real JSON file partially written.
    """
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_name(
        f".{path.name}.tmp"
    )

    temporary_path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
    )

    temporary_path.replace(path)


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def _validate_profile_filename(
    filename: str,
) -> str:
    if not isinstance(filename, str) or not filename:
        raise ValueError(
            "Profile filename must be a non-empty string."
        )

    path = Path(filename)

    if path.name != filename:
        raise ValueError(
            "Profile filenames cannot contain directories."
        )

    if path.suffix.lower() != ".json":
        raise ValueError(
            "Profile filenames must end in '.json'."
        )

    return filename

def _filename_from_display_name(
    name: str,
) -> str:
    cleaned = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "-",
        name.strip(),
    ).strip("-_").lower()

    if not cleaned:
        cleaned = "profile"

    return f"{cleaned}.json"

DEFAULT_JOYSTICK_SETTINGS: dict[str, Any] = {
    "mode": "mouse",
    "start_deadzone": 0.35,
    "stop_deadzone": 0.20,
    "left_gain": 1.0,
    "right_gain": 1.0,
    "up_gain": 1.0,
    "down_gain": 1.0,
    "center_alpha": 0.005,
}

class ProfileManager:
    def __init__(
        self,
        profiles_dir: Path = PROFILES_DIR,
        settings_file: Path = SETTINGS_FILE,
    ):
        if (
            profiles_dir == PROFILES_DIR
            and settings_file == SETTINGS_FILE
        ):
            bootstrap_user_data()

        self.profiles_dir = profiles_dir
        self.settings_file = settings_file

        self.profiles_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.settings = _read_json(
            self.settings_file
        )

        self.active_profile_name = ""
        self._active_profile_is_transient = False
        self.profile: dict[str, Any] = {}

        self.bindings: Bindings = {
            1: {},
            2: {},
            3: {},
        }

        self._settings_mtime_ns = _mtime_ns(
            self.settings_file
        )

        self._profile_mtime_ns: int | None = None

        requested_profile = self.settings.get(
            "active_profile",
            "default.json",
        )

        if not isinstance(requested_profile, str):
            raise ValueError(
                "'active_profile' must be a filename."
            )

        try:
            self.set_active_profile(
                requested_profile,
                persist=False,
            )

        except FileNotFoundError:
            available_profiles = self.list_profiles()

            if not available_profiles:
                raise RuntimeError(
                    "No G13 profiles were found in "
                    f"{self.profiles_dir}"
                )

            fallback_profile = available_profiles[0]

            print(
                f"Active profile '{requested_profile}' "
                f"was not found. Using '{fallback_profile}'."
            )

            self.set_active_profile(
                fallback_profile,
                persist=True,
            )

    def _profile_path(
        self,
        filename: str,
    ) -> Path:
        valid_filename = _validate_profile_filename(
            filename
        )

        return self.profiles_dir / valid_filename

    def _available_profile_filename(
        self,
        display_name: str,
    ) -> str:
        filename = _filename_from_display_name(
            display_name
        )

        if not self._profile_path(
            filename
        ).exists():
            return filename

        stem = Path(filename).stem
        number = 2

        while True:
            candidate = f"{stem}-{number}.json"

            if not self._profile_path(
                candidate
            ).exists():
                return candidate

            number += 1

    def _save_settings(self) -> None:
        _write_json_atomic(
            self.settings_file,
            self.settings,
        )

        self._settings_mtime_ns = _mtime_ns(
            self.settings_file
        )

    def list_profiles(self) -> list[str]:
        return sorted(
            path.name
            for path in self.profiles_dir.glob("*.json")
            if path.is_file()
        )

    def get_profile_display_name(
        self,
        filename: str,
    ) -> str:
        profile_path = self._profile_path(
            filename
        )

        raw_profile, _ = load_profile_file(
            profile_path
        )

        display_name = raw_profile.get(
            "name",
            filename,
        )

        if (
            not isinstance(display_name, str)
            or not display_name.strip()
        ):
            return filename

        return display_name.strip()

    def get_profile_icon_path(
        self,
        filename: str | None = None,
    ) -> str | None:
        if (
            filename is None
            or filename == self.active_profile_name
        ):
            profile = self.profile

        else:
            profile_path = self._profile_path(
                filename
            )

            profile, _ = load_profile_file(
                profile_path
            )

        icon_path = profile.get(
            "icon"
        )

        if icon_path is None:
            return None

        if not isinstance(
            icon_path,
            str,
        ):
            raise ValueError(
                "'icon' in the profile must be "
                "a string."
            )

        icon_path = icon_path.strip()

        if not icon_path:
            return None

        return icon_path

    def set_profile_icon_path(
        self,
        icon_path: str | None,
    ) -> None:
        if icon_path is None:
            self.profile.pop(
                "icon",
                None,
            )
            return

        if not isinstance(
            icon_path,
            str,
        ):
            raise ValueError(
                "Profile icon path must be "
                "a string."
            )

        icon_path = icon_path.strip()

        if not icon_path:
            self.profile.pop(
                "icon",
                None,
            )
            return

        self.profile["icon"] = icon_path

    def get_fallback_profile(
        self,
    ) -> str | None:
        fallback_profile = self.settings.get(
            "active_profile"
        )

        if not isinstance(
            fallback_profile,
            str,
        ):
            return None

        return fallback_profile

    def set_fallback_profile(
        self,
        filename: str,
    ) -> None:
        profile_path = self._profile_path(
            filename
        )

        # Confirm that the profile exists and is valid before
        # saving it as the manual fallback.
        load_profile_file(
            profile_path
        )

        self.settings[
            "active_profile"
        ] = filename

        self._save_settings()


    def set_active_profile(
        self,
        filename: str,
        persist: bool = True,
    ) -> None:
        profile_path = self._profile_path(filename)

        raw_profile, resolved_bindings = (
            load_profile_file(profile_path)
        )

        self.active_profile_name = filename
        self._active_profile_is_transient = False
        self.profile = raw_profile
        self.bindings = resolved_bindings

        if persist:
            self.settings["active_profile"] = filename
            self._save_settings()

        profile_name = self.profile.get(
            "name",
            filename,
        )

        print(f"Loaded profile: {profile_name}")

        self._profile_mtime_ns = _mtime_ns(
            profile_path
        )

        self._settings_mtime_ns = _mtime_ns(
            self.settings_file
        )

    def set_active_profile_data(
        self,
        identifier: str,
        profile_data: dict[str, Any],
    ) -> None:
        if (
            not isinstance(
                identifier,
                str,
            )
            or not identifier.strip()
        ):
            raise ValueError(
                "Transient profile identifier "
                "must be a non-empty string."
            )

        profile_copy = copy.deepcopy(
            profile_data
        )

        raw_profile, resolved_bindings = (
            load_profile_data(
                profile_copy
            )
        )

        self.active_profile_name = identifier
        self._active_profile_is_transient = True
        self.profile = raw_profile
        self.bindings = resolved_bindings

        profile_name = self.profile.get(
            "name",
            identifier,
        )

        print(
            f"Loaded transient profile: "
            f"{profile_name}"
        )

        self._profile_mtime_ns = None

        self._settings_mtime_ns = _mtime_ns(
            self.settings_file
        )

    def reload_active_profile(self) -> None:
        if not self.active_profile_name:
            raise RuntimeError(
                "No active profile is loaded."
            )

        if self._active_profile_is_transient:
            self.set_active_profile_data(
                self.active_profile_name,
                self.profile,
            )
            return

        self.set_active_profile(
            self.active_profile_name,
            persist=False,
        )

    def reload_if_changed(self) -> bool:
        """
        Reload settings or the active profile when either file
        changes.

        Returns True when settings, the active profile selection,
        or the active profile contents changed.
        """
        changed = False

        settings_mtime = _mtime_ns(
            self.settings_file
        )

        if settings_mtime != self._settings_mtime_ns:
            updated_settings = _read_json(
                self.settings_file
            )

            requested_profile = updated_settings.get(
                "active_profile",
                "default.json",
            )

            if not isinstance(requested_profile, str):
                raise ValueError(
                    "'active_profile' must be a filename."
                )

            self.settings = updated_settings
            self._settings_mtime_ns = settings_mtime
            changed = True

            if (
                not self._active_profile_is_transient
                and requested_profile
                != self.active_profile_name
            ):
                self.set_active_profile(
                    requested_profile,
                    persist=False,
                )

                return True

        if (
            self.active_profile_name
            and not self._active_profile_is_transient
        ):
            profile_path = self._profile_path(
                self.active_profile_name
            )

            profile_mtime = _mtime_ns(
                profile_path
            )

            if (
                profile_mtime
                != self._profile_mtime_ns
            ):
                self.set_active_profile(
                    self.active_profile_name,
                    persist=False,
                )

                return True

        return changed

    def get_binding(
        self,
        layer: int,
        button: str,
    ) -> Action | None:
        return self.bindings.get(
            layer,
            {},
        ).get(button)

    def get_macro_by_name(
        self,
        name: str,
    ) -> Action | None:
        """
        Find a named macro in the current profile.

        Profile-level commands are searched first.
        Older profiles with macros stored only as
        G-key bindings remain supported.
        """
        if not isinstance(name, str):
            raise ValueError(
                "Macro name must be text."
            )

        macro_name = name.strip()

        if not macro_name:
            raise ValueError(
                "Macro name cannot be empty."
            )

        commands = self.profile.get(
            "commands",
            {},
        )

        if isinstance(commands, dict):
            for raw_action in (
                commands.values()
            ):
                if not isinstance(
                    raw_action,
                    dict,
                ):
                    continue

                if (
                    raw_action.get("type")
                    != "macro"
                ):
                    continue

                if (
                    str(
                        raw_action.get(
                            "name",
                            "",
                        )
                    )
                    != macro_name
                ):
                    continue

                return resolve_action(
                    raw_action
                )

        # Backward compatibility with existing profiles where
        # macros exist only as assigned G-key actions.
        for layer in (1, 2, 3):
            for action in self.bindings.get(
                layer,
                {},
            ).values():
                if (
                    action.get("type")
                    == "macro"
                    and str(
                        action.get(
                            "name",
                            "",
                        )
                    ) == macro_name
                ):
                    return copy.deepcopy(
                        action
                    )

        return None

    def get_profile_script(
        self,
    ) -> dict[str, Any]:
        script = self.profile.get(
            "script",
            {},
        )

        if not isinstance(
            script,
            dict,
        ):
            return {
                "language": "lua",
                "enabled": False,
                "source": "",
            }

        language = script.get(
            "language",
            "lua",
        )

        enabled = script.get(
            "enabled",
            False,
        )

        source = script.get(
            "source",
            "",
        )

        if not isinstance(
            language,
            str,
        ):
            language = "lua"

        if not isinstance(
            enabled,
            bool,
        ):
            enabled = False

        if not isinstance(
            source,
            str,
        ):
            source = ""

        return {
            "language": language,
            "enabled": enabled,
            "source": source,
        }


    def save_profile_script(
        self,
        script: dict[str, Any],
    ) -> None:
        if not isinstance(
            script,
            dict,
        ):
            raise ValueError(
                "Profile script must be an object."
            )

        language = script.get(
            "language"
        )

        enabled = script.get(
            "enabled"
        )

        source = script.get(
            "source"
        )

        if language != "lua":
            raise ValueError(
                "Only Lua scripts are currently supported."
            )

        if not isinstance(
            enabled,
            bool,
        ):
            raise ValueError(
                "Script enabled state must be a boolean."
            )

        if not isinstance(
            source,
            str,
        ):
            raise ValueError(
                "Script source must be text."
            )

        self.profile[
            "script"
        ] = {
            "language": "lua",
            "enabled": enabled,
            "source": source,
        }

        self.save_active_profile()



    def get_profile_copy(
        self,
    ) -> dict[str, Any]:
        return copy.deepcopy(self.profile)

    def get_commands(
        self,
    ) -> dict[str, Action]:
        commands = self.profile.get(
            "commands",
            {},
        )

        if not isinstance(
            commands,
            dict,
        ):
            return {}

        return copy.deepcopy(
            commands
        )

    def assign_command(
        self,
        layer: int,
        button: str,
        command_id: str,
    ) -> None:
        if (
            not isinstance(command_id, str)
            or not command_id.strip()
        ):
            raise ValueError(
                "Command ID cannot be empty."
            )

        commands = self.profile.get(
            "commands",
            {},
        )

        if not isinstance(
            commands,
            dict,
        ):
            raise ValueError(
                "'commands' in the active profile "
                "must be an object."
            )

        action = commands.get(
            command_id
        )

        if not isinstance(
            action,
            dict,
        ):
            raise KeyError(
                f"Command was not found: "
                f"{command_id}"
            )

        linked_action = copy.deepcopy(
            action
        )

        linked_action[
            "command_id"
        ] = command_id

        self.set_binding(
            layer,
            button,
            linked_action,
        )

    def set_command_binding(
        self,
        layer: int,
        button: str,
        action: Action,
    ) -> str:
        if not isinstance(action, dict):
            raise ValueError(
                "Command must be an action object."
            )

        resolve_action(action)

        raw_layers = self.profile.get("layers", {})

        if isinstance(raw_layers, dict):
            raw_layer = raw_layers.get(str(layer), {})
        else:
            raw_layer = {}

        if isinstance(raw_layer, dict):
            current_action = raw_layer.get(button)
        else:
            current_action = None

        command_id = None

        if isinstance(current_action, dict):
            candidate_id = current_action.get("command_id")

            if (
                isinstance(candidate_id, str)
                and candidate_id.strip()
            ):
                commands = self.profile.get(
                    "commands",
                    {},
                )

                if (
                    isinstance(commands, dict)
                    and candidate_id in commands
                ):
                    command_id = candidate_id

        if command_id is None:
            command_id = self.create_command(action)
        else:
            self.update_command(
                command_id,
                action,
            )

        self.assign_command(
            layer,
            button,
            command_id,
        )

        return command_id

    def create_command(
        self,
        action: Action,
    ) -> str:
        if not isinstance(
            action,
            dict,
        ):
            raise ValueError(
                "Command must be an action object."
            )

        # Validate the action before placing it
        # into the profile command library.
        resolve_action(action)

        name = action.get(
            "name",
            "",
        )

        if (
            not isinstance(name, str)
            or not name.strip()
        ):
            raise ValueError(
                "Command name cannot be empty."
            )

        commands = self.profile.setdefault(
            "commands",
            {},
        )

        if not isinstance(
            commands,
            dict,
        ):
            raise ValueError(
                "'commands' in the active profile "
                "must be an object."
            )

        command_id = (
            "{"
            + str(uuid.uuid4()).upper()
            + "}"
        )

        commands[
            command_id
        ] = copy.deepcopy(
            action
        )

        return command_id

    def update_command(
        self,
        command_id: str,
        action: Action,
    ) -> None:
        if (
            not isinstance(command_id, str)
            or not command_id.strip()
        ):
            raise ValueError(
                "Command ID cannot be empty."
            )

        if not isinstance(
            action,
            dict,
        ):
            raise ValueError(
                "Command must be an action object."
            )

        resolve_action(action)

        name = action.get(
            "name",
            "",
        )

        if (
            not isinstance(name, str)
            or not name.strip()
        ):
            raise ValueError(
                "Command name cannot be empty."
            )

        commands = self.profile.get(
            "commands",
            {},
        )

        if not isinstance(
            commands,
            dict,
        ):
            raise ValueError(
                "'commands' in the active profile "
                "must be an object."
            )

        if command_id not in commands:
            raise KeyError(
                f"Command was not found: "
                f"{command_id}"
            )

        commands[
            command_id
        ] = copy.deepcopy(
            action
        )

        raw_layers = self.profile.get(
            "layers",
            {},
        )

        if not isinstance(
            raw_layers,
            dict,
        ):
            return

        for layer in (1, 2, 3):
            raw_layer = raw_layers.get(
                str(layer),
                {},
            )

            if not isinstance(
                raw_layer,
                dict,
            ):
                continue

            for (
                button,
                raw_binding,
            ) in list(
                raw_layer.items()
            ):
                if not isinstance(
                    raw_binding,
                    dict,
                ):
                    continue

                if (
                    raw_binding.get(
                        "command_id"
                    )
                    != command_id
                ):
                    continue

                linked_action = (
                    copy.deepcopy(
                        action
                    )
                )

                linked_action[
                    "command_id"
                ] = command_id

                raw_layer[
                    button
                ] = linked_action

                self.bindings[
                    layer
                ][
                    button
                ] = resolve_action(
                    linked_action
                )

    def delete_command(
        self,
        command_id: str,
    ) -> None:
        if (
            not isinstance(command_id, str)
            or not command_id.strip()
        ):
            raise ValueError(
                "Command ID cannot be empty."
            )

        commands = self.profile.get(
            "commands",
            {},
        )

        if not isinstance(
            commands,
            dict,
        ):
            raise ValueError(
                "'commands' in the active profile "
                "must be an object."
            )

        if command_id not in commands:
            raise KeyError(
                f"Command was not found: "
                f"{command_id}"
            )

        raw_layers = self.profile.get(
            "layers",
            {},
        )

        linked_bindings: list[
            tuple[int, str]
        ] = []

        if isinstance(
            raw_layers,
            dict,
        ):
            for layer in (1, 2, 3):
                raw_layer = raw_layers.get(
                    str(layer),
                    {},
                )

                if not isinstance(
                    raw_layer,
                    dict,
                ):
                    continue

                for (
                    button,
                    raw_binding,
                ) in raw_layer.items():
                    if (
                        isinstance(
                            raw_binding,
                            dict,
                        )
                        and raw_binding.get(
                            "command_id"
                        )
                        == command_id
                    ):
                        linked_bindings.append(
                            (
                                layer,
                                button,
                            )
                        )

        for (
            layer,
            button,
        ) in linked_bindings:
            self.clear_binding(
                layer,
                button,
            )

        del commands[
            command_id
        ]

    def get_profile_lock_while_running(
        self,
        filename: str | None = None,
    ) -> bool:
        if (
            filename is None
            or filename == self.active_profile_name
        ):
            profile = self.profile

        else:
            profile_path = self._profile_path(
                filename
            )

            profile, _ = load_profile_file(
                profile_path
            )

        enabled = profile.get(
            "lock_while_running",
            False,
        )

        if not isinstance(enabled, bool):
            raise ValueError(
                "'lock_while_running' must be "
                "true or false."
            )

        return enabled

    def set_profile_lock_while_running(
        self,
        enabled: bool,
    ) -> None:
        if not isinstance(enabled, bool):
            raise ValueError(
                "Lock while application is running "
                "must be true or false."
            )

        self.profile[
            "lock_while_running"
        ] = enabled

    def get_locked_profile(
        self,
    ) -> str | None:
        locked_profile = self.settings.get(
            "locked_profile"
        )

        if locked_profile in {
            None,
            "",
        }:
            return None

        if not isinstance(
            locked_profile,
            str,
        ):
            raise ValueError(
                "'locked_profile' must be "
                "a profile filename or null."
            )

        return _validate_profile_filename(
            locked_profile
        )

    def set_locked_profile(
        self,
        filename: str | None,
    ) -> None:
        if filename in {
            None,
            "",
        }:
            self.settings[
                "locked_profile"
            ] = None

            self._save_settings()
            return

        if not isinstance(filename, str):
            raise ValueError(
                "Locked profile must be "
                "a profile filename or null."
            )

        filename = _validate_profile_filename(
            filename
        )

        if filename not in self.list_profiles():
            raise FileNotFoundError(
                f"Profile was not found: {filename}"
            )

        self.settings[
            "locked_profile"
        ] = filename

        self._save_settings()

    def get_automatic_profile_switching(
        self,
    ) -> bool:
        enabled = self.settings.get(
            "automatic_profile_switching",
            False,
        )

        if not isinstance(enabled, bool):
            raise ValueError(
                "'automatic_profile_switching' "
                "must be true or false."
            )

        return enabled

    def set_automatic_profile_switching(
        self,
        enabled: bool,
    ) -> None:
        if not isinstance(enabled, bool):
            raise ValueError(
                "Automatic profile switching "
                "must be true or false."
            )

        self.settings[
            "automatic_profile_switching"
        ] = enabled

        self._save_settings()

    def get_application_rules(
        self,
    ) -> list[dict[str, str]]:
        raw_rules = self.settings.get(
            "application_rules",
            [],
        )

        if not isinstance(raw_rules, list):
            raise ValueError(
                "'application_rules' must be a list."
            )

        rules: list[dict[str, str]] = []

        for index, raw_rule in enumerate(
            raw_rules,
            start=1,
        ):
            if not isinstance(raw_rule, dict):
                raise ValueError(
                    f"Application rule {index} "
                    "must be an object."
                )

            window_class = raw_rule.get(
                "window_class"
            )

            profile = raw_rule.get(
                "profile"
            )

            if (
                not isinstance(window_class, str)
                or not window_class.strip()
            ):
                raise ValueError(
                    f"Application rule {index} "
                    "must contain a non-empty "
                    "'window_class'."
                )

            if (
                not isinstance(profile, str)
                or not profile.strip()
            ):
                raise ValueError(
                    f"Application rule {index} "
                    "must contain a non-empty "
                    "'profile'."
                )

            rules.append(
                {
                    "window_class": (
                        window_class.strip()
                    ),
                    "profile": profile.strip(),
                }
            )

        return rules

    def set_application_rules(
        self,
        rules: list[dict[str, str]],
    ) -> None:
        if not isinstance(rules, list):
            raise ValueError(
                "Application rules must be a list."
            )

        available_profiles = set(
            self.list_profiles()
        )

        normalized_rules: list[
            dict[str, str]
        ] = []

        seen_window_classes: set[str] = set()

        for index, rule in enumerate(
            rules,
            start=1,
        ):
            if not isinstance(rule, dict):
                raise ValueError(
                    f"Application rule {index} "
                    "must be an object."
                )

            window_class = rule.get(
                "window_class"
            )

            profile = rule.get(
                "profile"
            )

            if (
                not isinstance(window_class, str)
                or not window_class.strip()
            ):
                raise ValueError(
                    f"Application rule {index} "
                    "needs a window class."
                )

            if (
                not isinstance(profile, str)
                or not profile.strip()
            ):
                raise ValueError(
                    f"Application rule {index} "
                    "needs a profile."
                )

            window_class = window_class.strip()
            profile = profile.strip()

            normalized_window_class = (
                window_class.casefold()
            )

            if (
                normalized_window_class
                in seen_window_classes
            ):
                raise ValueError(
                    "Only one rule may exist for "
                    f"'{window_class}'."
                )

            if profile not in available_profiles:
                raise FileNotFoundError(
                    "Application rule profile "
                    f"was not found: {profile}"
                )

            seen_window_classes.add(
                normalized_window_class
            )

            normalized_rules.append(
                {
                    "window_class": window_class,
                    "profile": profile,
                }
            )

        self.settings[
            "application_rules"
        ] = normalized_rules

        self._save_settings()

    def get_backlight_colors(
        self,
    ) -> dict[int, tuple[int, int, int]]:
        raw_backlight = self.profile.get(
            "backlight",
            {},
        )

        if raw_backlight is None:
            raw_backlight = {}

        if not isinstance(raw_backlight, dict):
            raise ValueError(
                "'backlight' in the profile "
                "must be an object."
            )

        colors: dict[
            int,
            tuple[int, int, int],
        ] = {}

        for layer in (1, 2, 3):
            raw_color = raw_backlight.get(
                str(layer),
                list(DEFAULT_BACKLIGHT_COLOR),
            )

            if (
                not isinstance(raw_color, list)
                or len(raw_color) != 3
            ):
                raise ValueError(
                    f"M{layer} backlight color "
                    "must contain three RGB values."
                )

            color_values = []

            for value in raw_color:
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 0 <= value <= 255
                ):
                    raise ValueError(
                        f"M{layer} backlight RGB "
                        "values must be integers "
                        "from 0 through 255."
                    )

                color_values.append(value)

            colors[layer] = (
                color_values[0],
                color_values[1],
                color_values[2],
            )

        return colors

    def set_backlight_color(
        self,
        layer: int,
        red: int,
        green: int,
        blue: int,
    ) -> None:
        if layer not in {1, 2, 3}:
            raise ValueError(
                "Backlight layer must be "
                "1, 2, or 3."
            )

        values = {
            "red": red,
            "green": green,
            "blue": blue,
        }

        for name, value in values.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 255
            ):
                raise ValueError(
                    f"{name} must be an integer "
                    "from 0 through 255."
                )

        backlight = self.profile.setdefault(
            "backlight",
            {},
        )

        if not isinstance(backlight, dict):
            raise ValueError(
                "'backlight' in the profile "
                "must be an object."
            )

        backlight[str(layer)] = [
            red,
            green,
            blue,
        ]

    def save_active_profile_backlight(
        self,
    ) -> None:
        if not self.active_profile_name:
            raise RuntimeError(
                "No active profile is loaded."
            )

        # Validate the in-memory backlight data before
        # touching the profile file.
        self.get_backlight_colors()

        raw_backlight = self.profile.get(
            "backlight",
            {},
        )

        if not isinstance(raw_backlight, dict):
            raise ValueError(
                "'backlight' in the profile "
                "must be an object."
            )

        profile_path = self._profile_path(
            self.active_profile_name
        )

        # Read the currently saved profile from disk so
        # unrelated unsaved editor changes are not written.
        disk_profile = _read_json(
            profile_path
        )

        disk_profile["backlight"] = (
            copy.deepcopy(raw_backlight)
        )

        # Validate the merged profile before replacing the
        # real profile file.
        validation_path = profile_path.with_name(
            f".{profile_path.name}.validate"
        )

        try:
            _write_json_atomic(
                validation_path,
                disk_profile,
            )

            load_profile_file(
                validation_path
            )

        finally:
            validation_path.unlink(
                missing_ok=True
            )

        _write_json_atomic(
            profile_path,
            disk_profile,
        )

        self._profile_mtime_ns = _mtime_ns(
            profile_path
        )

    def get_record_macro_delays(
        self,
    ) -> bool:
        value = self.settings.get(
            "record_macro_delays",
            True,
        )

        if not isinstance(
            value,
            bool,
        ):
            return True

        return value


    def save_record_macro_delays(
        self,
        enabled: bool,
    ) -> None:
        if not isinstance(
            enabled,
            bool,
        ):
            raise ValueError(
                "record_macro_delays must be a boolean."
            )

        self.settings[
            "record_macro_delays"
        ] = enabled

        self._save_settings()

    def get_show_macro_recording_on_lcd(
        self,
    ) -> bool:
        value = self.settings.get(
            "show_macro_recording_on_lcd",
            False,
        )

        if not isinstance(
            value,
            bool,
        ):
            return False

        return value


    def save_show_macro_recording_on_lcd(
        self,
        enabled: bool,
    ) -> None:
        if not isinstance(
            enabled,
            bool,
        ):
            raise ValueError(
                "show_macro_recording_on_lcd "
                "must be a boolean."
            )

        self.settings[
            "show_macro_recording_on_lcd"
        ] = enabled

        self._save_settings()

    def get_show_profile_activation_on_lcd(
        self,
    ) -> bool:
        value = self.settings.get(
            "show_profile_activation_on_lcd",
            False,
        )

        if not isinstance(
            value,
            bool,
        ):
            return False

        return value


    def save_show_profile_activation_on_lcd(
        self,
        enabled: bool,
    ) -> None:
        if not isinstance(
            enabled,
            bool,
        ):
            raise ValueError(
                "show_profile_activation_on_lcd "
                "must be a boolean."
            )

        self.settings[
            "show_profile_activation_on_lcd"
        ] = enabled

        self._save_settings()

    def get_verify_onboard_writes(
        self,
    ) -> bool:
        value = self.settings.get(
            "verify_onboard_writes",
            False,
        )

        if not isinstance(
            value,
            bool,
        ):
            return False

        return value


    def save_verify_onboard_writes(
        self,
        enabled: bool,
    ) -> None:
        if not isinstance(
            enabled,
            bool,
        ):
            raise ValueError(
                "verify_onboard_writes "
                "must be a boolean."
            )

        self.settings[
            "verify_onboard_writes"
        ] = enabled

        self._save_settings()

    def get_joystick_settings(
        self,
    ) -> dict[str, Any]:
        joystick = self.profile.get(
            "joystick"
        )

        if joystick is None:
            return copy.deepcopy(
                DEFAULT_JOYSTICK_SETTINGS
            )

        if not isinstance(
            joystick,
            dict,
        ):
            raise ValueError(
                "'joystick' in the active profile "
                "must be an object."
            )

        return copy.deepcopy(
            joystick
        )

    def save_joystick_settings(
        self,
        joystick: dict[str, Any],
    ) -> None:
        if not self.active_profile_name:
            raise RuntimeError(
                "No active profile is loaded."
            )

        if self._active_profile_is_transient:
            raise RuntimeError(
                "An onboard runtime profile cannot "
                "be saved directly. Copy it from "
                "the G13 first."
            )

        if not isinstance(
            joystick,
            dict,
        ):
            raise ValueError(
                "Joystick settings must be an object."
            )

        mode = joystick.get(
            "mode"
        )

        if mode not in {
            "bindings",
            "mouse",
        }:
            raise ValueError(
                "Joystick mode must be "
                "'bindings' or 'mouse'."
            )

        number_fields = (
            "start_deadzone",
            "stop_deadzone",
            "left_gain",
            "right_gain",
            "up_gain",
            "down_gain",
            "center_alpha",
        )

        values: dict[str, float] = {}

        for field in number_fields:
            value = joystick.get(
                field
            )

            if (
                not isinstance(
                    value,
                    (int, float),
                )
                or isinstance(
                    value,
                    bool,
                )
            ):
                raise ValueError(
                    f"Joystick setting '{field}' "
                    "must be a number."
                )

            values[field] = float(
                value
            )

        start_deadzone = values[
            "start_deadzone"
        ]

        stop_deadzone = values[
            "stop_deadzone"
        ]

        if not (
            0.0
            <= stop_deadzone
            < start_deadzone
            < 1.0
        ):
            raise ValueError(
                "Deadzone values must satisfy:\n\n"
                "0 ≤ stop deadzone "
                "< start deadzone < 1"
            )

        for gain_name in (
            "left_gain",
            "right_gain",
            "up_gain",
            "down_gain",
        ):
            if not (
                0.1
                <= values[gain_name]
                <= 10.0
            ):
                raise ValueError(
                    f"{gain_name} must be between "
                    "0.1 and 10.0."
                )

        if not (
            0.0
            < values["center_alpha"]
            <= 1.0
        ):
            raise ValueError(
                "center_alpha must be greater than "
                "0 and no greater than 1."
            )

        normalized_joystick = {
            "mode": mode,
            **values,
        }

        self.profile[
            "joystick"
        ] = copy.deepcopy(
            normalized_joystick
        )

        profile_path = self._profile_path(
            self.active_profile_name
        )

        # Read the saved profile so joystick settings can be
        # updated without committing unrelated unsaved editor
        # changes.
        disk_profile = _read_json(
            profile_path
        )

        disk_profile[
            "joystick"
        ] = copy.deepcopy(
            normalized_joystick
        )

        validation_path = profile_path.with_name(
            f".{profile_path.name}.validate"
        )

        try:
            _write_json_atomic(
                validation_path,
                disk_profile,
            )

            load_profile_file(
                validation_path
            )

        finally:
            validation_path.unlink(
                missing_ok=True
            )

        _write_json_atomic(
            profile_path,
            disk_profile,
        )

        self._profile_mtime_ns = _mtime_ns(
            profile_path
        )

    def save_active_profile(self) -> None:
        if not self.active_profile_name:
            raise RuntimeError(
                "No active profile is loaded."
            )

        if self._active_profile_is_transient:
            raise RuntimeError(
                "An onboard runtime profile cannot "
                "be saved directly. Copy it from "
                "the G13 first."
            )

        profile_path = self._profile_path(
            self.active_profile_name
        )

        # Validate the edited profile before replacing the real
        # profile file.
        validation_path = profile_path.with_name(
            f".{profile_path.name}.validate"
        )

        try:
            _write_json_atomic(
                validation_path,
                self.profile,
            )

            load_profile_file(
                validation_path
            )

        finally:
            validation_path.unlink(
                missing_ok=True
            )

        _write_json_atomic(
            profile_path,
            self.profile,
        )

        # Rebuild the resolved runtime bindings.
        self.set_active_profile(
            self.active_profile_name,
            persist=False,
        )

    def set_binding(
        self,
        layer: int,
        button: str,
        action: Action,
    ) -> None:
        if layer not in {1, 2, 3}:
            raise ValueError(
                "Layer must be 1, 2, or 3."
            )

        if not isinstance(button, str) or not button:
            raise ValueError(
                "Button name must be a non-empty string."
            )

        resolved_action = resolve_action(action)

        layer_text = str(layer)

        raw_layers = self.profile.setdefault(
            "layers",
            {},
        )

        raw_layer = raw_layers.setdefault(
            layer_text,
            {},
        )

        raw_layer[button] = copy.deepcopy(action)
        self.bindings[layer][button] = resolved_action

    def clear_binding(
        self,
        layer: int,
        button: str,
    ) -> None:
        if layer not in {1, 2, 3}:
            raise ValueError(
                "Layer must be 1, 2, or 3."
            )

        layer_text = str(layer)

        raw_layers = self.profile.get(
            "layers",
            {},
        )

        raw_layer = raw_layers.get(
            layer_text,
            {},
        )

        if isinstance(raw_layer, dict):
            raw_layer.pop(button, None)

        self.bindings[layer].pop(
            button,
            None,
        )

    def create_profile(
        self,
        display_name: str,
        filename: str | None = None,
        activate: bool = False,
    ) -> str:
        if not isinstance(display_name, str):
            raise ValueError(
                "Profile name must be a string."
            )

        display_name = display_name.strip()

        if not display_name:
            raise ValueError(
                "Profile name cannot be empty."
            )

        if filename is None:
            filename = self._available_profile_filename(
                display_name
            )

        filename = _validate_profile_filename(
            filename
        )

        profile_path = self._profile_path(filename)

        if profile_path.exists():
            raise FileExistsError(
                f"Profile already exists: {filename}"
            )

        new_profile = {
            "name": display_name,
            "lock_while_running": False,
            "joystick": copy.deepcopy(
                DEFAULT_JOYSTICK_SETTINGS
            ),
            "backlight": {
                "1": [255, 255, 255],
                "2": [255, 255, 255],
                "3": [255, 255, 255],
            },
            "layers": {
                "1": {},
                "2": {},
                "3": {},
            },
        }

        _write_json_atomic(
            profile_path,
            new_profile,
        )

        if activate:
            self.set_active_profile(
                filename,
                persist=False,
            )

        return filename

    def store_profile_data(
        self,
        profile_data: dict,
        filename: str,
        replace: bool = False,
    ) -> str:
        if not isinstance(
            profile_data,
            dict,
        ):
            raise ValueError(
                "Profile data must be an object."
            )

        filename = _validate_profile_filename(
            filename
        )

        profile_path = self._profile_path(
            filename
        )

        if (
            profile_path.exists()
            and not replace
        ):
            raise FileExistsError(
                f"Profile already exists: "
                f"{filename}"
            )

        stored_profile = copy.deepcopy(
            profile_data
        )

        validation_path = (
            profile_path.with_name(
                f".{profile_path.name}.validate"
            )
        )

        try:
            _write_json_atomic(
                validation_path,
                stored_profile,
            )

            load_profile_file(
                validation_path
            )

        finally:
            validation_path.unlink(
                missing_ok=True
            )

        _write_json_atomic(
            profile_path,
            stored_profile,
        )

        return filename

    def rename_profile(
        self,
        source_filename: str,
        display_name: str,
        new_filename: str | None = None,
    ) -> str:
        source_filename = _validate_profile_filename(
            source_filename
        )

        if not isinstance(display_name, str):
            raise ValueError(
                "Profile name must be a string."
            )

        display_name = display_name.strip()

        if not display_name:
            raise ValueError(
                "Profile name cannot be empty."
            )

        source_path = self._profile_path(
            source_filename
        )

        if not source_path.exists():
            raise FileNotFoundError(
                f"Profile was not found: "
                f"{source_filename}"
            )

        if new_filename is None:
            new_filename = source_filename

        new_filename = _validate_profile_filename(
            new_filename
        )

        destination_path = self._profile_path(
            new_filename
        )

        if (
            destination_path != source_path
            and destination_path.exists()
        ):
            raise FileExistsError(
                f"Profile already exists: "
                f"{new_filename}"
            )

        profile, _ = load_profile_file(
            source_path
        )

        renamed_profile = copy.deepcopy(
            profile
        )

        renamed_profile["name"] = display_name

        _write_json_atomic(
            destination_path,
            renamed_profile,
        )

# Verify the renamed profile before changing any
# references or removing the old file.
        load_profile_file(
            destination_path
        )

        filename_changed = (
            destination_path
                       != source_path
        )

        was_active = (
            source_filename
            == self.active_profile_name
        )

        settings_changed = False

        if filename_changed:
            saved_fallback = self.settings.get(
                "active_profile"
            )

            if (
                saved_fallback
                == source_filename
            ):
                self.settings[
                    "active_profile"
                ] = new_filename

                settings_changed = True

            locked_profile = self.settings.get(
                "locked_profile"
            )

            if (
                locked_profile
                == source_filename
            ):
                self.settings[
                    "locked_profile"
                ] = new_filename

                settings_changed = True

            rules = (
                self.get_application_rules()
            )

            rules_changed = False

            for rule in rules:
                if (
                    rule["profile"]
                    == source_filename
                ):
                    rule["profile"] = (
                        new_filename
                    )

                    rules_changed = True

            if rules_changed:
                self.settings[
                    "application_rules"
                ] = rules

                settings_changed = True

        # Reload the renamed profile when it is currently open
        # for editing. Do not automatically make it the saved
        # fallback.
        if was_active:
            self.set_active_profile(
                new_filename,
                persist=False,
            )

        if settings_changed:
            self._save_settings()

        if filename_changed:
            source_path.unlink()

        return new_filename

    def duplicate_profile(
        self,
        source_filename: str,
        display_name: str,
        filename: str | None = None,
        activate: bool = False,
    ) -> str:
        source_filename = _validate_profile_filename(
            source_filename
        )

        if not isinstance(display_name, str):
            raise ValueError(
                "Profile name must be a string."
            )

        display_name = display_name.strip()

        if not display_name:
            raise ValueError(
                "Profile name cannot be empty."
            )

        source_path = self._profile_path(
            source_filename
        )

        if not source_path.exists():
            raise FileNotFoundError(
                f"Source profile was not found: "
                f"{source_filename}"
            )

        if filename is None:
            filename = self._available_profile_filename(
                display_name
            )

        filename = _validate_profile_filename(
            filename
        )

        destination_path = self._profile_path(
            filename
        )

        if destination_path.exists():
            raise FileExistsError(
                f"Profile already exists: {filename}"
            )

        source_profile, _ = load_profile_file(
            source_path
        )

        duplicate = copy.deepcopy(
            source_profile
        )

        duplicate["name"] = display_name
        duplicate["copied_from"] = source_filename

        _write_json_atomic(
            destination_path,
            duplicate,
        )

        # Confirm the new profile is valid before using it.
        load_profile_file(
            destination_path
        )

        if activate:
            self.set_active_profile(
                filename,
                persist=False,
            )

        return filename

    def delete_profile(
        self,
        filename: str,
    ) -> None:
        filename = _validate_profile_filename(
            filename
        )

        if filename == self.active_profile_name:
            raise ValueError(
                "The active profile cannot be deleted. "
                "Switch profiles first."
            )

        profile_path = self._profile_path(
            filename
        )

        if not profile_path.exists():
            raise FileNotFoundError(
                f"Profile was not found: {filename}"
            )

        other_profiles = [
            profile_name
            for profile_name in self.list_profiles()
            if profile_name != filename
        ]

        if not other_profiles:
            raise ValueError(
                "The last remaining profile "
                "cannot be deleted."
            )

        settings_changed = False

        saved_fallback = self.settings.get(
            "active_profile"
        )

        if saved_fallback == filename:
            if (
                self.active_profile_name
                in other_profiles
            ):
                replacement_profile = (
                    self.active_profile_name
                )
            else:
                replacement_profile = (
                    other_profiles[0]
                )

            self.settings[
                "active_profile"
            ] = replacement_profile

            settings_changed = True

        if (
            self.settings.get("locked_profile")
            == filename
        ):
            self.settings[
                "locked_profile"
            ] = None

            settings_changed = True

        rules = self.get_application_rules()

        filtered_rules = [
            rule
            for rule in rules
            if rule["profile"] != filename
        ]

        if len(filtered_rules) != len(rules):
            self.settings[
                "application_rules"
            ] = filtered_rules

            settings_changed = True

        if settings_changed:
            self._save_settings()

        profile_path.unlink()
