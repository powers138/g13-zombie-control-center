from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import editor_theme


class EditorThemePaletteTests(unittest.TestCase):
    def test_theme_names_and_labels_are_stable(self):
        self.assertEqual(
            editor_theme.EDITOR_THEME_OPTIONS,
            (
                ("classic", "Classic"),
                ("blue", "Blue"),
                ("green", "Green"),
            ),
        )

    def test_requested_core_palette_colors_are_used(self):
        blue = editor_theme.BLUE_EDITOR_THEME
        green = editor_theme.GREEN_EDITOR_THEME

        self.assertEqual(
            blue["BG_WINDOW"],
            "#0A1128",
        )
        self.assertEqual(
            blue["BG_TEAL_CONTROL"],
            "#001F54",
        )
        self.assertEqual(
            blue["BG_TEAL_HOVER"],
            "#034078",
        )

        self.assertEqual(
            green["BG_WINDOW"],
            "#283618",
        )
        self.assertEqual(
            green["BG_TEAL_CONTROL"],
            "#606C38",
        )
        self.assertEqual(
            blue["BG_TEAL_HOVER"],
            "#034078",
        )
        self.assertEqual(
            blue["ACCENT"],
            "#4D8CCF",
        )

        self.assertEqual(
            green["BG_WINDOW"],
            "#283618",
        )
        self.assertEqual(
            green["BG_TEAL_CONTROL"],
            "#606C38",
        )
        self.assertEqual(
            green["ACCENT"],
            "#A3B45E",
        )

    def test_text_and_semantic_colors_are_shared(self):
        shared_names = {
            name
            for name in editor_theme.CLASSIC_EDITOR_THEME
            if name.startswith("TEXT_")
        }

        shared_names.update(
            {
                "BG_WARNING",
                "BG_WARNING_HOVER",
                "BG_DELETE",
                "WARNING",
                "WARNING_HOVER",
                "BORDER_DELETE",
                "BG_LIVE",
                "BORDER_LIVE",
                "BG_LOCKED",
                "BORDER_LOCKED",
                "BORDER_LOCKED_HOVER",
                "BG_DISABLED_GROUP",
                "BG_DISABLED_CONTROL",
                "BG_CHECKBOX_DISABLED",
                "BORDER_DISABLED_GROUP",
                "BORDER_DISABLED_CONTROL",
                "BORDER_CHECKBOX_DISABLED",
            }
        )

        classic = editor_theme.CLASSIC_EDITOR_THEME

        for theme_name in (
            "blue",
            "green",
        ):
            palette = editor_theme.EDITOR_THEMES[
                theme_name
            ]

            for name in shared_names:
                self.assertEqual(
                    palette[name],
                    classic[name],
                    msg=(
                        f"{theme_name} changed shared "
                        f"color role {name}"
                    ),
                )

    def test_classic_theme_keeps_existing_colors(self):
        classic = editor_theme.CLASSIC_EDITOR_THEME

        self.assertEqual(
            classic["BG_WINDOW"],
            "#202226",
        )
        self.assertEqual(
            classic["BG_SHELL"],
            "#071b20",
        )
        self.assertEqual(
            classic["ACCENT"],
            "#39c9e5",
        )
        self.assertEqual(
            classic["WARNING"],
            "#dd7a24",
        )

    def test_stylesheet_tokens_are_replaced(self):
        stylesheet = editor_theme.theme_stylesheet(
            "QWidget { background: $BG_WINDOW; "
            "border-color: $ACCENT; }"
        )

        self.assertNotIn(
            "$BG_WINDOW",
            stylesheet,
        )
        self.assertNotIn(
            "$ACCENT",
            stylesheet,
        )


class EditorThemeSettingsTests(unittest.TestCase):
    def test_missing_setting_defaults_to_classic(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = (
                Path(directory)
                / "settings.json"
            )

            self.assertEqual(
                editor_theme.load_editor_theme_name(
                    settings_path
                ),
                "classic",
            )

    def test_invalid_setting_defaults_to_classic(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = (
                Path(directory)
                / "settings.json"
            )

            settings_path.write_text(
                json.dumps(
                    {
                        "editor_theme": "invalid",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                editor_theme.load_editor_theme_name(
                    settings_path
                ),
                "classic",
            )

    def test_save_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = (
                Path(directory)
                / "settings.json"
            )

            settings_path.write_text(
                json.dumps(
                    {
                        "active_profile": "default.json",
                        "automatic_profile_switching": True,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            editor_theme.save_editor_theme_name(
                "green",
                settings_path,
            )

            settings = json.loads(
                settings_path.read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                settings["active_profile"],
                "default.json",
            )
            self.assertTrue(
                settings["automatic_profile_switching"]
            )
            self.assertEqual(
                settings["editor_theme"],
                "green",
            )

    def test_invalid_theme_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = (
                Path(directory)
                / "settings.json"
            )

            with self.assertRaises(ValueError):
                editor_theme.save_editor_theme_name(
                    "invalid",
                    settings_path,
                )

            self.assertFalse(
                settings_path.exists()
            )


if __name__ == "__main__":
    unittest.main()
