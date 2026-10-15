"""Desktop installer tests: temporary files, no real Zombie/containers/services.

Run beside install_desktop.py:
    python3 -m unittest -v test_install_desktop
The host guard is mocked for temporary-file CLI tests, not bypassed in production.
An optional native GLib test launches only a harmless temporary shell fixture.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import ctypes
import ctypes.util
import fcntl
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import install_desktop as desktop


class DesktopInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zombie desktop test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": "", "XDG_CURRENT_DESKTOP": ""}
        self.layout = desktop.make_layout(self.env)
        self.seed(self.layout)
        self.entries = [desktop.Entry(self.layout.menu, 0o644)]

    def seed(self, layout):
        for path, data, mode in (
            (layout.app / ".zombiecc-stage1", b"a" * 64 + b"\n", 0o600),
            (layout.app / "editor.py", b"pass\n", 0o600),
            (layout.app / ".venv/pyvenv.cfg", b"include-system-site-packages = true\n", 0o600),
            (layout.icon, b"\x89PNG\r\n\x1a\nfixture", 0o600),
            (layout.launcher, b'#!/bin/bash\nprintf "%s\\n" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "${ZOMBIECC_CONTAINER-unset}" > "$HOME/launched.txt"\n', 0o755),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)

    def install(self, entries=None):
        with redirect_stdout(io.StringIO()):
            desktop.install_entries(self.layout, entries or self.entries, self.env)

    def cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(desktop, "host_guard"), \
             redirect_stdout(output), redirect_stderr(errors):
            code = desktop.main(list(args))
        return code, output.getvalue(), errors.getvalue()

    def test_default_layout(self):
        self.assertEqual(self.layout.app, self.home / ".local/share/zombiecc/app")
        self.assertEqual(self.layout.menu, self.home / ".local/share/applications/zombie-control-center.desktop")

    def test_absolute_xdg_layout(self):
        for key, field in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state")):
            with self.subTest(key=key):
                layout = desktop.make_layout({**self.env, key: str(self.root / "custom space")})
                self.assertEqual(getattr(layout, field), self.root / "custom space")

    def test_relative_and_empty_xdg_use_fallback(self):
        for value in ("", "relative", "~/not-expanded"):
            self.assertEqual(desktop.make_layout({**self.env, "XDG_DATA_HOME": value}), self.layout)

    def test_missing_or_relative_home_is_rejected(self):
        for value in ("", "relative"):
            with self.assertRaises(desktop.DesktopInstallError):
                desktop.make_layout({"HOME": value})

    def test_control_characters_rejected(self):
        for char in ("\n", "\r", "\t", "\0", "\x7f"):
            with self.subTest(char=repr(char)):
                for function in (desktop.string_value, desktop.exec_argument):
                    with self.assertRaises(desktop.DesktopInstallError):
                        function("bad" + char + "path")
                with self.assertRaises(desktop.DesktopInstallError):
                    desktop.absolute_path("/bad" + char, "test")

    def test_container_guard(self):
        with self.assertRaisesRegex(desktop.DesktopInstallError, "host"):
            desktop.host_guard({"CONTAINER_ID": "zombiecc"})

    def test_root_guard(self):
        with patch.object(Path, "exists", return_value=False), patch.object(os, "geteuid", return_value=0):
            with self.assertRaisesRegex(desktop.DesktopInstallError, "without sudo"):
                desktop.host_guard({})

    def test_missing_stage1_marker(self):
        (self.layout.app / ".zombiecc-stage1").unlink()
        with self.assertRaises(OSError):
            desktop.check_installation(self.layout)

    def test_bad_stage1_marker(self):
        (self.layout.app / ".zombiecc-stage1").write_text("not-our-installer")
        with self.assertRaisesRegex(desktop.DesktopInstallError, "marker"):
            desktop.check_installation(self.layout)

    def test_missing_icon(self):
        self.layout.icon.unlink()
        with self.assertRaises(OSError):
            desktop.check_installation(self.layout)

    def test_wrong_icon_format(self):
        self.layout.icon.write_text("not a png")
        with self.assertRaisesRegex(desktop.DesktopInstallError, "PNG"):
            desktop.check_installation(self.layout)

    def test_symlinked_launcher_refused(self):
        self.layout.launcher.unlink()
        self.layout.launcher.symlink_to("/bin/bash")
        with self.assertRaises(OSError):
            desktop.check_installation(self.layout)

    def test_nonexecutable_launcher_refused(self):
        self.layout.launcher.chmod(0o600)
        with self.assertRaisesRegex(desktop.DesktopInstallError, "executable"):
            desktop.check_installation(self.layout)

    def test_host_need_not_resolve_container_interpreter(self):
        (self.layout.app / ".venv/bin").mkdir()
        (self.layout.app / ".venv/bin/python").symlink_to("/container-only/python")
        desktop.check_installation(self.layout)

    def test_menu_created_mode_and_content(self):
        self.install()
        self.assertEqual(self.layout.menu.read_bytes(), desktop.entry_contents(self.layout))
        self.assertEqual(stat.S_IMODE(self.layout.menu.stat().st_mode), 0o644)

    def test_optional_desktop_entry(self):
        folder = self.home / "Localized Desktop"
        folder.mkdir()
        entry = desktop.Entry(folder / desktop.ENTRY_NAME, 0o755)
        self.install([*self.entries, entry])
        self.assertEqual(entry.path.read_bytes(), self.layout.menu.read_bytes())
        self.assertEqual(stat.S_IMODE(entry.path.stat().st_mode), 0o755)

    def test_menu_only_does_not_create_desktop(self):
        self.install()
        self.assertFalse((self.home / "Desktop").exists())

    def test_identical_rerun_preserves_inode_and_time(self):
        self.install()
        before = self.layout.menu.stat()
        self.install()
        after = self.layout.menu.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_conflicting_menu_preserved(self):
        self.layout.menu.parent.mkdir()
        self.layout.menu.write_text("user customization")
        with self.assertRaisesRegex(desktop.DesktopInstallError, "different"):
            self.install()
        self.assertEqual(self.layout.menu.read_text(), "user customization")

    def test_desktop_conflict_checked_before_menu_write(self):
        folder = self.home / "Desktop"
        folder.mkdir()
        entry = desktop.Entry(folder / desktop.ENTRY_NAME, 0o755)
        entry.path.write_text("user shortcut")
        with self.assertRaises(desktop.DesktopInstallError):
            self.install([*self.entries, entry])
        self.assertFalse(self.layout.menu.exists())
        self.assertEqual(entry.path.read_text(), "user shortcut")

    def test_symlinked_entry_preserved(self):
        self.layout.menu.parent.mkdir()
        target = self.root / "target"
        target.write_text("untouched")
        self.layout.menu.symlink_to(target)
        with self.assertRaises(desktop.DesktopInstallError):
            self.install()
        self.assertEqual(target.read_text(), "untouched")

    def test_symlinked_application_menu_folder_refused(self):
        folder = self.root / "external"
        folder.mkdir()
        self.layout.menu.parent.symlink_to(folder)
        with self.assertRaises(desktop.DesktopInstallError):
            self.install()
        self.assertEqual(list(folder.iterdir()), [])

    def test_no_overwrite_when_target_appears_during_publication(self):
        real_link = os.link
        def racing_link(source, destination):
            Path(destination).write_text("racing user change")
            real_link(source, destination)
        with patch.object(desktop.os, "link", side_effect=racing_link):
            with self.assertRaises(desktop.DesktopInstallError):
                self.install()
        self.assertEqual(self.layout.menu.read_text(), "racing user change")
        self.assertFalse(list(self.layout.menu.parent.glob(".zombiecc-desktop-*")))

    def test_lock_blocks_concurrent_installer(self):
        with desktop.install_lock(self.layout):
            with self.assertRaisesRegex(desktop.DesktopInstallError, "already running"):
                self.install()
        self.assertFalse(self.layout.menu.exists())

    def test_symlinked_lock_refused(self):
        target = self.root / "locktarget"
        target.write_text("keep")
        (self.layout.data / "zombiecc/.install.lock").symlink_to(target)
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual(target.read_text(), "keep")

    def test_dry_run_writes_nothing(self):
        before = set(self.root.rglob("*"))
        code, output, errors = self.cli("--dry-run")
        self.assertEqual(code, 0, errors)
        self.assertIn("DRY RUN complete", output)
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_noninteractive_requires_yes(self):
        with patch.object(desktop.sys.stdin, "isatty", return_value=False):
            code, _, errors = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("--yes", errors)
        self.assertFalse(self.layout.menu.exists())

    def test_cancel_writes_nothing(self):
        with patch.object(desktop.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="n"):
            code, output, _ = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("Cancelled", output)
        self.assertFalse(self.layout.menu.exists())

    def test_no_changes_to_installed_payload_or_live_data(self):
        settings = self.layout.config / "zombiecc/settings.json"
        autostart = self.layout.config / "autostart/zombie-control-center.desktop"
        service = self.layout.config / "systemd/user/g13-driver.service"
        profile = self.layout.data / "zombiecc/profiles/mine.json"
        for file in (settings, autostart, service, profile):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("original content")
        before = {p: p.read_bytes() for p in self.layout.app.rglob("*") if p.is_file()}
        self.install()
        for p, content in before.items():
            self.assertEqual(p.read_bytes(), content)
        for p in (settings, autostart, service, profile):
            self.assertEqual(p.read_text(), "original content")

    def desktop_result(self, stdout):
        return subprocess.CompletedProcess(["xdg-user-dir", "DESKTOP"], 0, stdout, "")

    def test_desktop_lookup_uses_configured_localized_folder(self):
        folder = self.home / "Bureau"
        folder.mkdir()
        with patch.object(desktop.shutil, "which", return_value="/usr/bin/xdg-user-dir"), \
             patch.object(desktop.subprocess, "run", return_value=self.desktop_result(str(folder)+"\n")) as run:
            self.assertEqual(desktop.configured_desktop(self.layout, self.env), folder)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/xdg-user-dir", "DESKTOP"])

    def test_disabled_desktop_refused(self):
        with patch.object(desktop.shutil, "which", return_value="/usr/bin/xdg-user-dir"), \
             patch.object(desktop.subprocess, "run", return_value=self.desktop_result(str(self.home)+"\n")):
            with self.assertRaisesRegex(desktop.DesktopInstallError, "disabled"):
                desktop.configured_desktop(self.layout, self.env)

    def test_missing_desktop_not_created(self):
        folder = self.home / "missing"
        with patch.object(desktop.shutil, "which", return_value="/usr/bin/xdg-user-dir"), \
             patch.object(desktop.subprocess, "run", return_value=self.desktop_result(str(folder)+"\n")):
            with self.assertRaises(desktop.DesktopInstallError):
                desktop.configured_desktop(self.layout, self.env)
        self.assertFalse(folder.exists())

    def test_missing_desktop_lookup_tool(self):
        with patch.object(desktop.shutil, "which", return_value=None):
            with self.assertRaisesRegex(desktop.DesktopInstallError, "Omit --desktop-shortcut"):
                desktop.configured_desktop(self.layout, self.env)

    def test_symlinked_configured_desktop_is_resolved(self):
        folder, link = self.home / "actual", self.home / "Desktop"
        folder.mkdir()
        link.symlink_to(folder, target_is_directory=True)
        with patch.object(desktop.shutil, "which", return_value="/usr/bin/xdg-user-dir"), \
             patch.object(desktop.subprocess, "run", return_value=self.desktop_result(str(link)+"\n")):
            self.assertEqual(desktop.configured_desktop(self.layout, self.env), folder)

    def test_entry_has_no_autostart_or_association_hints(self):
        content = desktop.entry_contents(self.layout).decode()
        for forbidden in ("OnlyShowIn=", "StartupWMClass=", "MimeType=", "%f", "%u", "g13-fedora", "g13-editor.sh"):
            self.assertNotIn(forbidden, content)
        self.assertIn("DBusActivatable=false", content)
        self.assertIn("Terminal=false", content)

    def test_failed_optional_validator_prevents_publication(self):
        with patch.object(desktop.shutil, "which", return_value="/validator"), \
             patch.object(desktop.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "bad entry")):
            with self.assertRaisesRegex(desktop.DesktopInstallError, "validation failed"):
                self.install()
        self.assertFalse(self.layout.menu.exists())
        self.assertFalse(list(self.layout.menu.parent.glob(".zombiecc-desktop-*")))

    def test_successful_menu_does_not_require_kde_cache_tool(self):
        self.env["XDG_CURRENT_DESKTOP"] = "KDE"
        with patch.object(desktop.shutil, "which", return_value=None):
            self.install()
        self.assertTrue(self.layout.menu.exists())

    def test_native_glib_launch_preserves_quoted_paths_and_environment(self):
        libraries = [ctypes.util.find_library(x) for x in ("gio-2.0", "gobject-2.0")]
        if not all(libraries):
            self.skipTest("Optional native GIO libraries unavailable")
        gio, gobject = map(ctypes.CDLL, libraries)
        try:
            new = gio.g_desktop_app_info_new_from_filename
        except AttributeError:
            self.skipTest("GDesktopAppInfo unavailable")
        new.argtypes, new.restype = [ctypes.c_char_p], ctypes.c_void_p
        launch = gio.g_app_info_launch
        launch.argtypes, launch.restype = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int
        gobject.g_object_unref.argtypes = [ctypes.c_void_p]
        for suffix in ("space and 'quote'", 'back\\slash "quote" $dollar `tick` %percent =equal café'):
            with self.subTest(path=suffix):
                home = self.root / suffix
                home.mkdir()
                env = {**self.env, "HOME": str(home), "ZOMBIECC_CONTAINER": "wrong-test-container"}
                layout = desktop.make_layout(env)
                self.seed(layout)
                entry = home / "test.desktop"
                entry.write_bytes(desktop.entry_contents(layout))
                with patch.dict(os.environ, env, clear=True):
                    info = new(os.fsencode(entry))
                    self.assertTrue(info, "GDesktopAppInfo could not parse the entry")
                    try:
                        self.assertTrue(launch(info, None, None, None), "GIO launch failed")
                        output = home / "launched.txt"
                        limit = time.monotonic() + 5
                        while not output.exists() and time.monotonic() < limit:
                            time.sleep(0.02)
                        self.assertTrue(output.exists())
                        self.assertEqual(output.read_text().splitlines(),
                                         [str(layout.data), str(layout.config), str(layout.state), "unset"])
                    finally:
                        gobject.g_object_unref(info)


if __name__ == "__main__":
    unittest.main()
