from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import install_desktop
import install_driver_service
import uninstall
import update_app as payload_tools


class FakeTools:
    def __init__(self, paths: uninstall.Paths):
        self.paths = paths
        self.distrobox = "/test/bin/distrobox"
        self.desktop = paths.payload.home / "Desktop"
        self.desktop.mkdir(parents=True, exist_ok=True)
        self.state = {
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "UnitFileState": "enabled",
            "FragmentPath": str(paths.service.unit),
            "MainPID": "7100",
        }
        self.container = {
            "OCIRuntime": "crun",
            "Config": {
                "Image": payload_tools.IMAGE,
                "Labels": {
                    "manager": "distrobox",
                    "io.zombiecc.installer": payload_tools.OWNER,
                    "distrobox.unshare_groups": "0",
                },
                "Annotations": {
                    "run.oci.keep_original_groups": "1",
                },
            },
            "State": {
                "Running": True,
                "Paused": False,
            },
        }
        self.actions: list[str] = []

    def service_state(self) -> dict[str, str]:
        return dict(self.state)

    def disable_and_stop_service(self) -> None:
        self.actions.append("disable-stop")
        self.state.update(
            ActiveState="inactive",
            SubState="dead",
            UnitFileState="disabled",
            MainPID="0",
        )

    def daemon_reload(self) -> None:
        self.actions.append("daemon-reload")
        if not self.paths.service.unit.exists():
            self.state = {
                "LoadState": "not-found",
                "ActiveState": "inactive",
                "SubState": "dead",
                "UnitFileState": "",
                "FragmentPath": "",
                "MainPID": "0",
            }

    def container_info(self) -> dict | None:
        return self.container

    def remove_container(self) -> None:
        self.actions.append("remove-container")
        self.container = None

    def configured_desktop(
        self,
        layout: install_desktop.Layout,
    ) -> Path | None:
        return self.desktop

    def refresh_desktop_cache(self) -> None:
        self.actions.append("refresh-desktop")


class UninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="zombiecc-uninstall-test-"
        )
        root = Path(self.temp.name)
        home = root / "home/tester"
        data = home / ".local/share"
        config = home / ".config"
        state = home / ".local/state"
        for path in (data, config, state):
            path.mkdir(parents=True, exist_ok=True)

        payload = payload_tools.Layout(home, data, state)
        desktop = install_desktop.Layout(home, data, config, state)
        service = install_driver_service.Layout(payload, config)
        self.paths = uninstall.Paths(payload, desktop, service)
        self.tools = FakeTools(self.paths)
        self.proc = root / "proc"
        self.proc.mkdir()
        self.make_install()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(
        self,
        path: Path,
        content: bytes | str,
        mode: int = 0o600,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode()
        path.write_bytes(content)
        path.chmod(mode)

    def make_install(self) -> None:
        app = self.paths.payload.app
        app.mkdir(parents=True)

        required = sorted(payload_tools.REQUIRED)
        for name in required:
            if name == "app-files.txt":
                continue
            content = "{}\n" if name.endswith(".json") else f"# {name}\n"
            self.write(app / name, content)

        self.write(
            app / "app-files.txt",
            "\n".join(required) + "\n",
        )
        self.write(app / ".venv/pyvenv.cfg", "home = /usr/bin\n")
        self.write(
            self.paths.payload.launcher,
            "#!/bin/bash\nexit 0\n",
            0o755,
        )

        payload = payload_tools.Payload.load(app)
        launcher = payload_tools.read_blob(self.paths.payload.launcher)
        assert launcher is not None
        self.write(
            app / payload_tools.MARKER,
            payload.digest(launcher.data) + "\n",
        )

        self.paths.profiles.mkdir(parents=True)
        self.write(self.paths.profiles / "default.json", "{}\n")
        self.write(self.paths.lock, b"")
        self.paths.config_root.mkdir(parents=True)
        self.write(self.paths.config_root / "settings.json", "{}\n")
        self.paths.state_root.mkdir(parents=True)
        self.write(self.paths.state_root / "keep.log", "test\n")

        desktop_content = install_desktop.entry_contents(self.paths.desktop)
        self.write(self.paths.desktop.menu, desktop_content, 0o644)
        self.write(
            self.tools.desktop / install_desktop.ENTRY_NAME,
            desktop_content,
            0o755,
        )

        self.write(
            self.paths.service.session_entry,
            install_driver_service.session_entry_contents(self.paths.service))

        self.write(
            self.paths.service.unit,
            install_driver_service.unit_contents(
                self.paths.service,
                self.tools.distrobox,
            ),
        )
        self.write(
            self.paths.service.dropin,
            install_driver_service.dropin_contents(),
        )

    def build(self, purge: bool = False) -> uninstall.Plan:
        return uninstall.build_plan(
            self.paths,
            self.tools,
            purge_user_data=purge,
        )

    def test_editor_scan_ignores_non_python_process(
        self,
    ) -> None:
        process = self.proc / "6453"
        process.mkdir()

        (process / "cmdline").write_bytes(
            (
                b"/usr/bin/kwin_wayland\0"
                b"--wayland-fd\0"
                b"7\0"
            )
        )

        with patch.object(
            uninstall.os,
            "readlink",
            side_effect=PermissionError(
                "denied"
            ),
        ):
            self.assertFalse(
                uninstall.editor_is_running(
                    self.paths,
                    proc_root=self.proc,
                )
            )

    def test_editor_scan_detects_relative_python_editor(
        self,
    ) -> None:
        process = self.proc / "7000"
        process.mkdir()

        (process / "cmdline").write_bytes(
            b"python3\0editor.py\0"
        )

        with patch.object(
            uninstall.os,
            "readlink",
            return_value=str(
                self.paths.payload.app
            ),
        ):
            self.assertTrue(
                uninstall.editor_is_running(
                    self.paths,
                    proc_root=self.proc,
                )
            )

    def test_preflight_allows_active_managed_driver_processes(self) -> None:
        supervisor = self.proc / "7100"
        supervisor.mkdir()
        (supervisor / "cmdline").write_bytes(
            b"/usr/bin/python3\0-s\0-u\0"
            + os.fsencode(
                self.paths.payload.app / "driver_service.py"
            )
            + b"\0"
        )

        driver = self.proc / "7101"
        driver.mkdir()
        (driver / "cmdline").write_bytes(
            os.fsencode(
                self.paths.payload.app / ".venv/bin/python"
            )
            + b"\0-u\0g13-test.py\0"
        )
        (driver / "cwd").symlink_to(self.paths.payload.app)
        (driver / "environ").write_bytes(
            b"G13_DRIVER_SERVICE_CONTROL=stdin-pipe-v1\0"
            b"SYSTEMD_EXEC_PID=7100\0"
        )

        uninstall.check_uninstall_preflight_idle(
            self.paths,
            self.tools.service_state(),
            proc_root=self.proc,
        )

    def test_session_bridge_refuses_before_any_mutation(self) -> None:
        process = self.proc / "7200"
        process.mkdir()
        (process / "cmdline").write_bytes(
            b"/usr/bin/python3\0-s\0-u\0"
            + os.fsencode(
                self.paths.payload.app
                / "driver_session_bridge.py"
            )
            + b"\0"
        )

        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "driver session bridge",
        ):
            uninstall.execute(
                self.build(),
                self.tools,
                proc_root=self.proc,
            )

        self.assertEqual(self.tools.actions, [])
        self.assertEqual(
            self.tools.state["ActiveState"],
            "active",
        )
        self.assertTrue(self.paths.service.unit.is_file())
        self.assertTrue(self.paths.service.session_entry.is_file())
        self.assertTrue(self.paths.payload.app.is_dir())

    def test_stray_driver_refuses_before_any_mutation(self) -> None:
        process = self.proc / "7300"
        process.mkdir()
        (process / "cmdline").write_bytes(
            b"/usr/bin/python3\0-u\0"
            + os.fsencode(
                self.paths.payload.app / "g13-test.py"
            )
            + b"\0"
        )

        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "running from the installed application",
        ):
            uninstall.execute(
                self.build(),
                self.tools,
                proc_root=self.proc,
            )

        self.assertEqual(self.tools.actions, [])
        self.assertEqual(
            self.tools.state["ActiveState"],
            "active",
        )

    def test_plan_recognizes_full_install(self) -> None:
        plan = self.build()
        self.assertTrue(plan.container_present)
        self.assertEqual(len(plan.desktop_files), 2)
        self.assertEqual(len(plan.session_files), 1)
        self.assertEqual(len(plan.service_files), 2)

    def test_default_uninstall_preserves_user_data(self) -> None:
        uninstall.execute(
            self.build(),
            self.tools,
            proc_root=self.proc,
        )

        self.assertFalse(self.paths.payload.app.exists())
        self.assertFalse(self.paths.payload.launcher.exists())
        self.assertFalse(self.paths.desktop.menu.exists())
        self.assertFalse(
            (self.tools.desktop / install_desktop.ENTRY_NAME).exists()
        )
        self.assertFalse(self.paths.service.session_entry.exists())
        self.assertFalse(self.paths.service.unit.exists())
        self.assertFalse(self.paths.service.dropin.exists())
        self.assertIsNone(self.tools.container)

        self.assertTrue(
            (self.paths.profiles / "default.json").is_file()
        )
        self.assertTrue(
            (self.paths.config_root / "settings.json").is_file()
        )
        self.assertTrue(
            (self.paths.state_root / "keep.log").is_file()
        )
        self.assertTrue(self.paths.lock.is_file())

    def test_purge_removes_user_data(self) -> None:
        uninstall.execute(
            self.build(purge=True),
            self.tools,
            proc_root=self.proc,
        )
        self.assertFalse(self.paths.payload.base.exists())
        self.assertFalse(self.paths.config_root.exists())
        self.assertFalse(self.paths.state_root.exists())

    def test_foreign_desktop_blocks_plan(self) -> None:
        self.write(
            self.paths.desktop.menu,
            "[Desktop Entry]\nName=Other\n",
        )
        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "Different Zombie desktop entry",
        ):
            self.build()

    def test_foreign_service_blocks_plan(self) -> None:
        self.write(self.paths.service.unit, "[Unit]\nDescription=Other\n")
        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "Different Zombie production service file",
        ):
            self.build()

    def test_foreign_driver_session_entry_blocks_plan(self) -> None:
        self.write(
            self.paths.service.session_entry,
            "[Desktop Entry]\nType=Application\nName=Other\n")
        with self.assertRaisesRegex(
                uninstall.UninstallError,
                "Different Zombie driver session entry"):
            self.build()

    def test_missing_driver_session_entry_is_supported(self) -> None:
        self.paths.service.session_entry.unlink()
        plan = self.build()
        self.assertEqual(plan.session_files, ())

    def test_mismatched_container_blocks_plan(self) -> None:
        assert self.tools.container is not None
        self.tools.container["Config"]["Image"] = "example.invalid/not-zombie"
        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "does not match the installer-owned production runtime",
        ):
            self.build()

    def test_modified_payload_blocks_plan(self) -> None:
        self.write(self.paths.payload.app / "editor.py", "changed\n")
        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "fingerprint differs",
        ):
            self.build()

    def test_untracked_app_file_blocks_plan(self) -> None:
        self.write(self.paths.payload.app / "notes.txt", "keep me\n")
        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "Untracked application file",
        ):
            self.build()

    def test_update_pending_blocks_plan(self) -> None:
        self.write(self.paths.payload.pending, "{}\n")
        with self.assertRaisesRegex(
            uninstall.UninstallError,
            "unfinished update",
        ):
            self.build()

    def test_display_plan_names_preserved_and_untouched_data(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            uninstall.display_plan(self.build())
        text = output.getvalue()
        self.assertIn("Preserved by default", text)
        self.assertIn("Steam Non-Steam shortcuts", text)
        self.assertIn("G13 onboard memory", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
