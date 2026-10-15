from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
import stat
import tempfile
import unittest

from app_version import APP_VERSION
from diagnostics import (
    build_diagnostic_report,
    write_diagnostic_report,
)


TEST_TIME = datetime(
    2026,
    9,
    22,
    19,
    40,
    0,
    123456,
    tzinfo=timezone(
        timedelta(
            hours=-4
        )
    ),
)


class DiagnosticsTests(
    unittest.TestCase
):
    def test_report_contains_expected_context(
        self,
    ):
        report = build_diagnostic_report(
            qt_version="6.11.1",
            qt_platform="wayland",
            editor_path=Path(
                "/test/app/editor.py"
            ),
            driver_connected=True,
            active_profile="Testing.json",
            profile_source="automatic",
            window_class="org.mozilla.firefox",
            environment={
                "XDG_CURRENT_DESKTOP": "KDE",
                "DESKTOP_SESSION": "plasma",
                "XDG_SESSION_TYPE": "wayland",
            },
            generated_at=TEST_TIME,
        )

        for expected in (
            f"Version: {APP_VERSION}",
            "Qt: 6.11.1",
            "Qt platform: wayland",
            "Editor path: /test/app/editor.py",
            "XDG_CURRENT_DESKTOP: KDE",
            "DESKTOP_SESSION: plasma",
            "XDG_SESSION_TYPE: wayland",
            "Connected: Yes",
            "Active profile: Testing.json",
            "Profile source: automatic",
            "Window class: org.mozilla.firefox",
        ):
            self.assertIn(
                expected,
                report,
            )

    def test_unset_session_values_are_marked(
        self,
    ):
        report = build_diagnostic_report(
            qt_version="6.11.1",
            qt_platform="wayland",
            editor_path=Path(
                "/test/app/editor.py"
            ),
            driver_connected=False,
            active_profile=None,
            profile_source=None,
            window_class="",
            environment={},
            generated_at=TEST_TIME,
        )

        self.assertEqual(
            report.count("(unset)"),
            3,
        )

        self.assertIn(
            "Connected: No",
            report,
        )

        self.assertIn(
            "Active profile: (unknown)",
            report,
        )

        self.assertIn(
            "Window class: (none)",
            report,
        )

    def test_runtime_values_are_single_line(
        self,
    ):
        report = build_diagnostic_report(
            qt_version="6.11.1",
            qt_platform="wayland",
            editor_path=Path(
                "/test/app/editor.py"
            ),
            driver_connected=True,
            active_profile="Testing\nProfile.json",
            profile_source="application\tlocked",
            window_class="example\nwindow",
            environment={},
            generated_at=TEST_TIME,
        )

        self.assertIn(
            "Active profile: Testing Profile.json",
            report,
        )

        self.assertIn(
            "Profile source: application locked",
            report,
        )

        self.assertIn(
            "Window class: example window",
            report,
        )

    def test_writer_creates_private_report(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = (
                Path(temp)
                / "diagnostics"
            )

            report_path = write_diagnostic_report(
                qt_version="6.11.1",
                qt_platform="wayland",
                editor_path=Path(
                    "/test/app/editor.py"
                ),
                driver_connected=True,
                active_profile="Testing.json",
                profile_source="manual",
                window_class="",
                environment={},
                generated_at=TEST_TIME,
                output_dir=output_dir,
            )

            self.assertTrue(
                report_path.is_file()
            )

            self.assertEqual(
                report_path.name,
                (
                    "zombiecc-diagnostics-"
                    "20260922-194000-123456.txt"
                ),
            )

            self.assertEqual(
                stat.S_IMODE(
                    report_path.stat().st_mode
                ),
                0o600,
            )

            self.assertEqual(
                stat.S_IMODE(
                    output_dir.stat().st_mode
                ),
                0o700,
            )

            self.assertIn(
                f"Version: {APP_VERSION}",
                report_path.read_text(
                    encoding="utf-8"
                ),
            )


if __name__ == "__main__":
    unittest.main()
