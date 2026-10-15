from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import ANY, call, patch

import install_device_permissions as service


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="zombiecc-permission-test-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_rule_status_missing_identical_and_different(self):
        rule = self.root / "rule.rules"
        self.assertEqual(
            service.rule_status(rule, b"expected\n"),
            "missing",
        )

        rule.write_bytes(b"expected\n")

        self.assertEqual(
            service.rule_status(rule, b"expected\n"),
            "identical",
        )
        self.assertEqual(
            service.rule_status(rule, b"other\n"),
            "different",
        )

    def test_rule_status_rejects_symlink(self):
        target = self.root / "target"
        target.write_bytes(b"expected\n")

        rule = self.root / "rule.rules"
        rule.symlink_to(target)

        with self.assertRaisesRegex(
            service.PermissionInstallError,
            "regular file",
        ):
            service.rule_status(
                rule,
                b"expected\n",
            )

    def test_find_g13_hidraw_matches_usb_ids(self):
        sysfs = self.root / "sys/class/hidraw"
        g13 = self.root / "sys/devices/usb/g13"
        other = self.root / "sys/devices/usb/other"

        g13.mkdir(parents=True)
        other.mkdir(parents=True)

        (g13 / "idVendor").write_text("046d\n")
        (g13 / "idProduct").write_text("c21c\n")
        (other / "idVendor").write_text("1234\n")
        (other / "idProduct").write_text("5678\n")

        for name, target in (
            ("hidraw3", g13),
            ("hidraw4", other),
        ):
            entry = sysfs / name
            entry.mkdir(parents=True)
            (entry / "device").symlink_to(
                target,
                target_is_directory=True,
            )

        with patch.object(
            service,
            "HIDRAW_SYSFS",
            sysfs,
        ):
            self.assertEqual(
                service.find_g13_hidraw(),
                [
                    (
                        Path("/dev/hidraw3"),
                        sysfs / "hidraw3",
                    )
                ],
            )

    def test_add_input_group_prefers_ujust(self):
        with patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/ujust",
        ), patch.object(
            service,
            "run",
        ) as run, patch.object(
            service,
            "sudo_command",
        ) as sudo:
            service.add_input_group(
                "powers"
            )

        run.assert_called_once_with(
            [
                "/usr/bin/ujust",
                "add-user-to-input-group",
                "add",
            ]
        )
        sudo.assert_not_called()

    def test_add_input_group_uses_existing_local_group(self):
        group_file = self.root / "group"
        group_file.write_text("input:x:104:\n")

        before = SimpleNamespace(
            gr_name="input",
            gr_passwd="x",
            gr_gid=104,
            gr_mem=[],
        )
        after = SimpleNamespace(
            gr_name="input",
            gr_passwd="x",
            gr_gid=104,
            gr_mem=["powers"],
        )

        with patch.object(
            service,
            "ETC_GROUP",
            group_file,
        ), patch.object(
            service.shutil,
            "which",
            return_value=None,
        ), patch.object(
            service,
            "input_group",
            side_effect=[before, after],
        ), patch.object(
            service,
            "sudo_command",
        ) as sudo:
            service.add_input_group(
                "powers"
            )

        sudo.assert_called_once_with(
            "usermod",
            "-aG",
            "input",
            "powers",
        )

    def test_add_input_group_creates_atomic_local_group(self):
        group_file = self.root / "group"
        group_file.write_text(
            "wheel:x:10:powers\n"
        )

        before = SimpleNamespace(
            gr_name="input",
            gr_passwd="x",
            gr_gid=104,
            gr_mem=[],
        )
        after = SimpleNamespace(
            gr_name="input",
            gr_passwd="x",
            gr_gid=104,
            gr_mem=["powers"],
        )

        with patch.object(
            service,
            "ETC_GROUP",
            group_file,
        ), patch.object(
            service.shutil,
            "which",
            return_value=None,
        ), patch.object(
            service,
            "input_group",
            side_effect=[before, after],
        ), patch.object(
            service,
            "sudo_command",
        ) as sudo:
            service.add_input_group(
                "powers"
            )

        self.assertEqual(
            sudo.call_args_list,
            [
                call(
                    "/bin/sh",
                    "-c",
                    'printf "%s\\n" "$1" >> "$2"',
                    "zombiecc-input-group",
                    "input:x:104:",
                    str(group_file),
                ),
                call(
                    "usermod",
                    "-aG",
                    "input",
                    "powers",
                ),
            ],
        )

    def test_add_input_group_refuses_conflicting_local_gid(self):
        group_file = self.root / "group"
        group_file.write_text(
            "render:x:104:\n"
        )

        group = SimpleNamespace(
            gr_name="input",
            gr_passwd="x",
            gr_gid=104,
            gr_mem=[],
        )

        with patch.object(
            service,
            "ETC_GROUP",
            group_file,
        ), patch.object(
            service.shutil,
            "which",
            return_value=None,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "sudo_command",
        ) as sudo:
            with self.assertRaisesRegex(
                service.PermissionInstallError,
                "already uses that GID for render",
            ):
                service.add_input_group(
                    "powers"
                )

        sudo.assert_not_called()

    def test_add_input_group_verifies_membership(self):
        group_file = self.root / "group"
        group_file.write_text(
            "input:x:104:\n"
        )

        group = SimpleNamespace(
            gr_name="input",
            gr_passwd="x",
            gr_gid=104,
            gr_mem=[],
        )

        with patch.object(
            service,
            "ETC_GROUP",
            group_file,
        ), patch.object(
            service.shutil,
            "which",
            return_value=None,
        ), patch.object(
            service,
            "input_group",
            side_effect=[group, group],
        ), patch.object(
            service,
            "sudo_command",
        ) as sudo:
            with self.assertRaisesRegex(
                service.PermissionInstallError,
                "Failed to add powers to the input group",
            ):
                service.add_input_group(
                    "powers"
                )

        sudo.assert_called_once_with(
            "usermod",
            "-aG",
            "input",
            "powers",
        )

    def test_identical_rule_is_not_republished(self):
        rule = self.root / "rule.rules"
        rule.write_bytes(
            b"expected\n"
        )

        with patch.object(
            service,
            "sudo_command",
        ) as sudo, contextlib.redirect_stdout(
            io.StringIO()
        ):
            service.install_rule(
                rule,
                b"expected\n",
            )

        sudo.assert_not_called()

    def test_dry_run_with_sufficient_permissions_changes_nothing(self):
        uinput = self.root / "uinput"
        uinput.write_bytes(b"")

        g13 = self.root / "hidraw9"
        g13.write_bytes(b"")

        group = SimpleNamespace(
            gr_gid=104
        )

        def access(path, _mode):
            return Path(path) in (
                uinput,
                g13,
            )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=True,
        ), patch.object(
            service,
            "group_would_grant",
            return_value=True,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[
                (
                    g13,
                    self.root / "sys/hidraw9",
                )
            ],
        ), patch.object(
            service.os,
            "access",
            side_effect=access,
        ), patch.object(
            service,
            "add_input_group",
        ) as add_group, patch.object(
            service,
            "install_rule",
        ) as install_rule, patch.object(
            service,
            "reload_rules",
        ) as reload_rules, contextlib.redirect_stdout(
            io.StringIO()
        ) as output:
            self.assertEqual(
                service.main(
                    ["--dry-run"]
                ),
                0,
            )

        self.assertIn(
            "Permissions are already sufficient",
            output.getvalue(),
        )
        add_group.assert_not_called()
        install_rule.assert_not_called()
        reload_rules.assert_not_called()

    def test_mint_like_dry_run_plans_group_and_both_rules(self):
        uinput = self.root / "uinput"
        uinput.write_bytes(b"")

        g13 = self.root / "hidraw1"
        g13.write_bytes(b"")

        uinput_rule = (
            self.root
            / "rules/99-zombiecc-uinput.rules"
        )
        g13_rule = (
            self.root
            / "rules/99-zombiecc-g13.rules"
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_RULE",
            uinput_rule,
        ), patch.object(
            service,
            "G13_RULE",
            g13_rule,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=False,
        ), patch.object(
            service,
            "group_would_grant",
            return_value=False,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[
                (
                    g13,
                    self.root / "sys/hidraw1",
                )
            ],
        ), patch.object(
            service.os,
            "access",
            return_value=False,
        ), patch.object(
            service,
            "add_input_group",
        ) as add_group, patch.object(
            service,
            "install_rule",
        ) as install_rule, patch.object(
            service,
            "reload_rules",
        ) as reload_rules, contextlib.redirect_stdout(
            io.StringIO()
        ) as output:
            self.assertEqual(
                service.main(
                    ["--dry-run"]
                ),
                0,
            )

        text = output.getvalue()

        self.assertIn(
            "add powers to input group",
            text,
        )
        self.assertIn(
            str(uinput_rule),
            text,
        )
        self.assertIn(
            str(g13_rule),
            text,
        )

        add_group.assert_not_called()
        install_rule.assert_not_called()
        reload_rules.assert_not_called()

    def test_different_existing_rule_is_refused(self):
        uinput = self.root / "uinput"
        uinput.write_bytes(b"")

        rule = (
            self.root
            / "99-zombiecc-uinput.rules"
        )
        rule.write_bytes(
            b"different\n"
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_RULE",
            rule,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=True,
        ), patch.object(
            service,
            "group_would_grant",
            return_value=False,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[],
        ), patch.object(
            service.os,
            "access",
            return_value=False,
        ), contextlib.redirect_stdout(
            io.StringIO()
        ), contextlib.redirect_stderr(
            io.StringIO()
        ) as error:
            self.assertEqual(
                service.main(
                    ["--dry-run"]
                ),
                1,
            )

        self.assertIn(
            "Different existing rule",
            error.getvalue(),
        )

    def test_apply_calls_only_planned_changes(self):
        uinput = self.root / "uinput"
        uinput.write_bytes(b"")

        g13 = self.root / "hidraw1"
        g13.write_bytes(b"")

        uinput_rule = (
            self.root
            / "rules/99-zombiecc-uinput.rules"
        )
        g13_rule = (
            self.root
            / "rules/99-zombiecc-g13.rules"
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_RULE",
            uinput_rule,
        ), patch.object(
            service,
            "G13_RULE",
            g13_rule,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=False,
        ), patch.object(
            service,
            "group_would_grant",
            return_value=False,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[
                (
                    g13,
                    self.root / "sys/hidraw1",
                )
            ],
        ), patch.object(
            service.os,
            "access",
            return_value=False,
        ), patch.object(
            service,
            "add_input_group",
        ) as add_group, patch.object(
            service,
            "install_rule",
        ) as install_rule, patch.object(
            service,
            "reload_rules",
        ) as reload_rules, contextlib.redirect_stdout(
            io.StringIO()
        ):
            self.assertEqual(
                service.main(
                    ["--yes"]
                ),
                0,
            )

        add_group.assert_called_once_with(
            "powers"
        )

        self.assertEqual(
            install_rule.call_args_list,
            [
                call(
                    uinput_rule,
                    service.UINPUT_CONTENT,
                ),
                call(
                    g13_rule,
                    service.G13_CONTENT,
                ),
            ],
        )

        reload_rules.assert_called_once_with(
            True,
            [
                (
                    g13,
                    self.root / "sys/hidraw1",
                )
            ],
        )

    def test_module_load_status_missing_identical_and_different(self):
        config = self.root / "zombiecc-uinput.conf"

        self.assertEqual(
            service.module_load_status(
                config,
                service.UINPUT_MODULE_CONTENT,
            ),
            "missing",
        )

        config.write_bytes(
            service.UINPUT_MODULE_CONTENT
        )

        self.assertEqual(
            service.module_load_status(
                config,
                service.UINPUT_MODULE_CONTENT,
            ),
            "identical",
        )

        config.write_bytes(
            b"different\n"
        )

        self.assertEqual(
            service.module_load_status(
                config,
                service.UINPUT_MODULE_CONTENT,
            ),
            "different",
        )

    def test_load_uinput_module_uses_modprobe_and_verifies_state(self):
        uinput = self.root / "uinput"
        uinput_sysfs = self.root / "sys/class/misc/uinput"

        def sudo_side_effect(*arguments):
            self.assertEqual(
                arguments,
                (
                    "/usr/bin/modprobe",
                    "uinput",
                ),
            )
            uinput_sysfs.mkdir(parents=True)
            uinput.write_bytes(b"")

        with patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/modprobe",
        ), patch.object(
            service,
            "sudo_command",
            side_effect=sudo_side_effect,
        ) as sudo, contextlib.redirect_stdout(
            io.StringIO()
        ):
            service.load_uinput_module()

        sudo.assert_called_once_with(
            "/usr/bin/modprobe",
            "uinput",
        )

    def test_missing_uinput_dry_run_plans_module_and_rule(self):
        uinput = self.root / "uinput"
        uinput_sysfs = self.root / "sys/class/misc/uinput"

        module_file = (
            self.root
            / "modules-load.d/zombiecc-uinput.conf"
        )

        uinput_rule = (
            self.root
            / "rules/99-zombiecc-uinput.rules"
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service,
            "UINPUT_MODULE_FILE",
            module_file,
        ), patch.object(
            service,
            "UINPUT_RULE",
            uinput_rule,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=True,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[],
        ), patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/modprobe",
        ), patch.object(
            service,
            "install_module_load",
        ) as install_module_load, patch.object(
            service,
            "load_uinput_module",
        ) as load_uinput_module, patch.object(
            service,
            "install_rule",
        ) as install_rule, patch.object(
            service,
            "reload_rules",
        ) as reload_rules, contextlib.redirect_stdout(
            io.StringIO()
        ) as output:
            self.assertEqual(
                service.main(
                    ["--dry-run"]
                ),
                0,
            )

        text = output.getvalue()

        self.assertIn(
            "kernel module not loaded",
            text,
        )
        self.assertIn(
            str(module_file),
            text,
        )
        self.assertIn(
            "load uinput kernel module",
            text,
        )
        self.assertIn(
            str(uinput_rule),
            text,
        )

        install_module_load.assert_not_called()
        load_uinput_module.assert_not_called()
        install_rule.assert_not_called()
        reload_rules.assert_not_called()

    def test_missing_uinput_apply_persists_loads_and_activates(self):
        uinput = self.root / "uinput"
        uinput_sysfs = self.root / "sys/class/misc/uinput"

        module_file = (
            self.root
            / "modules-load.d/zombiecc-uinput.conf"
        )

        uinput_rule = (
            self.root
            / "rules/99-zombiecc-uinput.rules"
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        def load_module():
            uinput.write_bytes(b"")

        def access(path, mode):
            return (
                Path(path) == uinput
                and mode == os.W_OK
                and uinput.exists()
            )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service,
            "UINPUT_MODULE_FILE",
            module_file,
        ), patch.object(
            service,
            "UINPUT_RULE",
            uinput_rule,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=True,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[],
        ), patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/modprobe",
        ), patch.object(
            service.os,
            "access",
            side_effect=access,
        ), patch.object(
            service,
            "install_module_load",
        ) as install_module_load, patch.object(
            service,
            "load_uinput_module",
            side_effect=load_module,
        ) as load_uinput_module, patch.object(
            service,
            "install_rule",
        ) as install_rule, patch.object(
            service,
            "reload_rules",
        ) as reload_rules, contextlib.redirect_stdout(
            io.StringIO()
        ):
            self.assertEqual(
                service.main(
                    ["--yes"]
                ),
                0,
            )

        install_module_load.assert_called_once_with(
            module_file,
            service.UINPUT_MODULE_CONTENT,
        )

        load_uinput_module.assert_called_once_with()

        install_rule.assert_called_once_with(
            uinput_rule,
            service.UINPUT_CONTENT,
        )

        reload_rules.assert_called_once_with(
            True,
            [],
        )

    def test_loaded_uinput_without_devnode_is_refused(self):
        uinput = self.root / "uinput"

        uinput_sysfs = (
            self.root
            / "sys/class/misc/uinput"
        )
        uinput_sysfs.mkdir(
            parents=True
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=True,
        ), contextlib.redirect_stderr(
            io.StringIO()
        ) as error:
            self.assertEqual(
                service.main(
                    ["--dry-run"]
                ),
                1,
            )

        self.assertIn(
            "kernel module is loaded",
            error.getvalue(),
        )

    def test_different_module_load_file_is_refused(self):
        uinput = self.root / "uinput"
        uinput_sysfs = self.root / "sys/class/misc/uinput"

        module_file = (
            self.root
            / "modules-load.d/zombiecc-uinput.conf"
        )
        module_file.parent.mkdir(
            parents=True
        )
        module_file.write_bytes(
            b"different\n"
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service,
            "UINPUT_MODULE_FILE",
            module_file,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=True,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[],
        ), patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/modprobe",
        ), contextlib.redirect_stdout(
            io.StringIO()
        ), contextlib.redirect_stderr(
            io.StringIO()
        ) as error:
            self.assertEqual(
                service.main(
                    ["--dry-run"]
                ),
                1,
            )

        self.assertIn(
            "Different existing module-load file",
            error.getvalue(),
        )

    def test_module_load_status_rejects_symlink(self):
        target = self.root / "target.conf"
        target.write_bytes(
            service.UINPUT_MODULE_CONTENT
        )

        config = self.root / "zombiecc-uinput.conf"
        config.symlink_to(target)

        with self.assertRaisesRegex(
            service.PermissionInstallError,
            "regular file",
        ):
            service.module_load_status(
                config,
                service.UINPUT_MODULE_CONTENT,
            )

    def test_install_module_load_publishes_persistent_config(self):
        directory = self.root / "modules-load.d"
        directory.mkdir()

        config = directory / "zombiecc-uinput.conf"

        def sudo_side_effect(*arguments):
            if arguments[0] == "cp":
                source = Path(arguments[-2])
                destination = Path(arguments[-1])
                destination.write_bytes(
                    source.read_bytes()
                )
            elif arguments[0] == "chmod":
                Path(arguments[-1]).chmod(0o644)

        with patch.object(
            service,
            "sudo_command",
            side_effect=sudo_side_effect,
        ) as sudo, contextlib.redirect_stdout(
            io.StringIO()
        ):
            service.install_module_load(
                config,
                service.UINPUT_MODULE_CONTENT,
            )

        self.assertEqual(
            config.read_bytes(),
            service.UINPUT_MODULE_CONTENT,
        )

        self.assertEqual(
            config.stat().st_mode & 0o777,
            0o644,
        )

        self.assertEqual(
            sudo.call_args_list,
            [
                call(
                    "cp",
                    "--no-clobber",
                    "--",
                    unittest.mock.ANY,
                    str(config),
                ),
                call(
                    "chmod",
                    "0644",
                    "--",
                    str(config),
                ),
            ],
        )

    def test_load_uinput_module_refuses_missing_device_after_modprobe(self):
        uinput = self.root / "uinput"
        uinput_sysfs = self.root / "sys/class/misc/uinput"
        uinput_sysfs.mkdir(parents=True)

        with patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/modprobe",
        ), patch.object(
            service,
            "sudo_command",
        ) as sudo:
            with self.assertRaisesRegex(
                service.PermissionInstallError,
                "did not appear",
            ):
                service.load_uinput_module()

        sudo.assert_called_once_with(
            "/usr/bin/modprobe",
            "uinput",
        )

    def test_load_uinput_module_refuses_missing_sysfs_after_modprobe(self):
        uinput = self.root / "uinput"
        uinput_sysfs = self.root / "sys/class/misc/uinput"
        uinput.write_bytes(b"")

        with patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/modprobe",
        ), patch.object(
            service,
            "sudo_command",
        ) as sudo:
            with self.assertRaisesRegex(
                service.PermissionInstallError,
                "did not appear",
            ):
                service.load_uinput_module()

        sudo.assert_called_once_with(
            "/usr/bin/modprobe",
            "uinput",
        )

    def test_host_prerequisites_accept_required_commands(self):
        with patch.object(
            service.shutil,
            "which",
            side_effect=lambda command: f"/usr/bin/{command}",
        ):
            service.check_host_prerequisites()

    def test_host_prerequisites_reports_all_missing_commands(self):
        def which(command):
            if command in {
                "distrobox",
                "podman",
            }:
                return None

            return f"/usr/bin/{command}"

        with patch.object(
            service.shutil,
            "which",
            side_effect=which,
        ):
            with self.assertRaisesRegex(
                service.PermissionInstallError,
                "distrobox, podman",
            ):
                service.check_host_prerequisites()

    def test_static_uinput_node_without_loaded_module_plans_load(self):
        uinput = self.root / "uinput"
        uinput.write_bytes(b"")

        uinput_sysfs = self.root / "sys/class/misc/uinput"

        module_file = (
            self.root
            / "modules-load.d/zombiecc-uinput.conf"
        )

        uinput_rule = (
            self.root
            / "rules/99-zombiecc-uinput.rules"
        )

        group = SimpleNamespace(
            gr_gid=104
        )

        with patch.object(
            service,
            "host_guard",
        ), patch.object(
            service,
            "UINPUT",
            uinput,
        ), patch.object(
            service,
            "UINPUT_SYSFS",
            uinput_sysfs,
        ), patch.object(
            service,
            "UINPUT_MODULE_FILE",
            module_file,
        ), patch.object(
            service,
            "UINPUT_RULE",
            uinput_rule,
        ), patch.object(
            service,
            "input_group",
            return_value=group,
        ), patch.object(
            service,
            "current_username",
            return_value="powers",
        ), patch.object(
            service,
            "input_group_active",
            return_value=True,
        ), patch.object(
            service,
            "find_g13_hidraw",
            return_value=[],
        ), patch.object(
            service.shutil,
            "which",
            return_value="/usr/bin/modprobe",
        ), contextlib.redirect_stdout(
            io.StringIO()
        ) as output:
            self.assertEqual(
                service.main(
                    ["--dry-run"]
                ),
                0,
            )

        text = output.getvalue()

        self.assertIn(
            "kernel module not loaded",
            text,
        )
        self.assertIn(
            str(module_file),
            text,
        )
        self.assertIn(
            "load uinput kernel module",
            text,
        )
        self.assertIn(
            str(uinput_rule),
            text,
        )

    def test_find_system_command_prefers_path(self):
        with patch.object(
            service.shutil,
            "which",
            return_value="/custom/bin/modprobe",
        ):
            self.assertEqual(
                service.find_system_command("modprobe"),
                "/custom/bin/modprobe",
            )

    def test_find_system_command_finds_sbin_command_outside_path(self):
        with patch.object(
            service.shutil,
            "which",
            return_value=None,
        ), patch.object(
            service.Path,
            "is_file",
            return_value=True,
        ), patch.object(
            service.os,
            "access",
            return_value=True,
        ):
            self.assertEqual(
                service.find_system_command("modprobe"),
                "/usr/sbin/modprobe",
            )

if __name__ == "__main__":
    unittest.main()
