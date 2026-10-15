from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SteamGame:
    app_id: int
    name: str
    install_dir: str
    library_path: Path
    manifest_path: Path

    @property
    def window_class(self) -> str:
        return f"steam_app_{self.app_id}"


def _steam_root_candidates() -> list[Path]:
    home = Path.home()

    return [
        home / ".local/share/Steam",
        home / ".steam/root",
        home / ".steam/steam",
        home / ".steam/debian-installation",
        (
            home
            / ".var/app/com.valvesoftware.Steam"
            / "data/Steam"
        ),
    ]


def _unescape_vdf(
    value: str,
) -> str:
    return (
        value
        .replace(
            r"\\",
            "\\",
        )
        .replace(
            r"\"",
            '"',
        )
    )


def _vdf_values(
    text: str,
    key: str,
) -> list[str]:
    pattern = re.compile(
        (
            r'^\s*"'
            + re.escape(key)
            + r'"\s*"((?:\\.|[^"])*)"\s*$'
        ),
        re.MULTILINE,
    )

    return [
        _unescape_vdf(
            match.group(1)
        )
        for match in pattern.finditer(
            text
        )
    ]


def _vdf_value(
    text: str,
    key: str,
) -> str | None:
    values = _vdf_values(
        text,
        key,
    )

    if not values:
        return None

    return values[0]


def find_steam_libraries() -> list[Path]:
    libraries: list[Path] = []
    seen: set[str] = set()

    def add_library(
        path: Path,
    ) -> None:
        try:
            normalized = path.expanduser().resolve(
                strict=False
            )
        except OSError:
            normalized = path.expanduser()

        key = str(
            normalized
        )

        if key in seen:
            return

        steamapps = (
            normalized
            / "steamapps"
        )

        if not steamapps.is_dir():
            return

        seen.add(
            key
        )

        libraries.append(
            normalized
        )

    steam_roots: list[Path] = []

    for candidate in (
        _steam_root_candidates()
    ):
        if candidate.is_dir():
            steam_roots.append(
                candidate
            )

            add_library(
                candidate
            )

    for steam_root in steam_roots:
        library_files = [
            (
                steam_root
                / "steamapps"
                / "libraryfolders.vdf"
            ),
            (
                steam_root
                / "config"
                / "libraryfolders.vdf"
            ),
        ]

        for library_file in (
            library_files
        ):
            try:
                text = (
                    library_file
                    .read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                )
            except OSError:
                continue

            for raw_path in (
                _vdf_values(
                    text,
                    "path",
                )
            ):
                if not raw_path:
                    continue

                add_library(
                    Path(raw_path)
                )

    return libraries


def _read_manifest(
    manifest_path: Path,
    library_path: Path,
) -> SteamGame | None:
    try:
        text = manifest_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None

    raw_app_id = _vdf_value(
        text,
        "appid",
    )

    name = _vdf_value(
        text,
        "name",
    )

    install_dir = _vdf_value(
        text,
        "installdir",
    )

    if raw_app_id is None:
        match = re.fullmatch(
            r"appmanifest_(\d+)\.acf",
            manifest_path.name,
        )

        if match is None:
            return None

        raw_app_id = match.group(1)

    try:
        app_id = int(
            raw_app_id
        )
    except ValueError:
        return None

    if app_id <= 0:
        return None

    if not name:
        name = (
            f"Steam App {app_id}"
        )

    if install_dir is None:
        install_dir = ""

    return SteamGame(
        app_id=app_id,
        name=name,
        install_dir=install_dir,
        library_path=library_path,
        manifest_path=manifest_path,
    )

def _is_steam_system_component(
    game: SteamGame,
) -> bool:
    name = game.name.casefold()

    if name.startswith(
        "proton "
    ):
        return True

    if name.startswith(
        "steam linux runtime"
    ):
        return True

    if name == (
        "steamworks common redistributables"
    ):
        return True

    return False

def scan_steam_games() -> list[SteamGame]:
    games_by_id: dict[
        int,
        SteamGame,
    ] = {}

    for library in (
        find_steam_libraries()
    ):
        steamapps = (
            library
            / "steamapps"
        )

        for manifest_path in sorted(
            steamapps.glob(
                "appmanifest_*.acf"
            )
        ):
            game = _read_manifest(
                manifest_path,
                library,
            )

            if game is None:
                continue

            if _is_steam_system_component(
                game
            ):
                continue

            games_by_id[
                game.app_id
            ] = game

    return sorted(
        games_by_id.values(),
        key=lambda game: (
            game.name.casefold(),
            game.app_id,
        ),
    )


def main() -> None:
    libraries = (
        find_steam_libraries()
    )

    print(
        "Steam libraries:"
    )

    if libraries:
        for library in libraries:
            print(
                f"  {library}"
            )
    else:
        print(
            "  None found"
        )

    print()

    games = scan_steam_games()

    print(
        f"Steam applications found: "
        f"{len(games)}"
    )

    print()

    for game in games:
        print(
            f"{game.app_id:<10} "
            f"{game.name}"
        )


if __name__ == "__main__":
    main()
