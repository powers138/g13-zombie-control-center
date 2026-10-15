"""Hardware-free regression tests for profiles_to_go.py onboard writes.

Put this file next to profiles_to_go.py, then run:
    python3 test_onboard_write_safety.py

Only Python's standard library is used. The device module is replaced before
profiles_to_go.py is loaded; opening a real G13 is explicitly blocked. All memory
reads/writes and lock operations in these tests use in-memory replacements.
No settings, profile files, cache files, or device memory are changed.

An alternate source path can be supplied for development:
    python3 test_onboard_write_safety.py --source /path/to/profiles_to_go.py
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_storage_module(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"Cannot find source file: {path}")
    device_stub = types.ModuleType("device")
    device_stub.open_g13 = Mock(
        side_effect=AssertionError("Hardware access is blocked by this test.")
    )
    name = "_g13_storage_safety_test_target"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load source file: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with patch.dict(sys.modules, {"device": device_stub}):
        spec.loader.exec_module(module)
    return module


ptg = None


def entries(*names: str, revision: int = 1):
    return [
        {
            "filename": f"{name.lower()}.json",
            "profile": {
                "name": name,
                "revision": revision,
                "layers": {"1": {"G1": {"name": "Test", "key": "KEY_A"}}},
            },
        }
        for name in names
    ]


def xml_data(*names: str, revision: int = 1) -> bytes:
    # Test names are fixed ASCII strings, not user-supplied XML.
    profiles = "".join(
        f'<profile name="{name}" guid="{{{name.upper()}}}" revision="{revision}" />'
        for name in names
    )
    return f"<profiles>{profiles}</profiles>".encode("utf-8")


def bank(profiles, generation: int, storage_format: str = "zombie") -> bytes:
    if storage_format == "zombie":
        return ptg.build_zombie_bank(profiles, generation)
    return ptg.build_bank(profiles, generation)


def initial_memory(current: str = "A", storage_format: str = "zombie") -> bytes:
    profiles = entries("Original") if storage_format == "zombie" else xml_data("Original")
    newer = bank(profiles, 10, storage_format)
    older = bank(profiles, 9, storage_format)
    return newer + older if current == "A" else older + newer


def replace_bank(memory: bytes, name: str, data: bytes) -> bytes:
    start = ptg._bank_start(name)
    return memory[:start] + data + memory[start + ptg.BANK_SIZE:]


class MemoryDevice:
    """Memory-only I/O with assertions that each call happens inside the lock."""

    def __init__(self, memory: bytes):
        self.memory = memory
        self.locked = False
        self.events = []
        self.read_count = 0
        self.writes = []
        self.on_enter = None
        self.on_write = None
        self.read_errors = {}
        self.read_overrides = {}
        self.write_error = None

    @contextmanager
    def lock(self):
        if self.locked:
            raise AssertionError("Unexpected nested lock.")
        self.locked = True
        self.events.append("lock")
        try:
            if self.on_enter is not None:
                self.on_enter(self)
            yield
        finally:
            self.events.append("unlock")
            self.locked = False

    def read(self):
        if not self.locked:
            raise AssertionError("Read happened outside the lock.")
        self.read_count += 1
        self.events.append("read")
        if self.read_count in self.read_errors:
            raise self.read_errors[self.read_count]
        return self.read_overrides.get(self.read_count, self.memory)

    def write(self, name, data):
        if not self.locked:
            raise AssertionError("Write happened outside the lock.")
        self.events.append(f"write:{name}")
        self.writes.append((name, data))
        if self.write_error is not None:
            raise self.write_error
        self.memory = replace_bank(self.memory, name, data)
        if self.on_write is not None:
            self.memory = self.on_write(self.memory, name, data)


class OnboardWriteSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global ptg
        if ptg is None:
            ptg = load_storage_module(
                Path(__file__).resolve().with_name("profiles_to_go.py")
            )

    def setUp(self):
        self.before = initial_memory()
        self.device = MemoryDevice(self.before)
        for target, replacement in (
            ("_profiles_to_go_lock", self.device.lock),
            ("_read_onboard_memory_unlocked", self.device.read),
            ("_write_bank_unlocked", self.device.write),
        ):
            patcher = patch.object(ptg, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        sleep_patcher = patch.object(ptg.time, "sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

        ptg.open_g13.reset_mock()

    def tearDown(self):
        ptg.open_g13.assert_not_called()
        self.assertFalse(self.device.locked)

    def use_memory(self, memory):
        self.before = memory
        self.device.memory = memory

    def save(self, profiles=None, **kwargs):
        if profiles is None:
            profiles = entries("Original", revision=2)
        return ptg.replace_with_zombie_profiles(
            profiles, before_memory=self.before, **kwargs
        )

    def direct_write(self, **kwargs):
        arguments = {
            "before_memory": self.before,
            "new_bank": ptg.build_zombie_bank(entries("Original", revision=2), 11),
            "target_bank_name": "B",
            "expected_generation": 11,
            "expected_format": "zombie",
            "expected_profile_names": ("Original",),
        }
        arguments.update(kwargs)
        return ptg._write_and_verify(**arguments)

    def test_native_write_a_to_b_checks_then_writes_then_verifies(self):
        status = self.save()
        expected = ptg.build_zombie_bank(entries("Original", revision=2), 11)
        self.assertEqual(status.memory_image, self.before[:ptg.BANK_SIZE] + expected)
        self.assertEqual(status.current_bank.name, "B")
        self.assertEqual(status.current_bank.generation, 11)
        self.assertEqual(self.device.events, ["lock", "read", "write:B", "read", "unlock"])

    def test_verified_write_settles_before_readback(self):
        self.sleep.side_effect = lambda delay: self.device.events.append(("sleep", delay))

        self.save()

        self.assertEqual(
            self.device.events,
            ["lock", "read", "write:B",
             ("sleep", ptg.ONBOARD_WRITE_READBACK_DELAY), "read", "unlock"])

    def test_native_write_b_to_a_preserves_previous_bank(self):
        self.use_memory(initial_memory("B"))
        status = self.save()
        self.assertEqual(status.current_bank.name, "A")
        self.assertEqual(status.memory_image[ptg.BANK_SIZE:], self.before[ptg.BANK_SIZE:])
        self.assertEqual(status.memory_image[:ptg.BANK_SIZE], self.device.writes[0][1])

    def test_logitech_write_matches_all_target_bytes(self):
        self.use_memory(initial_memory(storage_format="logitech"))
        status = ptg.replace_with_logitech_xml(
            xml_data("Original", revision=2), before_memory=self.before
        )
        self.assertEqual(status.current_bank.storage_format, "logitech")
        self.assertEqual(status.memory_image[ptg.BANK_SIZE:], self.device.writes[0][1])
        self.assertEqual(status.memory_image[:ptg.BANK_SIZE], self.before[:ptg.BANK_SIZE])

    def test_native_add_retains_existing_profile(self):
        status = ptg.add_zombie_onboard_profile(
            "new.json", {"name": "New"}, before_memory=self.before
        )
        self.assertEqual(status.current_bank.profile_names, ("Original", "New"))
        self.assertEqual(self.device.read_count, 2)

    def test_native_remove_retains_order(self):
        self.use_memory(ptg.build_zombie_bank(entries("One", "Two", "Three"), 10)
                        + self.before[ptg.BANK_SIZE:])
        status = ptg.remove_onboard_profile(2, before_memory=self.before)
        self.assertEqual(status.current_bank.profile_names, ("One", "Three"))

    def test_logitech_add_retains_existing_profile(self):
        self.use_memory(initial_memory(storage_format="logitech"))
        status = ptg.add_logitech_onboard_profile(xml_data("New"), before_memory=self.before)
        self.assertEqual(status.current_bank.profile_names, ("Original", "New"))
        self.assertEqual(self.device.read_count, 2)

    def test_logitech_remove_retains_order(self):
        self.use_memory(ptg.build_bank(xml_data("One", "Two", "Three"), 10)
                        + ptg.build_bank(xml_data("Original"), 9))
        status = ptg.remove_onboard_profile(2, before_memory=self.before)
        self.assertEqual(status.current_bank.profile_names, ("One", "Three"))

    def test_empty_native_set_can_be_written(self):
        status = self.save([])
        self.assertEqual(status.current_bank.profile_names, ())

    def test_invalid_inactive_bank_can_be_replaced(self):
        self.use_memory(self.before[:ptg.BANK_SIZE] + bytes(ptg.BANK_SIZE))
        status = self.save()
        self.assertEqual(status.current_bank.name, "B")
        self.assertEqual(status.bank_errors, ())

    def test_generation_wraparound_still_selects_new_bank(self):
        self.use_memory(ptg.build_zombie_bank(entries("Original"), 0xFFFFFFFE)
                        + ptg.build_zombie_bank(entries("Original"), 0xFFFFFFFF))
        status = self.save()
        self.assertEqual((status.current_bank.name, status.current_bank.generation), ("A", 0))

    def test_stale_snapshot_cannot_overwrite_a_newer_save(self):
        first = ptg.add_zombie_onboard_profile(
            "first.json", {"name": "First"}, before_memory=self.before
        )
        newer_memory = self.device.memory
        with self.assertRaisesRegex(RuntimeError, "memory has changed"):
            ptg.add_zombie_onboard_profile(
                "second.json", {"name": "Second"}, before_memory=self.before
            )
        self.assertEqual(len(self.device.writes), 1)
        self.assertEqual(self.device.memory, newer_memory)
        # A deliberate retry using the new snapshot retains both additions.
        retried = ptg.add_zombie_onboard_profile(
            "second.json", {"name": "Second"}, before_memory=first.memory_image
        )
        self.assertEqual(retried.current_bank.profile_names, ("Original", "First", "Second"))

    def test_stale_snapshot_is_rejected_even_without_readback(self):
        changed = ptg.build_zombie_bank(entries("Changed"), 12)
        self.device.memory = replace_bank(self.before, "B", changed)
        with self.assertRaisesRegex(RuntimeError, "memory has changed"):
            self.save(verify_readback=False)
        self.assertEqual(self.device.writes, [])

    def test_change_only_in_inactive_bank_padding_is_stale(self):
        self.device.memory = self.before[:-1] + b"\x01"
        with self.assertRaisesRegex(RuntimeError, "memory has changed"):
            self.save()
        self.assertEqual(self.device.writes, [])

    def test_changed_payload_with_same_snapshot_metadata_is_stale(self):
        self.device.memory = replace_bank(
            self.before, "A", ptg.build_zombie_bank(entries("Original", revision=99), 10)
        )
        with self.assertRaisesRegex(RuntimeError, "memory has changed"):
            self.save()
        self.assertEqual(self.device.writes, [])

    def test_snapshot_comparison_occurs_after_acquiring_lock(self):
        self.device.on_enter = lambda d: setattr(d, "memory", self.before[:-1] + b"\x01")
        with self.assertRaisesRegex(RuntimeError, "memory has changed"):
            self.save()
        self.assertEqual(self.device.events, ["lock", "read", "unlock"])
        self.assertEqual(self.device.writes, [])

    def test_preflight_read_failure_prevents_write(self):
        self.device.read_errors[1] = RuntimeError("Simulated preflight failure")
        with self.assertRaisesRegex(RuntimeError, "preflight failure"):
            self.save()
        self.assertEqual(self.device.writes, [])

    def test_write_failure_is_not_retried(self):
        self.device.write_error = RuntimeError("Simulated write failure")
        with self.assertRaisesRegex(RuntimeError, "write failure"):
            self.save()
        self.assertEqual(len(self.device.writes), 1)
        self.assertEqual(self.device.read_count, 1)

    def test_readback_failure_is_not_retried(self):
        self.device.read_errors[2] = RuntimeError("Simulated readback failure")
        with self.assertRaisesRegex(RuntimeError, "readback failure"):
            self.save()
        self.assertEqual(len(self.device.writes), 1)

    def test_different_valid_native_payload_with_same_metadata_is_rejected(self):
        wrong_bank = ptg.build_zombie_bank(entries("Original", revision=99), 11)
        self.device.on_write = lambda memory, name, data: replace_bank(memory, name, wrong_bank)
        with self.assertRaisesRegex(RuntimeError, "does not exactly match"):
            self.save()
        self.assertEqual(len(self.device.writes), 1)

    def test_different_valid_xml_payload_with_same_metadata_is_rejected(self):
        self.use_memory(initial_memory(storage_format="logitech"))
        wrong_bank = ptg.build_bank(xml_data("Original", revision=99), 11)
        self.device.on_write = lambda memory, name, data: replace_bank(memory, name, wrong_bank)
        with self.assertRaisesRegex(RuntimeError, "does not exactly match"):
            ptg.replace_with_logitech_xml(xml_data("Original", revision=2),
                                          before_memory=self.before)

    def test_changed_target_padding_is_rejected(self):
        self.device.on_write = lambda memory, name, data: replace_bank(
            memory, name, data[:-1] + b"\x01"
        )
        with self.assertRaisesRegex(RuntimeError, "does not exactly match"):
            self.save()

    def test_short_readback_is_rejected(self):
        self.device.read_overrides[2] = self.before[:ptg.BANK_SIZE]
        with self.assertRaises(RuntimeError):
            self.save()

    def test_previous_active_bank_damage_is_still_rejected(self):
        def damage_previous(memory, name, data):
            # Change unused padding, preserving parseable profile metadata.
            position = ptg.BANK_SIZE - 1
            return memory[:position] + b"\x01" + memory[position + 1:]
        self.device.on_write = damage_previous
        with self.assertRaisesRegex(RuntimeError, "previous active bank changed"):
            self.save()

    def test_unverified_mode_checks_snapshot_but_skips_readback(self):
        self.device.on_write = lambda memory, name, data: replace_bank(
            memory, name, data[:-1] + b"\x01"
        )
        status = self.save(verify_readback=False)
        self.assertEqual(self.device.read_count, 1)
        self.assertEqual(len(self.device.writes), 1)
        self.assertNotEqual(status.memory_image, self.device.memory)
        self.assertEqual(status.memory_image[ptg.BANK_SIZE:], self.device.writes[0][1])

    def test_active_bank_target_is_rejected_before_any_device_io(self):
        with self.assertRaisesRegex(RuntimeError, "inactive bank"):
            self.direct_write(target_bank_name="A")
        self.assertEqual(self.device.events, [])
        self.assertEqual(self.device.memory, self.before)

    def test_unknown_target_is_rejected_before_any_device_io(self):
        with self.assertRaises((ValueError, RuntimeError)):
            self.direct_write(target_bank_name="C")
        self.assertEqual(self.device.events, [])

    def test_invalid_candidate_is_rejected_before_any_device_io(self):
        with self.assertRaises(RuntimeError):
            self.direct_write(new_bank=bytes(ptg.BANK_SIZE))
        self.assertEqual(self.device.events, [])

    def test_wrong_candidate_generation_is_rejected_before_any_device_io(self):
        with self.assertRaisesRegex(RuntimeError, "wrong generation"):
            self.direct_write(expected_generation=12)
        self.assertEqual(self.device.events, [])

    def test_wrong_candidate_format_is_rejected_before_any_device_io(self):
        with self.assertRaisesRegex(RuntimeError, "wrong storage format"):
            self.direct_write(expected_format="logitech")
        self.assertEqual(self.device.events, [])

    def test_wrong_candidate_names_are_rejected_before_any_device_io(self):
        with self.assertRaisesRegex(RuntimeError, "expected list"):
            self.direct_write(expected_profile_names=("Different",))
        self.assertEqual(self.device.events, [])

    def test_duplicate_native_filename_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "already exists"):
            ptg.add_zombie_onboard_profile("ORIGINAL.JSON", {"name": "Different"},
                                           before_memory=self.before)
        self.assertEqual(self.device.events, [])

    def test_five_profile_limit_still_enforced(self):
        self.use_memory(ptg.build_zombie_bank(entries("One", "Two", "Three", "Four", "Five"), 10)
                        + self.before[ptg.BANK_SIZE:])
        with self.assertRaisesRegex(RuntimeError, "five onboard profiles"):
            ptg.add_zombie_onboard_profile("six.json", {"name": "Six"},
                                           before_memory=self.before)
        self.assertEqual(self.device.events, [])

    def test_unsupplied_snapshot_uses_read_path_and_rechecks_under_write_lock(self):
        status = ptg.replace_with_zombie_profiles(entries("Original", revision=2))
        self.assertEqual(status.current_bank.name, "B")
        self.assertEqual(self.device.events,
                         ["lock", "read", "unlock", "lock", "read", "write:B", "read", "unlock"])

class OnboardHardwareTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global ptg

        if ptg is None:
            ptg = load_storage_module(
                Path(__file__).resolve().with_name("profiles_to_go.py"))

    def test_whole_read_retries_page_sequence_failure(self):
        expected = bytes(ptg.TOTAL_MEMORY_SIZE)
        failure = RuntimeError("Expected onboard page 0x01, received 0x00.")

        with patch.object(ptg, "_read_onboard_memory_once",
                          side_effect=[failure, expected]) as read_once:
            with patch.object(ptg.time, "sleep") as sleep:
                result = ptg._read_onboard_memory_unlocked()

        self.assertEqual(result, expected)
        self.assertEqual(read_once.call_count, 2)
        sleep.assert_called_once_with(ptg.ONBOARD_READ_RETRY_DELAY)

    def test_whole_read_retries_os_error(self):
        expected = bytes(ptg.TOTAL_MEMORY_SIZE)
        failure = OSError("Simulated HID read failure")

        with patch.object(ptg, "_read_onboard_memory_once",
                          side_effect=[failure, expected]) as read_once:
            with patch.object(ptg.time, "sleep") as sleep:
                result = ptg._read_onboard_memory_unlocked()

        self.assertEqual(result, expected)
        self.assertEqual(read_once.call_count, 2)
        sleep.assert_called_once_with(ptg.ONBOARD_READ_RETRY_DELAY)

    def test_whole_read_stops_after_retry_limit(self):
        failure = RuntimeError("Simulated persistent read failure")

        with patch.object(ptg, "_read_onboard_memory_once",
                          side_effect=failure) as read_once:
            with patch.object(ptg.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "persistent read failure"):
                    ptg._read_onboard_memory_unlocked()

        self.assertEqual(read_once.call_count, ptg.ONBOARD_READ_ATTEMPTS)
        self.assertEqual(sleep.call_count, ptg.ONBOARD_READ_ATTEMPTS - 1)

        for call in sleep.call_args_list:
            self.assertEqual(call.args, (ptg.ONBOARD_READ_RETRY_DELAY,))

    def test_bank_writer_paces_every_page(self):
        bank_data = ptg.build_zombie_bank(entries("Original", revision=2), 11)
        device = Mock()
        device.send_feature_report.side_effect = lambda report: len(report)

        with patch.object(ptg, "open_g13", return_value=device):
            with patch.object(ptg.time, "sleep") as sleep:
                ptg._write_bank_unlocked("A", bank_data)

        self.assertEqual(device.send_feature_report.call_count, 129)
        self.assertEqual(sleep.call_count, 129)
        self.assertEqual(sleep.call_args_list[0].args, (0.75,))

        for call in sleep.call_args_list[1:]:
            self.assertEqual(call.args, (ptg.ONBOARD_WRITE_PAGE_DELAY,))

        reports = [
            call.args[0]
            for call in device.send_feature_report.call_args_list
        ]

        self.assertEqual(reports[0], bytes((0x04, 0x01, 0x00, 0x00, 0x00)))
        self.assertEqual([report[1] for report in reports[1:]], list(range(128)))
        device.close.assert_called_once_with()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().with_name("profiles_to_go.py"))
    args, remaining = parser.parse_known_args()
    try:
        ptg = load_storage_module(args.source.resolve())
    except (OSError, RuntimeError) as error:
        raise SystemExit(str(error))
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
