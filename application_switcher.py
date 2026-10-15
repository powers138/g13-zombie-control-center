from __future__ import annotations

import time

from active_window import (
    ActiveWindow,
    get_active_window,
)
from profile_manager import ProfileManager


class ApplicationProfileSwitcher:
    """
    Selects a runtime profile according to the active KDE
    window class.

    Automatic switches do not overwrite the manually selected
    profile stored in settings.json.
    """

    def __init__(
        self,
        profile_manager: ProfileManager,
    ):
        self.profile_manager = (
            profile_manager
        )

        self.last_window_class: str | None = None
        self.last_target_profile: str | None = None

    def is_enabled(self) -> bool:
        return (
            self.profile_manager
            .get_automatic_profile_switching()
        )

    def get_rules(
        self,
    ) -> list[dict[str, str]]:
        return (
            self.profile_manager
            .get_application_rules()
        )

    def get_manual_profile(self) -> str:
        profile = (
            self.profile_manager
            .settings
            .get(
                "active_profile",
                "default.json",
            )
        )

        if (
            not isinstance(profile, str)
            or not profile
        ):
            raise ValueError(
                "'active_profile' must be "
                "a profile filename."
            )

        return profile

    def resolve_profile(
        self,
        window: ActiveWindow | None,
    ) -> str:
        """
        Return the matching application profile.

        If no rule matches, return the profile selected manually
        in settings.json.
        """
        if window is not None:
            active_class = (
                window.window_class
                .strip()
                .casefold()
            )

            for rule in self.get_rules():
                rule_class = (
                    rule["window_class"]
                    .casefold()
                )

                if active_class == rule_class:
                    return rule["profile"]

        return self.get_manual_profile()

    def check(self) -> bool:
        """
        Check the active window and switch runtime profiles when
        necessary.

        Returns True when the runtime profile changed.
        """
        if not self.is_enabled():
            return False

        window = get_active_window()

        target_profile = self.resolve_profile(
            window
        )

        window_class = (
            window.window_class
            if window is not None
            else ""
        )

        self.last_window_class = (
            window_class
        )

        if (
            target_profile
            == self.profile_manager
            .active_profile_name
        ):
            self.last_target_profile = (
                target_profile
            )

            return False

        available_profiles = (
            self.profile_manager
            .list_profiles()
        )

        if target_profile not in available_profiles:
            print(
                "Automatic profile switch skipped: "
                f"'{target_profile}' does not exist."
            )

            return False

        self.profile_manager.set_active_profile(
            target_profile,
            persist=False,
        )

        self.last_target_profile = (
            target_profile
        )

        if window_class:
            print(
                "Automatic profile switch:",
                f"{window_class} -> "
                f"{target_profile}",
            )
        else:
            print(
                "Automatic profile fallback:",
                target_profile,
            )

        return True


def main() -> None:
    manager = ProfileManager()

    switcher = ApplicationProfileSwitcher(
        manager
    )

    print(
        "Automatic profile switching:",
        (
            "enabled"
            if switcher.is_enabled()
            else "disabled"
        ),
    )

    print(
        "Manual fallback profile:",
        switcher.get_manual_profile(),
    )

    print("Application rules:")

    for rule in switcher.get_rules():
        print(
            " ",
            rule["window_class"],
            "->",
            rule["profile"],
        )

    print(
        "\nWatching the active application. "
        "Press Ctrl+C to stop."
    )

    try:
        while True:
            try:
                manager.reload_if_changed()
                switcher.check()

            except Exception as error:
                print(
                    "Application switch error:",
                    error,
                )

            time.sleep(0.5)

    except KeyboardInterrupt:
        print(
            "\nApplication switch test stopped."
        )


if __name__ == "__main__":
    main()
