from pathlib import Path
import tempfile
import unittest

from app_version import (
    APP_VERSION,
    read_version,
)


class AppVersionTests(
    unittest.TestCase
):
    def test_release_version(self):
        self.assertEqual(
            APP_VERSION,
            "1.0.0",
        )

    def test_reads_valid_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "VERSION"
            )

            path.write_text(
                "2.4.7\n",
                encoding="utf-8",
            )

            self.assertEqual(
                read_version(path),
                "2.4.7",
            )

    def test_rejects_invalid_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "VERSION"
            )

            path.write_text(
                "Version 1.0\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                RuntimeError
            ):
                read_version(path)

    def test_rejects_missing_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "VERSION"
            )

            with self.assertRaises(
                RuntimeError
            ):
                read_version(path)


if __name__ == "__main__":
    unittest.main()
