from __future__ import annotations

from pathlib import Path
import re


VERSION_FILE = (
    Path(__file__)
    .resolve()
    .parent
    / "VERSION"
)

_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)"
    r"\."
    r"(?:0|[1-9][0-9]*)"
    r"\."
    r"(?:0|[1-9][0-9]*)"
)


def read_version(
    path: Path = VERSION_FILE,
) -> str:
    try:
        value = path.read_text(
            encoding="utf-8"
        ).strip()

    except OSError as error:
        raise RuntimeError(
            f"Application version file could "
            f"not be read: {path}"
        ) from error

    if (
        _VERSION_PATTERN.fullmatch(
            value
        )
        is None
    ):
        raise RuntimeError(
            "Application version must use "
            "MAJOR.MINOR.PATCH format."
        )

    return value


APP_VERSION = read_version()
