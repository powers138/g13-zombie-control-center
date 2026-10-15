from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from user_data_bootstrap import (
    bootstrap_user_data,
)


class UserDataBootstrapTests(
    unittest.TestCase
):
    def setUp(self):
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

        self.factory_directory = (
            self.root
            / "factory"
        )

        self.factory_directory.mkdir()

        self.factory_settings = (
            self.factory_directory
            / "settings.json"
        )

        self.factory_profile = (
            self.factory_directory
            / "default-profile.json"
        )

        self.factory_settings.write_text(
            '{"factory": true}\n',
            encoding="utf-8",
        )

        self.factory_profile.write_text(
            '{"name": "Factory"}\n',
            encoding="utf-8",
        )

        self.profiles_dir = (
            self.root
            / "data"
            / "profiles"
        )

        self.settings_file = (
            self.root
            / "config"
            / "settings.json"
        )

        self.logs_dir = (
            self.root
            / "state"
            / "logs"
        )

        self.diagnostics_dir = (
            self.root
            / "state"
            / "diagnostics"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def bootstrap(self):
        bootstrap_user_data(
            profiles_dir=self.profiles_dir,
            settings_file=self.settings_file,
            logs_dir=self.logs_dir,
            diagnostics_dir=(
                self.diagnostics_dir
            ),
            factory_settings_file=(
                self.factory_settings
            ),
            factory_default_profile_file=(
                self.factory_profile
            ),
        )

    def test_missing_user_data_is_created(self):
        self.bootstrap()

        self.assertEqual(
            self.settings_file.read_text(
                encoding="utf-8"
            ),
            '{"factory": true}\n',
        )

        self.assertEqual(
            (
                self.profiles_dir
                / "default.json"
            ).read_text(
                encoding="utf-8"
            ),
            '{"name": "Factory"}\n',
        )

        self.assertTrue(
            self.logs_dir.is_dir()
        )

        self.assertTrue(
            self.diagnostics_dir.is_dir()
        )

    def test_existing_settings_are_preserved(self):
        self.settings_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.settings_file.write_text(
            '{"user": true}\n',
            encoding="utf-8",
        )

        self.bootstrap()

        self.assertEqual(
            self.settings_file.read_text(
                encoding="utf-8"
            ),
            '{"user": true}\n',
        )

    def test_existing_profile_prevents_default_seed(
        self,
    ):
        self.profiles_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        user_profile = (
            self.profiles_dir
            / "my-profile.json"
        )

        user_profile.write_text(
            '{"name": "Mine"}\n',
            encoding="utf-8",
        )

        self.bootstrap()

        self.assertTrue(
            user_profile.is_file()
        )

        self.assertFalse(
            (
                self.profiles_dir
                / "default.json"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
