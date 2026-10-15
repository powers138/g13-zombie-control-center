"""Host-side updater tests: temporary payloads and mocked runtime commands.

No actual Podman/Distrobox, package manager, application, device or service is
used. Tests cross-check the Stage 1 fingerprint against install.sh and GNU
sha256sum, and use harmless Python children to check /proc and lock inheritance.
"""
from __future__ import annotations

import contextlib
from dataclasses import replace
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import update_app as update


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.error = None

    def check_idle(self, layout):
        self.calls.append(("idle", str(layout.app)))

    def validate(self, layout, staged):
        self.calls.append(("validate", str(staged)))
        if self.error:
            raise self.error
        payload = update.Payload.load(staged)
        for name in payload.order:
            if name.endswith(".py"):
                compile(payload.files[name].data, name, "exec")
        for name in ("settings.json", "default-profile.json"):
            json.loads((staged / "resources/defaults" / name).read_bytes())
        return "OK: mocked container syntax/JSON verification."


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="zombiecc-updater-tests-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "Home with spaces"
        self.source = self.root / "Source $with 'quoted' paths"
        self.layout = update.Layout(self.home, self.home / ".local/share", self.home / ".local/state")
        self.layout.app.mkdir(parents=True)
        self.layout.state.mkdir(parents=True)
        self.source.mkdir()
        self.layout.launcher.parent.mkdir(parents=True)
        self.launcher = b"#!/bin/bash\nexit 0\n"
        self.layout.launcher.write_bytes(self.launcher)
        self.layout.launcher.chmod(0o755)
        (self.source / "zombiecc").write_bytes(self.launcher)
        (self.source / "zombiecc").chmod(0o755)
        names = sorted(update.REQUIRED | {"active_window.py", "assets/g13_device.png"})
        for name in names:
            if name.endswith(".py"):
                data = b"# Old payload\nvalue = 1\n"
            elif name.endswith(".json"):
                data = b'{"factory": true}\n'
            elif name == "container-packages.txt":
                data = b"hidapi\npython3-evdev\n"
            elif name == "requirements-runtime.txt":
                data = b"PySide6==6.11.1\nhid==1.0.9\nlupa==2.8\n"
            elif name == "app-files.txt":
                data = ("\n".join(names) + "\n").encode()
            else:
                data = b"asset"
            for root in (self.source, self.layout.app):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o600 if root == self.layout.app else 0o644)
        (self.layout.app / ".venv").mkdir()
        (self.layout.app / ".venv/pyvenv.cfg").write_text("include-system-site-packages = true\n")
        (self.layout.app / ".venv/keep").write_text("runtime stays put")
        self.old = update.Payload.load(self.layout.app)
        self.old_marker = update.Blob((self.old.digest(self.launcher) + "\n").encode(), 0o600)
        (self.layout.app / update.MARKER).write_bytes(self.old_marker.data)
        (self.layout.app / update.MARKER).chmod(0o600)
        (self.source / "editor.py").write_text("# New payload\nvalue = 2\n")
        (self.source / "new_runtime_helper.py").write_text("# New runtime helper\nvalue = 3\n")
        (self.source / "VERSION").write_text("1.0.0\n")
        (self.source / "app_version.py").write_text(
            "# New release version helper\nAPP_VERSION = '1.0.0'\n"
        )
        new_names = sorted(names + ["VERSION", "app_version.py", "new_runtime_helper.py"])
        (self.source / "app-files.txt").write_text("\n".join(new_names) + "\n")
        self.protected = [
            self.layout.base / "profiles/personal.json",
            self.home / ".config/zombiecc/settings.json",
            self.home / ".config/autostart/zombie-control-center.desktop",
            self.home / ".config/systemd/user/g13-driver.service",
            self.home / "Desktop/zombie-control-center.desktop",
            self.layout.data / "applications/zombie-control-center.desktop",
        ]
        for path in self.protected:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("user-owned content")
        self.runtime = FakeRuntime()
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", os.defpath)}

    def plan(self):
        return update.build_plan(self.source, self.layout)

    def apply(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return update.apply(self.plan(), self.runtime)

    def assert_original(self):
        self.assertEqual(update.Payload.load(self.layout.app), self.old)
        self.assertEqual(update.marker(self.layout), self.old_marker)
        for name in ("VERSION", "app_version.py", "new_runtime_helper.py"):
            self.assertFalse((self.layout.app / name).exists())
        self.assertFalse(self.layout.pending.exists())

    def assert_preserved(self):
        for path in self.protected:
            self.assertEqual(path.read_text(), "user-owned content", str(path))
        self.assertEqual(self.layout.launcher.read_bytes(), self.launcher)
        self.assertEqual((self.layout.app / ".venv/keep").read_text(), "runtime stays put")

    def prepare_interrupted(self):
        plan = self.plan()
        location = update.write_backup(plan)
        backup = update.Backup.load(self.layout, location)
        update.set_pending(self.layout, location)
        update.publish(self.layout.app / "editor.py", backup.after.files["editor.py"],
                       backup.before.files["editor.py"])
        return location, backup

    def test_expected_two_replacements_three_additions(self):
        plan = self.plan()
        self.assertEqual(
            set(plan.changes),
            {"VERSION", "app_version.py", "app-files.txt", "editor.py", "new_runtime_helper.py"},
        )

    def test_pre_version_install_can_upgrade_to_versioned_release(self):
        plan = self.plan()
        self.assertIsNone(update.payload_version(plan.old.files))
        self.assertEqual(update.payload_version(plan.new.files), "1.0.0")

    def test_new_release_requires_version_files(self):
        manifest_path = self.source / "app-files.txt"
        original = manifest_path.read_text()
        for name in ("VERSION", "app_version.py"):
            with self.subTest(name=name):
                manifest_path.write_text(original.replace(f"{name}\n", ""))
                with self.assertRaisesRegex(update.UpdateError, "New release payload is missing"):
                    self.plan()
                manifest_path.write_text(original)

    def test_new_release_rejects_invalid_version(self):
        (self.source / "VERSION").write_text("1.0\n")
        with self.assertRaisesRegex(update.UpdateError, "MAJOR.MINOR.PATCH"):
            self.plan()

    def test_apply_updates_marker_and_preserves_runtime_user_data(self):
        venv_inode = (self.layout.app / ".venv").stat().st_ino
        location = self.apply()
        self.assertTrue((location / "receipt.json").exists())
        self.assertEqual((self.layout.app / "editor.py").read_bytes(), (self.source / "editor.py").read_bytes())
        new = update.Payload.load(self.layout.app)
        self.assertEqual(new.digest(self.launcher).encode(), update.marker(self.layout).data.strip())
        self.assertEqual((self.layout.app / ".venv").stat().st_ino, venv_inode)
        self.assertFalse(self.layout.pending.exists())
        self.assert_preserved()

    def test_backup_contains_verified_before_and_after_not_venv(self):
        location = self.apply()
        backup = update.Backup.load(self.layout, location)
        self.assertEqual(backup.before, self.old)
        self.assertFalse((location / "before/.venv").exists())
        self.assertEqual(backup.original_marker, self.old_marker)

    def test_identical_rerun_is_noop(self):
        self.apply()
        before = sorted(self.layout.backups.iterdir())
        self.runtime.calls.clear()
        self.assertIsNone(self.apply())
        self.assertEqual(sorted(self.layout.backups.iterdir()), before)
        self.assertFalse(any(c[0] == "validate" for c in self.runtime.calls))

    def test_installed_file_modification_is_refused(self):
        (self.layout.app / "editor.py").write_text("personal modification")
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            self.plan()
        self.assertFalse(self.layout.backups.exists())

    def test_installed_manifest_modification_is_refused(self):
        path = self.layout.app / "app-files.txt"
        path.write_text(path.read_text() + "# changed\n")
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            self.plan()

    def test_installed_launcher_modification_is_refused(self):
        self.layout.launcher.write_text("changed launcher")
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            self.plan()

    def test_source_launcher_update_is_supported(self):
        new_launcher = b"#!/bin/bash\nexport XCURSOR_PATH=/test/cursors\nexit 0\n"
        (self.source / "zombiecc").write_bytes(new_launcher)
        (self.source / "zombiecc").chmod(0o755)

        plan = self.plan()

        self.assertTrue(plan.launcher_changed)
        self.assertEqual(plan.launcher.data, self.launcher)
        self.assertEqual(plan.next_launcher.data, new_launcher)

    def test_launcher_update_is_published_and_fingerprinted(self):
        new_launcher = b"#!/bin/bash\nexport XCURSOR_PATH=/test/cursors\nexit 0\n"
        (self.source / "zombiecc").write_bytes(new_launcher)
        (self.source / "zombiecc").chmod(0o755)

        with contextlib.redirect_stdout(io.StringIO()):
            location = self.apply()

        self.assertIsNotNone(location)
        self.assertEqual(self.layout.launcher.read_bytes(), new_launcher)

        installed = update.Payload.load(self.layout.app)
        self.assertEqual(
            installed.digest(new_launcher).encode(),
            update.marker(self.layout).data.strip(),
        )

    def test_launcher_update_rollback_restores_old_launcher(self):
        new_launcher = b"#!/bin/bash\nexport XCURSOR_PATH=/test/cursors\nexit 0\n"
        (self.source / "zombiecc").write_bytes(new_launcher)
        (self.source / "zombiecc").chmod(0o755)

        with contextlib.redirect_stdout(io.StringIO()):
            location = self.apply()

        self.assertEqual(self.layout.launcher.read_bytes(), new_launcher)

        with contextlib.redirect_stdout(io.StringIO()):
            update.rollback(self.layout, location, self.runtime, recovery=False)

        self.assertEqual(self.layout.launcher.read_bytes(), self.launcher)
        self.assert_original()

    def test_launcher_update_recovery_restores_old_launcher(self):
        new_launcher = b"#!/bin/bash\nexport XCURSOR_PATH=/test/cursors\nexit 0\n"
        (self.source / "zombiecc").write_bytes(new_launcher)
        (self.source / "zombiecc").chmod(0o755)

        plan = self.plan()
        location = update.write_backup(plan)
        backup = update.Backup.load(self.layout, location)

        update.set_pending(self.layout, location)

        current_launcher = update.read_blob(self.layout.launcher)
        self.assertIsNotNone(current_launcher)

        update.publish(
            self.layout.launcher,
            backup.next_launcher,
            current_launcher,
        )

        self.assertEqual(self.layout.launcher.read_bytes(), new_launcher)

        with contextlib.redirect_stdout(io.StringIO()):
            update.rollback(self.layout, location, self.runtime, recovery=True)

        self.assertEqual(self.layout.launcher.read_bytes(), self.launcher)
        self.assert_original()

    def test_legacy_backup_without_launcher_after_still_loads(self):
        location = self.apply()

        (location / "launcher-after").unlink()

        backup = update.Backup.load(self.layout, location)

        self.assertEqual(backup.next_launcher, backup.launcher)

    def test_tampered_launcher_after_backup_is_refused(self):
        new_launcher = b"#!/bin/bash\nexport XCURSOR_PATH=/test/cursors\nexit 0\n"
        (self.source / "zombiecc").write_bytes(new_launcher)
        (self.source / "zombiecc").chmod(0o755)

        location = update.write_backup(self.plan())
        (location / "launcher-after").write_bytes(b"tampered launcher\n")

        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            update.Backup.load(self.layout, location)

    def test_source_launcher_update_must_be_executable(self):
        (self.source / "zombiecc").write_text("#!/bin/bash\nexit 0\n")
        (self.source / "zombiecc").chmod(0o644)

        with self.assertRaisesRegex(update.UpdateError, "launcher is not executable"):
            self.plan()

    def test_both_dependency_changes_are_refused(self):
        for name in update.DEPENDENCIES:
            with self.subTest(name=name):
                path = self.source / name
                original = path.read_bytes()
                path.write_bytes(original + b"# changed\n")
                with self.assertRaisesRegex(update.UpdateError, "does not change dependencies"):
                    self.plan()
                path.write_bytes(original)

    def test_removed_manifest_file_is_refused(self):
        path = self.source / "app-files.txt"
        path.write_text(path.read_text().replace("active_window.py\n", ""))
        with self.assertRaisesRegex(update.UpdateError, "does not remove"):
            self.plan()

    def test_untracked_installed_file_is_not_overwritten(self):
        (self.layout.app / "new_runtime_helper.py").write_text("untracked file")
        with self.assertRaisesRegex(update.UpdateError, "untracked"):
            self.plan()

    def test_missing_source_file_is_refused(self):
        (self.source / "new_runtime_helper.py").unlink()
        with self.assertRaises(FileNotFoundError):
            self.plan()

    def test_symlinked_source_file_is_refused(self):
        target = self.source / "new_runtime_helper.py"
        target.unlink()
        target.symlink_to(self.source / "editor.py")
        with self.assertRaises(OSError):
            self.plan()

    def test_symlinked_installed_file_is_refused(self):
        target = self.layout.app / "editor.py"
        target.unlink()
        target.symlink_to(self.source / "editor.py")
        with self.assertRaises(OSError):
            self.plan()

    def test_symlinked_payload_parent_is_refused(self):
        source_assets = self.source / "assets"
        source_assets.rename(self.source / "other-assets")
        source_assets.symlink_to(self.source / "other-assets", target_is_directory=True)
        with self.assertRaisesRegex(update.UpdateError, "directory"):
            self.plan()

    def test_symlinked_venv_is_refused_without_touching_target(self):
        path = self.layout.app / ".venv"
        path.rename(self.layout.app / "other-venv")
        path.symlink_to(self.layout.app / "other-venv", target_is_directory=True)
        with self.assertRaisesRegex(update.UpdateError, "directory"):
            self.plan()
        self.assertTrue((self.layout.app / "other-venv/keep").exists())

    def test_hard_link_is_refused(self):
        os.link(self.source / "editor.py", self.source / "extra-link")
        with self.assertRaisesRegex(update.UpdateError, "hard-linked"):
            self.plan()

    def test_special_file_is_refused_without_blocking(self):
        path = self.source / "new_runtime_helper.py"
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(update.UpdateError, "ordinary file"):
            self.plan()

    def test_world_writable_file_is_refused(self):
        (self.source / "editor.py").chmod(0o666)
        with self.assertRaisesRegex(update.UpdateError, "Unsafe permissions"):
            self.plan()

    def test_source_inside_installation_is_refused(self):
        with self.assertRaisesRegex(update.UpdateError, "separate"):
            update.build_plan(self.layout.app, self.layout)

    def test_source_changed_after_preflight_is_refused(self):
        plan = self.plan()
        (self.source / "editor.py").write_text("different source")
        with self.assertRaisesRegex(update.UpdateError, "changed after preflight"):
            update.apply(plan, self.runtime)
        self.assert_original()

    def test_runtime_failure_does_not_change_installed_payload(self):
        self.runtime.error = update.UpdateError("runtime failed")
        with self.assertRaisesRegex(update.UpdateError, "runtime failed"):
            self.apply()
        self.assert_original()
        self.assert_preserved()

    def test_syntax_error_in_staged_code_does_not_publish(self):
        (self.source / "editor.py").write_text("def broken(:\n")
        with self.assertRaises(SyntaxError):
            self.apply()
        self.assert_original()

    def test_mid_update_failure_restores_original(self):
        original = update.publish
        triggered = False
        def failing(path, blob, expected):
            nonlocal triggered
            if path == self.layout.app / "editor.py" and not triggered:
                triggered = True
                raise OSError("injected disk failure")
            return original(path, blob, expected)
        with patch.object(update, "publish", side_effect=failing):
            with self.assertRaisesRegex(update.UpdateError, "previous application files restored"):
                self.apply()
        self.assertTrue(triggered)
        self.assert_original()
        self.assert_preserved()

    def test_interrupt_restores_original(self):
        original = update.publish
        triggered = False
        def failing(path, blob, expected):
            nonlocal triggered
            if path == self.layout.app / "editor.py" and not triggered:
                triggered = True
                raise KeyboardInterrupt()
            return original(path, blob, expected)
        with patch.object(update, "publish", side_effect=failing):
            with self.assertRaisesRegex(update.UpdateError, "previous application files restored"):
                self.apply()
        self.assert_original()

    def test_marker_publication_failure_rolls_back_added_file(self):
        original = update.publish
        def failing(path, blob, expected):
            if path == self.layout.app / update.MARKER:
                raise OSError("marker failure")
            return original(path, blob, expected)
        with patch.object(update, "publish", side_effect=failing):
            with self.assertRaisesRegex(update.UpdateError, "previous application files restored"):
                self.apply()
        self.assert_original()

    def test_concurrent_edit_blocks_automatic_rollback_and_retains_journal(self):
        original = update.publish
        def failing(path, blob, expected):
            if path == self.layout.app / "editor.py":
                path.write_text("unexpected external edit")
                raise OSError("simulated racing edit")
            return original(path, blob, expected)
        with patch.object(update, "publish", side_effect=failing):
            with self.assertRaisesRegex(update.UpdateError, "automatic rollback did not finish"):
                self.apply()
        self.assertTrue(self.layout.pending.exists())
        self.assertEqual((self.layout.app / "editor.py").read_text(), "unexpected external edit")
        with self.assertRaisesRegex(update.UpdateError, "unfinished update"):
            self.plan()

    def test_deliberate_rollback_restores_previous_payload(self):
        location = self.apply()
        with contextlib.redirect_stdout(io.StringIO()):
            update.rollback(self.layout, location, self.runtime, recovery=False)
        self.assert_original()
        self.assert_preserved()

    def test_rollback_after_personal_edit_is_refused(self):
        location = self.apply()
        (self.layout.app / "editor.py").write_text("personal edit after update")
        with self.assertRaisesRegex(update.UpdateError, "BOTH known versions"):
            update.rollback(self.layout, location, self.runtime, recovery=False)
        self.assertFalse(self.layout.pending.exists())

    def test_tampered_backup_is_refused(self):
        location = self.apply()
        (location / "before/editor.py").write_text("tampered backup")
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            update.Backup.load(self.layout, location)

    def test_backup_for_other_installation_is_refused(self):
        location = self.apply()
        path = location / "receipt.json"
        receipt = json.loads(path.read_text())
        receipt["application"] = "/unrelated/app"
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(update.UpdateError, "wrong application path"):
            update.Backup.load(self.layout, location)

    def test_pending_update_blocks_new_update(self):
        self.prepare_interrupted()
        with self.assertRaisesRegex(update.UpdateError, "unfinished update"):
            self.plan()

    def test_recovery_restores_interrupted_transaction(self):
        location, backup = self.prepare_interrupted()
        with contextlib.redirect_stdout(io.StringIO()):
            update.rollback(self.layout, location, self.runtime, recovery=True)
        self.assert_original()
        self.assert_preserved()

    def test_recovery_handles_all_new_files_but_old_marker(self):
        location, backup = self.prepare_interrupted()
        for name in backup.after.order:
            path = self.layout.app / name
            current = update.read_blob(path, missing=True)
            if current != backup.after.files[name]:
                update.publish(path, backup.after.files[name], current)
        with contextlib.redirect_stdout(io.StringIO()):
            update.rollback(self.layout, location, self.runtime, recovery=True)
        self.assert_original()

    def test_recovery_handles_new_marker_before_journal_removal(self):
        location, backup = self.prepare_interrupted()
        for name in backup.after.order:
            path = self.layout.app / name
            current = update.read_blob(path, missing=True)
            if current != backup.after.files[name]:
                update.publish(path, backup.after.files[name], current)
        update.publish(self.layout.app / update.MARKER, backup.next_marker(), backup.original_marker)
        with contextlib.redirect_stdout(io.StringIO()):
            update.rollback(self.layout, location, self.runtime, recovery=True)
        self.assert_original()

    def test_shared_installer_lock_blocks_update(self):
        with update.install_lock(self.layout):
            with self.assertRaisesRegex(update.UpdateError, "holds the installation lock"):
                self.apply()
        self.assert_original()

    def test_symlinked_lock_is_refused(self):
        target = self.root / "keep"
        target.write_text("untouched")
        (self.layout.base / ".install.lock").symlink_to(target)
        with self.assertRaises(OSError):
            self.apply()
        self.assertEqual(target.read_text(), "untouched")

    def test_lock_file_is_retained_and_reacquirable(self):
        self.apply()
        with update.install_lock(self.layout):
            self.assertTrue((self.layout.base / ".install.lock").is_file())

    def test_safe_manifest_variants_and_bad_entries(self):
        content = (self.source / "app-files.txt").read_bytes()
        self.assertEqual(update.manifest(b"# header\r\n\r\n" + content.replace(b"\n", b"\r\n")),
                         update.manifest(content))
        for bad in (b"/etc/passwd", b"../file", b"a/../../b", b".venv/lib/site.py", b"*.py",
                    b"test_file.py", b"docs/note.txt", b"profiles/personal.json", b"g13-editor.sh",
                    b"update_app.py", b"install_desktop.py", b"other.service", b"a\tbad"):
            with self.subTest(bad=bad):
                with self.assertRaises(update.UpdateError):
                    update.manifest(content + bad + b"\n")

    def test_duplicate_missing_required_and_path_overlap_refused(self):
        content = (self.source / "app-files.txt").read_bytes()
        for data in (content + b"editor.py\n", content.replace(b"editor.py\n", b""),
                     content + b"part\npart/file.py\n"):
            with self.assertRaises(update.UpdateError):
                update.manifest(data)

    def test_normalized_xdg_paths_and_relative_fallback(self):
        layout = update.Layout.from_environment({"HOME": str(self.home), "XDG_DATA_HOME": "relative",
                                                "XDG_STATE_HOME": str(self.root / "state space/%$\\")})
        self.assertEqual(layout.data, self.home / ".local/share")
        self.assertEqual(layout.state, self.root / "state space/%$\\")
        for value in ("", "relative", "/path\nline"):
            with self.assertRaises(update.UpdateError):
                update.absolute(value, "path")

    def test_fingerprint_matches_original_install_sh(self):
        installer = Path(__file__).with_name("install.sh")
        if not (installer.is_file() and shutil.which("sha256sum") and shutil.which("bash")):
            self.skipTest("install.sh and GNU sha256sum/bash are required for the compatibility cross-check.")
        text = installer.read_text()
        function = text.split("fingerprint() {", 1)[1].split("\nFINGERPRINT=", 1)[0]
        script = '''set -euo pipefail
CONTAINER_IMAGE="$1"; OWNER_VALUE="$2"; root="$3"; launcher="$4"; shift 4
APP_FILES=("$@")
fingerprint() {''' + function + '\nfingerprint "$root" "$launcher"\n'
        result = subprocess.run(["bash", "-c", script, "fingerprint-check", update.IMAGE, update.OWNER,
                                 str(self.layout.app), str(self.layout.launcher), *self.old.order],
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), self.old.digest(self.launcher))

    def test_dry_run_has_no_updates_or_runtime_start(self):
        output = io.StringIO()
        old_tree = sorted(str(p) for p in self.home.rglob("*"))
        with patch.object(update, "host_guard"), patch.object(update, "__file__", str(self.source / "update_app.py")), \
             patch.dict(os.environ, self.env, clear=True), patch.object(update, "Runtime", return_value=self.runtime), \
             contextlib.redirect_stdout(output):
            self.assertEqual(update.main(["--dry-run"]), 0)
        self.assertIn("DRY RUN complete", output.getvalue())
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*")), old_tree)
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(self.runtime.calls[0][0], "idle")

    def test_noninteractive_update_requires_yes(self):
        output = io.StringIO()
        with patch.object(update, "host_guard"), patch.object(update, "__file__", str(self.source / "update_app.py")), \
             patch.dict(os.environ, self.env, clear=True), patch.object(update, "Runtime", return_value=self.runtime), \
             patch.object(sys.stdin, "isatty", return_value=False), contextlib.redirect_stdout(output), \
             contextlib.redirect_stderr(output):
            self.assertEqual(update.main([]), 1)
        self.assertIn("Interactive confirmation", output.getvalue())
        self.assert_original()

    def test_root_and_container_guards(self):
        with patch.object(update.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(update.UpdateError, "without sudo"):
                update.host_guard({})
        with patch.object(update.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(update.UpdateError, "not inside"):
                update.host_guard({"CONTAINER_ID": "zombiecc"})

    def test_embedded_check_compiles_and_never_imports_application(self):
        compile(update.RUNTIME_CHECK, "<runtime-check>", "exec")
        self.assertNotIn("import editor", update.RUNTIME_CHECK)
        self.assertNotIn("ProfileManager()", update.RUNTIME_CHECK)
        self.assertIn("compile(path.read_bytes()", update.RUNTIME_CHECK)

    def test_atomic_new_publication_cannot_overwrite_a_racing_file(self):
        source = self.root / "publication-source"
        target = self.root / "publication-target"
        source.write_text("intended")
        target.write_text("unrelated")
        with self.assertRaises(FileExistsError):
            update.rename_new(source, target)
        self.assertEqual(target.read_text(), "unrelated")
        self.assertEqual(source.read_text(), "intended")

    def test_atomic_new_publication_has_only_one_link(self):
        source = self.root / "publication-source"
        target = self.root / "publication-target"
        source.write_text("intended")
        update.rename_new(source, target)
        self.assertFalse(source.exists())
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertEqual(target.read_text(), "intended")

    def test_only_changed_modules_bytecode_caches_are_removed(self):
        cache = self.layout.app / "__pycache__"
        cache.mkdir()
        changed = cache / "editor.cpython-314.pyc"
        unrelated = cache / "active_window.cpython-314.pyc"
        changed.write_bytes(b"stale compiled code")
        unrelated.write_bytes(b"other compiled code")
        self.apply()
        self.assertFalse(changed.exists())
        self.assertEqual(unrelated.read_bytes(), b"other compiled code")
        self.assert_preserved()

    def test_rollback_removes_added_module_bytecode(self):
        location = self.apply()
        cache = self.layout.app / "__pycache__"
        cache.mkdir()
        added = cache / "new_runtime_helper.cpython-314.pyc"
        added.write_bytes(b"compiled new helper")
        with contextlib.redirect_stdout(io.StringIO()):
            update.rollback(self.layout, location, self.runtime, recovery=False)
        self.assertFalse(added.exists())
        self.assert_original()

    def test_permissions_on_updated_and_new_files_are_private(self):
        self.apply()
        for name in ("VERSION", "app_version.py", "editor.py", "new_runtime_helper.py", "app-files.txt"):
            self.assertEqual(stat.S_IMODE((self.layout.app / name).stat().st_mode), 0o600)


class ProcessGuardTests(unittest.TestCase):
    """A fake proc tree preserves real argv boundaries and cwd symlinks.

    The last tests use harmless sleeping Python children, never Zombie itself.
    """
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="zombiecc-process-tests-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "Home with spaces"
        self.layout = update.Layout(self.home, self.home / ".local/share", self.home / ".local/state")
        self.app = self.layout.app
        (self.app / ".venv/bin").mkdir(parents=True)
        self.dev = self.root / "Desktop/G13 Project"
        (self.dev / ".venv/bin").mkdir(parents=True)
        self.proc = self.root / "proc"
        self.proc.mkdir()

    def process(self, argv, *, cwd=None, pid=1001):
        path = self.proc / str(pid)
        path.mkdir()
        (path / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in argv) + b"\0")
        if cwd is not None:
            (path / "cwd").symlink_to(cwd, target_is_directory=True)
        return path

    def check(self):
        update.check_installed_app_idle(self.layout, proc_root=self.proc)

    def blocked(self):
        with self.assertRaisesRegex(update.UpdateError, r"installed application \(PID \d+:"):
            self.check()

    def test_development_driver_and_host_supervisor_do_not_block(self):
        self.process(["./.venv/bin/python", "-u", "g13-test.py"], cwd=self.dev)
        self.process(["/usr/bin/python3", "-u", str(self.dev / "driver_service.py"),
                      "--container", "g13-fedora"], cwd=self.dev, pid=1002)
        self.check()

    def test_shell_distrobox_podman_and_grep_mentions_are_ignored(self):
        commands = [
            ["/usr/bin/sh", "/usr/bin/distrobox", "enter", "g13-fedora", "--",
             "bash", "-lc", 'exec ./.venv/bin/python -u g13-test.py'],
            ["podman", "exec", "g13-fedora", "bash", "-lc", "python -u g13-test.py"],
            ["grep", "-E", 'editor.py|g13-test.py|driver_service.py|' + str(self.app / ".venv")],
            ["/bin/bash", "-c", "echo " + str(self.app / "editor.py")],
        ]
        for pid, argv in enumerate(commands, 1001):
            # No cwd: a non-Python command must be ignored before reading it.
            self.process(argv, pid=pid)
        self.check()

    def test_absolute_installed_editor_is_blocked_with_outside_cwd(self):
        self.process(["/usr/bin/python3", "-u", str(self.app / "editor.py")], cwd=self.dev)
        self.blocked()

    def test_relative_installed_driver_is_blocked(self):
        self.process(["./.venv/bin/python", "-u", "g13-test.py"], cwd=self.app)
        self.blocked()

    def test_host_supervisor_from_installed_tree_is_blocked(self):
        self.process(["/usr/bin/python3", str(self.app / "driver_service.py"),
                      "--container", "zombiecc"], cwd=self.dev)
        self.blocked()

    def test_installed_venv_from_outside_tree_is_blocked(self):
        self.process([str(self.app / ".venv/bin/python"), "worker.py"], cwd=self.dev)
        self.blocked()

    def test_final_python_symlink_does_not_erase_venv_identity(self):
        for root in (self.app, self.dev):
            (root / ".venv/bin/python").symlink_to(sys.executable)
        self.process([str(self.dev / ".venv/bin/python"), "-u", "g13-test.py"], cwd=self.dev)
        self.check()
        self.process([str(self.app / ".venv/bin/python"), "-c", "pass"], cwd=self.dev, pid=1002)
        self.blocked()

    def test_relative_interpreter_resolves_against_process_cwd(self):
        self.process([os.path.relpath(self.app / ".venv/bin/python", self.dev),
                      "-c", "pass"], cwd=self.dev)
        self.blocked()

    def test_home_aliases_are_canonicalized(self):
        alias = self.root / "Home alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.process(["/usr/bin/python3", "-B", str(alias / ".local/share/zombiecc/app/editor.py")],
                     cwd=self.dev)
        self.blocked()

    def test_sibling_directory_with_same_prefix_does_not_block(self):
        other = self.app.parent / "app-old"
        other.mkdir()
        self.process(["python3", "editor.py"], cwd=other)
        self.check()

    def test_python_module_mode_in_installed_tree_is_blocked(self):
        self.process(["python3", "-B", "-m", "editor"], cwd=self.app)
        self.blocked()

    def test_python_c_code_is_not_mistaken_for_script_path(self):
        self.process(["/usr/bin/python3", "-c", str(self.app / "editor.py")], cwd=self.dev)
        self.check()

    def test_grouped_c_flag_does_not_turn_code_into_script_path(self):
        self.process(["/usr/bin/python3", "-uc", str(self.app / "editor.py")], cwd=self.dev)
        self.check()

    def test_unrelated_script_argument_is_not_an_executed_script(self):
        self.process(["python3", str(self.dev / "inspect.py"), str(self.app / "editor.py")], cwd=self.dev)
        self.check()

    def test_python_option_values_are_not_mistaken_for_script_paths(self):
        self.process(["python3", "-X", str(self.app / "editor.py"),
                      "-W", str(self.app / "g13-test.py"), str(self.dev / "inspect.py")], cwd=self.dev)
        self.check()

    def test_script_after_double_dash_is_detected(self):
        self.process(["python3", "-u", "--", str(self.app / "editor.py")], cwd=self.dev)
        self.blocked()

    def test_no_numeric_hpid_or_ps_output_is_needed(self):
        # A proc directory is the PID source, so Podman's '?' is irrelevant.
        (self.proc / "not-a-pid").mkdir()
        self.process(["python3", "g13-test.py"], cwd=self.dev)
        self.check()

    def test_current_updater_process_is_excluded(self):
        self.process([str(self.app / ".venv/bin/python"), "update_app.py"], pid=os.getpid())
        self.check()

    def test_empty_cmdline_and_exited_process_are_ignored(self):
        self.process([])
        (self.proc / "1001/cmdline").write_bytes(b"")
        (self.proc / "1002").mkdir()  # Disappeared before cmdline could be read.
        self.check()

    def test_another_users_process_is_not_inspected(self):
        path = self.process([str(self.app / ".venv/bin/python"), "editor.py"])
        original = Path.stat
        def different_owner(candidate, *args, **kwargs):
            info = original(candidate, *args, **kwargs)
            if candidate == path:
                values = list(info)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return info
        with patch.object(Path, "stat", different_owner):
            self.check()

    def test_unreadable_cmdline_is_not_permission_to_update(self):
        path = self.process(["python3", "editor.py"], cwd=self.app)
        original = Path.read_bytes
        def denied(candidate):
            if candidate == path / "cmdline":
                raise PermissionError("denied for test")
            return original(candidate)
        with patch.object(Path, "read_bytes", denied):
            with self.assertRaisesRegex(update.UpdateError, "Cannot inspect your process PID 1001"):
                self.check()

    def test_unreadable_python_cwd_is_reported_not_guessed(self):
        self.process(["python3", "editor.py"], cwd=self.app)
        original = os.readlink
        def denied(candidate, *args, **kwargs):
            if Path(candidate) == self.proc / "1001/cwd":
                raise PermissionError("denied for test")
            return original(candidate, *args, **kwargs)
        with patch.object(update.os, "readlink", denied):
            with self.assertRaisesRegex(update.UpdateError, "Cannot inspect working directory of Python PID 1001"):
                self.check()

    def test_absolute_installed_path_is_detected_without_cwd_access(self):
        self.process([str(self.app / ".venv/bin/python"), "editor.py"])
        self.blocked()

    def child(self, executable, cwd):
        process = subprocess.Popen(
            [str(executable), "-c", "import time; print('ready', flush=True); time.sleep(20)"],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        def stop():
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        self.addCleanup(stop)
        self.assertEqual(process.stdout.readline().strip(), "ready")
        return process

    def test_real_proc_detects_installed_python_symlink(self):
        python = self.app / ".venv/bin/python"
        python.symlink_to(sys.executable)
        process = self.child(python, self.dev)
        with self.assertRaisesRegex(update.UpdateError, f"PID {process.pid}:"):
            update.check_installed_app_idle(self.layout)

    def test_real_proc_detects_python_cwd_in_installed_tree(self):
        process = self.child(sys.executable, self.app)
        with self.assertRaisesRegex(update.UpdateError, f"PID {process.pid}:"):
            update.check_installed_app_idle(self.layout)

    def test_real_proc_allows_development_python(self):
        python = self.dev / ".venv/bin/python"
        python.symlink_to(sys.executable)
        self.child(python, self.dev)
        update.check_installed_app_idle(self.layout)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        with patch.object(update.shutil, "which", side_effect=lambda tool, **kw: "/usr/bin/" + tool):
            self.runtime = update.Runtime({"PATH": "/usr/bin:/bin"})
        self.layout = update.Layout(Path("/home/test"), Path("/home/test/.local/share"), Path("/home/test/.local/state"))
        self.info = {"Config": {"Image": update.IMAGE,
                     "Labels": {"manager": "distrobox", "io.zombiecc.installer": update.OWNER,
                                "distrobox.unshare_groups": "0"},
                     "Annotations": {"run.oci.keep_original_groups": "1"}},
                     "OCIRuntime": "crun", "State": {"Paused": False, "Running": True}}
        self.mode = "true false\n"
        self.calls = []
        idle = patch.object(update, "check_installed_app_idle")
        self.idle = idle.start()
        self.addCleanup(idle.stop)

    def answer(self, args, **kwargs):
        self.calls.append(args)
        if args[1] == "info":
            return self.mode
        if args[1:3] == ["container", "inspect"]:
            return json.dumps([self.info])
        raise AssertionError(args)

    def test_valid_idle_runtime_is_inspected_only(self):
        with patch.object(self.runtime, "run", side_effect=self.answer):
            self.runtime.check_idle(self.layout)
        self.assertEqual([args[1] for args in self.calls], ["info", "container"])
        self.idle.assert_called_once_with(self.layout)
        self.assertTrue(all("g13-fedora" not in args for args in self.calls))

    def test_stopped_container_is_not_started_by_preflight(self):
        self.info["State"]["Running"] = False
        with patch.object(self.runtime, "run", side_effect=self.answer):
            self.runtime.check_idle(self.layout)
        self.assertEqual(len(self.calls), 2)
        self.idle.assert_called_once_with(self.layout)

    def test_running_installed_editor_or_driver_is_refused(self):
        self.idle.side_effect = update.UpdateError("Zombie is running from the installed application")
        with patch.object(self.runtime, "run", side_effect=self.answer):
            with self.assertRaisesRegex(update.UpdateError, "running from the installed"):
                self.runtime.check_idle(self.layout)

    def test_shared_host_pid_namespace_does_not_use_podman_top(self):
        self.info["HostConfig"] = {"PidMode": "host"}
        with patch.object(self.runtime, "run", side_effect=self.answer):
            self.runtime.check_idle(self.layout)
        self.idle.assert_called_once_with(self.layout)
        self.assertNotIn("top", [args[1] for args in self.calls])

    def test_paused_wrong_image_groups_owner_runtime_are_refused(self):
        mutations = [lambda d: d["State"].update(Paused=True),
                     lambda d: d["Config"].update(Image="other"),
                     lambda d: d["Config"]["Labels"].update({"io.zombiecc.installer": "other"}),
                     lambda d: d["Config"]["Annotations"].update({"run.oci.keep_original_groups": "0"}),
                     lambda d: d.update(OCIRuntime="runc")]
        original = json.dumps(self.info)
        for change in mutations:
            self.info = json.loads(original)
            change(self.info)
            with patch.object(self.runtime, "run", side_effect=self.answer):
                with self.assertRaises(update.UpdateError):
                    self.runtime.check_idle(self.layout)

    def test_rootful_and_remote_engines_are_refused(self):
        for self.mode in ("false false", "true true"):
            with patch.object(self.runtime, "run", side_effect=self.answer):
                with self.assertRaisesRegex(update.UpdateError, "local rootless"):
                    self.runtime.check_idle(self.layout)

    def test_inspection_failure_is_not_permission_to_create(self):
        with patch.object(self.runtime, "run", side_effect=update.UpdateError("storage failure")):
            with self.assertRaisesRegex(update.UpdateError, "storage failure"):
                self.runtime.check_idle(self.layout)

    def test_validate_uses_final_venv_and_isolated_noninteractive_python(self):
        with patch.object(self.runtime, "run", return_value="OK") as run:
            self.runtime.validate(self.layout, Path("/staging path/$quoted"))
        args = run.call_args.args[0]
        self.assertEqual(args[0:7], ["/usr/bin/distrobox", "enter", "--name", "zombiecc", "--no-tty", "--",
                                    str(self.layout.app / ".venv/bin/python")])
        self.assertEqual(args[7:10], ["-I", "-B", "-c"])
        self.assertEqual(args[-1], "/staging path/$quoted")
        self.assertNotIn("pip", args)
        self.assertNotIn("sudo", args)
        self.assertNotIn("systemctl", args)

    def test_child_commands_cannot_inherit_install_lock(self):
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            layout = update.Layout(home, home / "data", home / "state")
            layout.base.mkdir(parents=True)
            lock = layout.base / ".install.lock"
            child = """import fcntl,os,sys
from pathlib import Path
identity = (os.stat(sys.argv[1]).st_dev, os.stat(sys.argv[1]).st_ino)
for path in Path('/proc/self/fd').iterdir():
    try:
        info = os.fstat(int(path.name))
    except OSError:
        continue
    assert (info.st_dev, info.st_ino) != identity, 'lock descriptor inherited'
f = open(sys.argv[1], 'a')
try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: print('parent holds lock')
else: raise RuntimeError('missing parent lock')
"""
            with update.install_lock(layout):
                self.assertIn("parent holds lock", self.runtime.run([sys.executable, "-c", child, str(lock)]))
            probe = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); print('released')"
            self.assertIn("released", self.runtime.run([sys.executable, "-c", probe, str(lock)]))

    def test_subprocess_uses_close_fds_and_never_shell_true(self):
        result = subprocess.CompletedProcess([], 0, "ok", "")
        with patch.object(update.subprocess, "run", return_value=result) as run:
            self.assertEqual(self.runtime.run(["/bin/true"]), "ok")
        self.assertTrue(run.call_args.kwargs["close_fds"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
