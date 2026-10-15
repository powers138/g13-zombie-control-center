from __future__ import annotations

import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent

MONOGRAM_FILE = (
    PROJECT_DIR
    / "assets"
    / "monogram-bitmap.json"
)

# Printable ASCII glyphs use five drawn pixels and advance
# six pixels horizontally, leaving one spacing column.
MONOGRAM_GLYPH_WIDTH = 5
MONOGRAM_GLYPH_HEIGHT = 12
MONOGRAM_ADVANCE = 6

ASCII_FIRST = 32
ASCII_LAST = 126


def _load_monogram_glyphs() -> dict[
    str,
    tuple[int, ...],
]:
    if not MONOGRAM_FILE.exists():
        raise FileNotFoundError(
            "Monogram bitmap font was not found:\n"
            f"{MONOGRAM_FILE}"
        )

    try:
        raw_glyphs = json.loads(
            MONOGRAM_FILE.read_text(
                encoding="utf-8"
            )
        )

    except json.JSONDecodeError as error:
        raise ValueError(
            "Invalid Monogram JSON: "
            f"line {error.lineno}, "
            f"column {error.colno}: "
            f"{error.msg}"
        ) from error

    if not isinstance(raw_glyphs, dict):
        raise ValueError(
            "Monogram JSON must contain "
            "an object of glyphs."
        )

    glyphs: dict[
        str,
        tuple[int, ...],
    ] = {}

    maximum_row_value = (
        1 << MONOGRAM_GLYPH_WIDTH
    ) - 1

    for character, raw_rows in raw_glyphs.items():
        if (
            not isinstance(character, str)
            or len(character) != 1
        ):
            continue

        # The downloaded font contains extended Unicode
        # characters whose bitmaps may be wider. The LCD menu
        # currently uses printable ASCII only.
        character_code = ord(character)

        if not (
            ASCII_FIRST
            <= character_code
            <= ASCII_LAST
        ):
            continue

        if (
            not isinstance(raw_rows, list)
            or len(raw_rows)
            != MONOGRAM_GLYPH_HEIGHT
        ):
            raise ValueError(
                f"Glyph {character!r} must contain "
                f"{MONOGRAM_GLYPH_HEIGHT} rows."
            )

        rows: list[int] = []

        for row in raw_rows:
            if (
                not isinstance(row, int)
                or isinstance(row, bool)
                or not 0 <= row <= maximum_row_value
            ):
                raise ValueError(
                    f"ASCII glyph {character!r} "
                    "contains an invalid bitmap row: "
                    f"{row!r}"
                )

            rows.append(row)

        glyphs[character] = tuple(rows)

    if "?" not in glyphs:
        raise ValueError(
            "Monogram bitmap data does not "
            "contain an ASCII '?' fallback glyph."
        )

    if " " not in glyphs:
        glyphs[" "] = (
            0,
        ) * MONOGRAM_GLYPH_HEIGHT

    return glyphs


MONOGRAM_GLYPHS = _load_monogram_glyphs()


def get_monogram_glyph(
    character: str,
) -> tuple[int, ...]:
    if not character:
        character = "?"

    return MONOGRAM_GLYPHS.get(
        character,
        MONOGRAM_GLYPHS["?"],
    )


def monogram_text_width(
    text: str,
) -> int:
    lines = text.splitlines() or [""]

    return max(
        (
            len(line) * MONOGRAM_ADVANCE
            for line in lines
        ),
        default=0,
    )
