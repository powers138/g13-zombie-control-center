"""Hardware-free regression tests for live onboard profile refresh.

Save beside runtime_profile_controller.py and g13-test.py, then run:
    python3 test_onboard_runtime_refresh.py

Only the Python standard library is required. This script reads source files
and executes selected class/function definitions and the existing reload-loop
block in an isolated namespace. It NEVER imports or starts g13-test.py.
Profile managers, device operations, event connections, Lua, and storage/cache
operations are replaced with in-memory fakes. No settings, profiles, cache,
USB device, virtual input device, or onboard memory are modified.

The real storage parser, Logitech converter, and ProfileManager are not tested
here. This suite checks the controller, driver refresh handlers, and driver's
existing Lua/backlight/joystick reload decisions with simulated dependencies.

Development option:
    python3 test_onboard_runtime_refresh.py --project /path/to/source/copies
"""
from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import dataclass
from pathlib import Path
import sys
import types
from typing import Any
import unittest
from unittest.mock import Mock


@dataclass(frozen=True)
class FakeWindow:
    window_id: str
    window_class: str
    pid: int | None
    title: str


def profile(name: str = "Onboard One", revision: int = 1) -> dict[str, Any]:
    return {
        "name": name,
        "revision": revision,
        "bindings": {"M1": {"G1": {"name": "Test", "key": f"KEY_{revision}"}}},
        "script": f"-- {name}, revision {revision}",
        "backlight": {1: [revision, 20, 30]},
        "joystick": {"mode": "keys", "revision": revision},
    }


class FakeManager:
    """In-memory API used by the real controller and driver definitions."""

    def __init__(self):
        self.local_profiles = {
            "default.json": profile("Default", 0),
            "game.json": profile("Game", 3),
        }
        self.settings = {"active_profile": "default.json"}
        self.active_profile_name = "default.json"
        self.profile = copy.deepcopy(self.local_profiles["default.json"])
        self.locked_profile = None
        self.automatic = False
        self.rules = []
        self.lock_while_running = set()
        self.data_loads = []
        self.local_loads = []
        self.fail_next_data_load = False
        self.mutate_data_argument = False
        self.fail_next_local_load = False
        self.files_changed = False

    def list_profiles(self):
        return list(self.local_profiles)

    def get_locked_profile(self):
        return self.locked_profile

    def set_locked_profile(self, filename):
        self.locked_profile = filename

    def get_profile_lock_while_running(self, filename):
        return filename in self.lock_while_running

    def set_active_profile_data(self, identifier, data):
        if self.fail_next_data_load:
            self.fail_next_data_load = False
            raise RuntimeError("Simulated profile-load failure")
        self.data_loads.append((identifier, copy.deepcopy(data)))
        if self.mutate_data_argument:
            data["runtime_only"] = {"normalized": True}
            data["bindings"]["M1"]["G1"]["normalized"] = True
        # Intentionally retain the argument, to detect aliasing bugs.
        self.profile = data
        self.active_profile_name = identifier

    def set_active_profile(self, filename, persist=True):
        if self.fail_next_local_load:
            self.fail_next_local_load = False
            raise RuntimeError("Simulated local-load failure")
        self.profile = copy.deepcopy(self.local_profiles[filename])
        self.active_profile_name = filename
        self.local_loads.append((filename, persist))
        if persist:
            self.settings["active_profile"] = filename

    def reload_if_changed(self):
        result = self.files_changed
        self.files_changed = False
        return result

    def get_show_profile_activation_on_lcd(self):
        return False

    def get_backlight_colors(self):
        return copy.deepcopy(self.profile["backlight"])

    def get_joystick_settings(self):
        return copy.deepcopy(self.profile["joystick"])

    def get_profile_script(self):
        return self.profile["script"]


class FakeSwitcher:
    def __init__(self, manager):
        self.manager = manager

    def is_enabled(self):
        return self.manager.automatic

    def get_rules(self):
        return copy.deepcopy(self.manager.rules)

    def get_manual_profile(self):
        return self.manager.settings["active_profile"]


def selected_definitions(path: Path, names: set[str]) -> ast.Module:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    body = [node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    found = {node.name for node in body}
    if found != names:
        raise RuntimeError(f"Missing definitions in {path.name}: {sorted(names - found)}")
    return ast.Module(body=body, type_ignores=[])


def load_controller(path: Path):
    module_name = "_g13_onboard_refresh_controller_test"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module
    module.__dict__.update({
        "dataclass": dataclass, "copy": copy, "Any": Any,
        "ActiveWindow": FakeWindow, "get_active_window": lambda: None,
        "ProfileManager": FakeManager, "ApplicationProfileSwitcher": FakeSwitcher,
        "os": types.SimpleNamespace(kill=Mock(return_value=None)),
        "print": Mock(),
    })
    definitions = selected_definitions(path, {"RuntimeProfileStatus", "RuntimeProfileController"})
    exec(compile(definitions, str(path), "exec"), module.__dict__)
    return module


def load_driver_definitions(path: Path):
    names = {
        "_apply_onboard_runtime_status", "refresh_onboard_runtime_profiles",
        "reload_onboard_runtime_profiles_from_cache", "process_driver_commands",
        "publish_runtime_profile_status", "reload_lua_profile", "apply_backlight",
    }
    definitions = selected_definitions(path, names)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    # Extract the actual periodic reload block, not a rewritten approximation.
    ticks = [node for node in ast.walk(main)
             if isinstance(node, ast.If)
             and any(isinstance(item, ast.Name) and item.id == "last_reload_check"
                     for item in ast.walk(node.test))]
    if len(ticks) != 1:
        raise RuntimeError("Could not uniquely locate the driver's periodic reload block.")
    tick = ast.Module(body=[ticks[0]], type_ignores=[])
    return compile(definitions, str(path), "exec"), compile(tick, str(path), "exec")


CONTROLLER = None
DRIVER_DEFINITIONS = None
DRIVER_TICK = None


class ControllerRefreshTests(unittest.TestCase):
    def setUp(self):
        CONTROLLER.get_active_window = lambda: None
        self.manager = FakeManager()
        self.controller = CONTROLLER.RuntimeProfileController(self.manager)
        self.original_settings = copy.deepcopy(self.manager.settings)

    def tearDown(self):
        self.assertEqual(self.manager.settings, self.original_settings,
                         "Refresh changed the saved manual fallback")

    def activate(self, profiles=None, identifier="onboard:1", temporary=False):
        if profiles is None:
            profiles = {identifier: profile()}
        self.controller.set_onboard_profiles(profiles)
        if temporary:
            self.controller.select_temporary_profile(identifier)
        else:
            self.controller.lock_profile(identifier)
        self.assertEqual(self.manager.active_profile_name, identifier)
        self.manager.data_loads.clear()
        self.manager.local_loads.clear()

    def test_changed_active_contents_reload_once_under_same_id(self):
        self.activate()
        old_status = self.controller.get_status()
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.profile["revision"], 2)
        self.assertEqual(self.controller.get_status(), old_status)
        self.assertFalse(self.controller.check())
        self.assertEqual(len(self.manager.data_loads), 1)

    def test_each_profile_owned_setting_triggers_reload(self):
        changes = {
            "name": "Renamed", "script": "-- changed script",
            "backlight": {1: [222, 1, 2]}, "joystick": {"mode": "mouse"},
            "bindings": {"M1": {"G1": {"name": "New", "key": "KEY_Z"}}},
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                self.activate()
                updated = profile()
                updated[key] = value
                self.controller.set_onboard_profiles({"onboard:1": updated})
                self.assertTrue(self.controller.check())
                self.assertEqual(self.manager.profile[key], value)
                self.assertFalse(self.controller.check())

    def test_identical_refresh_does_not_reload(self):
        self.activate()
        for _ in range(3):
            self.controller.set_onboard_profiles({"onboard:1": copy.deepcopy(profile())})
            self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.data_loads, [])

    def test_dictionary_key_order_does_not_trigger_reload(self):
        self.activate()
        reordered = dict(reversed(list(profile().items())))
        self.controller.set_onboard_profiles({"onboard:1": reordered})
        self.assertFalse(self.controller.check())

    def test_inactive_profile_change_does_not_reload_active(self):
        self.activate({"onboard:1": profile(), "onboard:2": profile("Two")})
        self.controller.set_onboard_profiles({"onboard:1": profile(), "onboard:2": profile("Two", 2)})
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.data_loads, [])
        self.assertTrue(self.controller.lock_profile("onboard:2"))
        self.assertEqual(self.manager.profile["revision"], 2)

    def test_onboard_refresh_does_not_activate_over_manual_fallback(self):
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.active_profile_name, "default.json")
        self.assertEqual(self.manager.data_loads, [])

    def test_temporary_selection_survives_same_slot_update(self):
        self.activate(temporary=True)
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.controller.temporary_profile, "onboard:1")
        self.assertEqual(self.controller.get_status().source, "temporary")

    def test_transient_lock_survives_same_slot_update(self):
        self.activate()
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.controller.transient_locked_profile, "onboard:1")
        self.assertEqual(self.controller.get_status().source, "locked")
        self.assertIsNone(self.manager.get_locked_profile())

    def test_removed_active_locked_id_returns_to_manual(self):
        self.activate()
        self.controller.set_onboard_profiles({})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.active_profile_name, "default.json")
        self.assertIsNone(self.controller.transient_locked_profile)
        self.assertEqual(self.manager.local_loads, [("default.json", False)])
        self.assertFalse(self.controller.check())

    def test_removed_active_temporary_id_returns_to_manual(self):
        self.activate(temporary=True)
        self.controller.set_onboard_profiles({})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.active_profile_name, "default.json")
        self.assertIsNone(self.controller.temporary_profile)
        self.assertIsNone(self.controller.temporary_window_id)

    def test_removed_active_id_honors_matching_application_rule(self):
        self.activate()
        self.manager.automatic = True
        self.manager.rules = [{"window_class": "steam_app_692850", "profile": "game.json"}]
        CONTROLLER.get_active_window = lambda: FakeWindow("gamescope:692850", "steam_app_692850", None, "Game")
        self.controller.set_onboard_profiles({})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.active_profile_name, "game.json")
        self.assertEqual(self.controller.get_status().source, "automatic")

    def test_local_lock_retains_priority_during_onboard_refresh(self):
        self.activate()
        self.controller.lock_profile("game.json")
        self.manager.data_loads.clear()
        self.manager.local_loads.clear()
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.active_profile_name, "game.json")
        self.assertEqual(self.manager.data_loads, [])

    def test_removing_inactive_slot_does_not_reload_active(self):
        self.activate({"onboard:1": profile(), "onboard:2": profile("Two")})
        self.controller.set_onboard_profiles({"onboard:1": profile()})
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.data_loads, [])

    def test_reused_slot_id_loads_replacement_contents(self):
        self.activate()
        self.controller.set_onboard_profiles({"onboard:1": profile("Replacement", 7)})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.profile["name"], "Replacement")
        # Slot identity is deliberately unchanged by this patch.
        self.assertEqual(self.controller.transient_locked_profile, "onboard:1")

    def test_multiple_updates_before_check_apply_latest_only(self):
        self.activate()
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=3)})
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.profile["revision"], 3)
        self.assertEqual(len(self.manager.data_loads), 1)

    def test_update_reverted_before_check_does_not_reload(self):
        self.activate()
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.controller.set_onboard_profiles({"onboard:1": profile()})
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.data_loads, [])

    def test_failed_data_load_is_retried_on_next_check(self):
        self.activate()
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.manager.fail_next_data_load = True
        with self.assertRaisesRegex(RuntimeError, "Simulated profile-load"):
            self.controller.check()
        self.assertEqual(self.manager.profile["revision"], 1)
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.profile["revision"], 2)
        self.assertFalse(self.controller.check())

    def test_manager_normalization_does_not_change_stored_snapshot(self):
        self.manager.mutate_data_argument = True
        self.activate()
        self.assertNotIn("runtime_only", self.controller.onboard_profiles["onboard:1"])
        self.controller.set_onboard_profiles({"onboard:1": profile()})
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.data_loads, [])

    def test_mutating_manager_data_does_not_mutate_onboard_source(self):
        self.activate()
        self.manager.profile["bindings"]["M1"]["G1"]["key"] = "RUNTIME_ONLY"
        self.assertEqual(self.controller.onboard_profiles["onboard:1"]["bindings"]["M1"]["G1"]["key"], "KEY_1")
        self.controller.set_onboard_profiles({"onboard:1": profile()})
        self.assertFalse(self.controller.check())

    def test_input_profiles_are_deep_copied(self):
        incoming = {"onboard:1": profile()}
        self.activate(incoming)
        incoming["onboard:1"]["bindings"]["M1"]["G1"]["key"] = "EXTERNAL_CHANGE"
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.profile["bindings"]["M1"]["G1"]["key"], "KEY_1")

    def test_invalid_refresh_keeps_existing_profiles_and_selection(self):
        self.activate()
        with self.assertRaises(ValueError):
            self.controller.set_onboard_profiles({"onboard:1": profile(revision=2), "local.json": profile()})
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.profile["revision"], 1)
        self.assertEqual(self.controller.transient_locked_profile, "onboard:1")

    def test_different_identifiers_with_equal_contents_still_switch(self):
        self.activate({"onboard:1": profile(), "onboard:2": profile()})
        self.assertTrue(self.controller.lock_profile("onboard:2"))
        self.assertEqual(self.manager.active_profile_name, "onboard:2")
        self.assertEqual(len(self.manager.data_loads), 1)

    def test_switch_away_and_back_loads_latest_data(self):
        self.activate()
        self.controller.unlock_profile()
        self.assertEqual(self.manager.active_profile_name, "default.json")
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.assertFalse(self.controller.check())
        self.assertTrue(self.controller.lock_profile("onboard:1"))
        self.assertEqual(self.manager.profile["revision"], 2)

    def test_preexisting_same_id_without_snapshot_is_loaded_once(self):
        self.manager.active_profile_name = "onboard:1"
        self.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.controller.transient_locked_profile = "onboard:1"
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.profile["revision"], 2)
        self.assertFalse(self.controller.check())

    def test_local_automatic_match_and_manual_return_unchanged(self):
        self.manager.automatic = True
        self.manager.rules = [{"window_class": "org.test.game", "profile": "game.json"}]
        CONTROLLER.get_active_window = lambda: FakeWindow("window:1", "org.test.game", None, "Game")
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.active_profile_name, "game.json")
        self.assertFalse(self.controller.check())
        CONTROLLER.get_active_window = lambda: None
        self.assertTrue(self.controller.check())
        self.assertEqual(self.manager.active_profile_name, "default.json")


class DriverHarness:
    def __init__(self):
        CONTROLLER.get_active_window = lambda: None
        self.manager = FakeManager()
        self.controller = CONTROLLER.RuntimeProfileController(self.manager)
        self.controller.set_onboard_profiles({"onboard:1": profile()})
        self.controller.lock_profile("onboard:1")
        self.manager.data_loads.clear()
        self.manager.local_loads.clear()
        self.ns = {
            "runtime_profile_controller": self.controller,
            "profile_manager": self.manager,
            "get_onboard_profiles": lambda bank: bank.profiles,
            "convert_profile_xml": Mock(side_effect=lambda data, **kw: copy.deepcopy(data)),
            "get_onboard_status": Mock(), "get_cached_onboard_status": Mock(),
            "write_onboard_cache": Mock(), "event_publisher": Mock(),
            "lcd_runtime_ui": Mock(), "command_listener": Mock(),
            "runtime_profile_action_pending": False,
            "onboard_runtime_profiles_ready": True,
            "macro_recording_lcd_active": False,
            "lcd_test_until": 0.0, "led_test_until": 0.0,
            "profile_activation_lcd_profile": None, "profile_activation_lcd_until": 0.0,
            "refresh_lcd_status": Mock(), "apply_indicator_leds": Mock(),
            "g13_device": object(), "set_g13_backlight": Mock(),
            "lua_runtime": Mock(), "lua_lcd_messages": [],
            "current_backlight_colors": self.manager.get_backlight_colors(),
            "current_joystick_settings": self.manager.get_joystick_settings(),
            "decoder": Mock(), "get_layer": lambda: 1,
            "time": types.SimpleNamespace(monotonic=lambda: 100.0),
            "last_reload_check": 0.0,
            "last_runtime_status": self.controller.get_status(),
            "now": 1.0, "print": Mock(),
        }
        callbacks = (
            "set_lua_m_key_state press_lua_key release_lua_key play_lua_macro "
            "abort_script_macro is_lua_modifier_pressed is_lua_mouse_button_pressed "
            "is_lua_key_lock_on press_lua_mouse_button release_lua_mouse_button "
            "move_relative scroll_vertical get_lua_mouse_position move_lua_mouse_to "
            "move_lua_mouse_to_virtual show_lua_lcd_message clear_lua_lcd set_lua_backlight_color"
        )
        for name in callbacks.split():
            self.ns[name] = Mock()
        exec(DRIVER_DEFINITIONS, self.ns)

    def status(self, profiles, storage_format="zombie"):
        items = []
        for identifier, data in profiles.items():
            items.append(types.SimpleNamespace(
                slot=int(identifier.split(":")[1]), storage_format=storage_format,
                profile_data=copy.deepcopy(data) if storage_format == "zombie" else None,
                xml_data=copy.deepcopy(data) if storage_format == "logitech" else None,
            ))
        return types.SimpleNamespace(current_bank=types.SimpleNamespace(profiles=items), memory_image=b"simulated")

    def run_commands(self, *commands):
        self.ns["command_listener"].receive_all.return_value = [{"type": name} for name in commands]
        self.ns["process_driver_commands"]()

    def tick(self):
        self.ns["now"] += 1.0
        exec(DRIVER_TICK, self.ns)

    def assert_no_saved_fallback_change(self, case):
        case.assertEqual(self.manager.settings["active_profile"], "default.json")
        case.assertTrue(all(not persist for _, persist in self.manager.local_loads))


class DriverRefreshTests(unittest.TestCase):
    def setUp(self):
        self.h = DriverHarness()

    def tearDown(self):
        self.h.assert_no_saved_fallback_change(self)

    def arrange(self, command, profiles, storage_format="zombie"):
        status = self.h.status(profiles, storage_format)
        reader = "get_cached_onboard_status" if command == "reload_onboard_profiles_cache" else "get_onboard_status"
        self.h.ns[reader].return_value = status

    def assert_one_hardware_reload(self, revision):
        ns = self.h.ns
        self.assertEqual(ns["lua_runtime"].deactivate_profile.call_count, 1)
        self.assertEqual(ns["lua_runtime"].load_profile.call_count, 1)
        self.assertEqual(ns["lua_runtime"].load_profile.call_args.args,
                         (self.h.manager.active_profile_name, self.h.manager.profile["script"]))
        ns["set_g13_backlight"].assert_called_once_with(ns["g13_device"], revision, 20, 30)
        ns["decoder"].update_joystick_settings.assert_called_once_with(self.h.manager.get_joystick_settings())

    def test_cache_refresh_applies_same_id_and_queues_reload(self):
        self.arrange("reload_onboard_profiles_cache", {"onboard:1": profile(revision=2)})
        self.h.run_commands("reload_onboard_profiles_cache")
        self.assertEqual(self.h.manager.profile["revision"], 2)
        self.assertTrue(self.h.ns["runtime_profile_action_pending"])
        self.h.tick()
        self.assert_one_hardware_reload(2)
        self.assertFalse(self.h.ns["runtime_profile_action_pending"])
        self.h.tick()
        self.assertEqual(self.h.ns["lua_runtime"].load_profile.call_count, 1)
        self.h.ns["write_onboard_cache"].assert_not_called()

    def test_device_refresh_applies_same_id_and_queues_reload(self):
        self.arrange("refresh_onboard_profiles", {"onboard:1": profile(revision=2)})
        self.h.run_commands("refresh_onboard_profiles")
        self.assertTrue(self.h.ns["runtime_profile_action_pending"])
        self.h.tick()
        self.assert_one_hardware_reload(2)
        self.h.ns["write_onboard_cache"].assert_called_once_with(b"simulated")
        self.h.ns["event_publisher"].publish_runtime_profile.assert_called()

    def test_removed_active_id_queues_hardware_reload_for_both_commands(self):
        for command in ("reload_onboard_profiles_cache", "refresh_onboard_profiles"):
            with self.subTest(command=command):
                self.h = DriverHarness()
                self.arrange(command, {})
                self.h.run_commands(command)
                self.assertEqual(self.h.manager.active_profile_name, "default.json")
                self.assertTrue(self.h.ns["runtime_profile_action_pending"])
                self.assertFalse(self.h.controller.check())
                self.h.tick()
                self.assert_one_hardware_reload(0)
                self.h.assert_no_saved_fallback_change(self)

    def test_identical_refresh_never_restarts_lua(self):
        for command in ("reload_onboard_profiles_cache", "refresh_onboard_profiles"):
            self.arrange(command, {"onboard:1": profile()})
            for _ in range(2):
                self.h.run_commands(command)
                self.assertFalse(self.h.ns["runtime_profile_action_pending"])
                self.h.tick()
        self.h.ns["lua_runtime"].load_profile.assert_not_called()
        self.h.ns["set_g13_backlight"].assert_not_called()
        self.h.ns["decoder"].update_joystick_settings.assert_not_called()

    def test_inactive_slot_change_does_not_restart_lua(self):
        self.arrange("reload_onboard_profiles_cache", {"onboard:1": profile(), "onboard:2": profile("Two", 5)})
        self.h.run_commands("reload_onboard_profiles_cache")
        self.h.tick()
        self.h.ns["lua_runtime"].load_profile.assert_not_called()
        self.h.ns["decoder"].update_joystick_settings.assert_not_called()

    def test_unchanged_command_does_not_clear_existing_pending_action(self):
        self.h.ns["runtime_profile_action_pending"] = True
        self.arrange("reload_onboard_profiles_cache", {"onboard:1": profile()})
        self.h.run_commands("reload_onboard_profiles_cache")
        self.assertTrue(self.h.ns["runtime_profile_action_pending"])
        self.h.tick()
        self.h.ns["lua_runtime"].load_profile.assert_called_once()
        self.assertFalse(self.h.ns["runtime_profile_action_pending"])

    def test_two_commands_in_one_batch_keep_pending_flag(self):
        updated = {"onboard:1": profile(revision=2)}
        self.arrange("reload_onboard_profiles_cache", updated)
        self.arrange("refresh_onboard_profiles", updated)
        self.h.run_commands("reload_onboard_profiles_cache", "refresh_onboard_profiles")
        self.assertTrue(self.h.ns["runtime_profile_action_pending"])
        self.h.tick()
        self.assert_one_hardware_reload(2)

    def test_changed_profile_seen_by_periodic_check_refreshes_lcd(self):
        self.h.controller.set_onboard_profiles({"onboard:1": profile(revision=2)})
        self.assertFalse(self.h.ns["runtime_profile_action_pending"])
        self.h.tick()
        self.assert_one_hardware_reload(2)
        self.h.ns["refresh_lcd_status"].assert_called_once()
        self.assertFalse(self.h.ns["runtime_status_changed"])
        self.assertTrue(self.h.ns["runtime_profile_changed"])

    def test_name_only_change_refreshes_lcd_without_joystick_change(self):
        updated = profile()
        updated["name"] = "Renamed onboard profile"
        self.h.controller.set_onboard_profiles({"onboard:1": updated})
        self.h.tick()
        self.assertEqual(self.h.manager.profile["name"], "Renamed onboard profile")
        self.h.ns["refresh_lcd_status"].assert_called_once()
        self.h.ns["decoder"].update_joystick_settings.assert_not_called()

    def test_missing_cache_keeps_current_data_without_reload(self):
        self.h.ns["get_cached_onboard_status"].return_value = None
        self.h.run_commands("reload_onboard_profiles_cache")
        self.h.tick()
        self.assertEqual(self.h.manager.profile["revision"], 1)
        self.h.ns["lua_runtime"].load_profile.assert_not_called()
        self.h.ns["event_publisher"].publish_onboard_profiles_updated.assert_not_called()

    def test_device_read_failure_keeps_current_data_without_reload(self):
        self.h.ns["get_onboard_status"].side_effect = RuntimeError("Simulated device read failure")
        self.h.run_commands("refresh_onboard_profiles")
        self.h.tick()
        self.assertEqual(self.h.manager.profile["revision"], 1)
        self.assertFalse(self.h.ns["onboard_runtime_profiles_ready"])
        self.h.ns["lua_runtime"].load_profile.assert_not_called()

    def test_conversion_failure_does_not_partially_replace_profiles(self):
        self.arrange("reload_onboard_profiles_cache", {"onboard:1": profile(revision=2)}, "logitech")
        self.h.ns["convert_profile_xml"].side_effect = ValueError("Simulated conversion failure")
        self.h.run_commands("reload_onboard_profiles_cache")
        self.h.tick()
        self.assertEqual(self.h.manager.profile["revision"], 1)
        self.h.ns["lua_runtime"].load_profile.assert_not_called()

    def test_converted_logitech_data_uses_same_refresh_path(self):
        self.arrange("reload_onboard_profiles_cache", {"onboard:1": profile(revision=2)}, "logitech")
        self.h.run_commands("reload_onboard_profiles_cache")
        self.h.tick()
        self.assert_one_hardware_reload(2)
        self.h.ns["convert_profile_xml"].assert_called_once()
        self.h.run_commands("reload_onboard_profiles_cache")
        self.h.tick()
        self.assertEqual(self.h.ns["lua_runtime"].load_profile.call_count, 1)

    def test_lcd_and_led_test_commands_do_not_queue_profile_reload(self):
        self.h.run_commands("test_lcd", "test_leds", "request_onboard_profiles")
        self.assertFalse(self.h.ns["runtime_profile_action_pending"])
        self.h.tick()
        self.h.ns["lua_runtime"].load_profile.assert_not_called()
        self.h.ns["apply_indicator_leds"].assert_called_once()

    def test_ready_onboard_request_does_not_read_device(self):
        ns = self.h.ns

        self.h.run_commands("request_onboard_profiles")

        ns["get_onboard_status"].assert_not_called()
        ns["event_publisher"].publish_onboard_profiles_updated.assert_called_once_with()
        ns["refresh_lcd_status"].assert_not_called()
        self.assertFalse(ns["runtime_profile_action_pending"])

    def test_onboard_request_recovers_failed_startup_read(self):
        ns = self.h.ns
        ns["onboard_runtime_profiles_ready"] = False
        ns["get_onboard_status"].return_value = self.h.status(
            {"onboard:1": profile(revision=2)})

        self.h.run_commands("request_onboard_profiles")

        self.assertTrue(ns["onboard_runtime_profiles_ready"])
        ns["get_onboard_status"].assert_called_once_with()
        ns["write_onboard_cache"].assert_called_once_with(b"simulated")
        ns["event_publisher"].publish_onboard_profiles_updated.assert_called_once_with()
        self.assertEqual(self.h.manager.profile["revision"], 2)
        self.assertTrue(ns["runtime_profile_action_pending"])
        ns["event_publisher"].publish_runtime_profile.assert_called()
        ns["refresh_lcd_status"].assert_called_once_with()

    def test_onboard_request_failed_recovery_stays_not_ready(self):
        ns = self.h.ns
        ns["onboard_runtime_profiles_ready"] = False
        ns["get_onboard_status"].side_effect = RuntimeError(
            "Simulated startup read failure")

        self.h.run_commands("request_onboard_profiles")

        self.assertFalse(ns["onboard_runtime_profiles_ready"])
        ns["get_onboard_status"].assert_called_once_with()
        ns["write_onboard_cache"].assert_not_called()
        ns["event_publisher"].publish_onboard_profiles_updated.assert_not_called()
        ns["event_publisher"].publish_runtime_profile.assert_not_called()
        ns["refresh_lcd_status"].assert_not_called()
        self.assertFalse(ns["runtime_profile_action_pending"])
        self.assertEqual(self.h.manager.profile["revision"], 1)

    def test_local_file_change_still_uses_existing_reload_path(self):
        self.h.manager.files_changed = True
        self.h.tick()
        self.h.ns["lua_runtime"].load_profile.assert_called_once()
        self.h.ns["refresh_lcd_status"].assert_called_once()
        self.h.tick()
        self.assertEqual(self.h.ns["lua_runtime"].load_profile.call_count, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent,
                        help="Directory containing runtime_profile_controller.py and g13-test.py")
    args = parser.parse_args()
    global CONTROLLER, DRIVER_DEFINITIONS, DRIVER_TICK
    try:
        CONTROLLER = load_controller(args.project / "runtime_profile_controller.py")
        DRIVER_DEFINITIONS, DRIVER_TICK = load_driver_definitions(args.project / "g13-test.py")
    except (OSError, SyntaxError, RuntimeError, StopIteration) as error:
        parser.error(str(error))
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
