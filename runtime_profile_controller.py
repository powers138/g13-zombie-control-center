from __future__ import annotations

import argparse
import copy
import os
import time
from dataclasses import dataclass
from typing import Any

from active_window import (
    ActiveWindow,
    get_active_window,
)
from application_switcher import (
    ApplicationProfileSwitcher,
)
from profile_manager import ProfileManager


@dataclass(frozen=True)
class RuntimeProfileStatus:
    active_profile: str
    source: str
    locked_profile: str | None
    window_class: str
    automatic_enabled: bool


class RuntimeProfileController:
    """
    Owns the final decision about which profile is active.

    Priority:

        1. Locked profile
        2. Temporary LCD-selected profile
        3. Matching application rule
        4. Manually selected fallback profile
    """

    def __init__(
        self,
        profile_manager: ProfileManager,
    ):
        self.profile_manager = (
            profile_manager
        )

        self.application_switcher = (
            ApplicationProfileSwitcher(
                profile_manager
            )
        )

        self.last_status: (
            RuntimeProfileStatus | None
        ) = None

        self.temporary_profile: str | None = None
        self.temporary_window_id: str | None = None

        self.onboard_profiles: dict[
            str,
            dict[str, Any],
        ] = {}

        # Last onboard data successfully applied to the profile manager.
        self._applied_onboard_profile: (
            tuple[str, dict[str, Any]] | None
        ) = None

        self.transient_locked_profile: (
            str | None
        ) = None

        self.application_locked_profile: (
            str | None
        ) = None

        self.application_locked_pid: (
            int | None
        ) = None

        self.application_locked_window_class: (
            str | None
        ) = None

    def get_status(
        self,
    ) -> RuntimeProfileStatus:
        if self.last_status is not None:
            return self.last_status

        status = self._resolve_status()

        self.last_status = status

        return status

    def check(self) -> bool:
        """
        Resolve and activate the correct runtime profile.

        Returns True when the selected profile changes or new
        contents are applied to the same onboard profile.
        """
        status = self._resolve_status()

        onboard_profile = (
            self.onboard_profiles.get(
                status.active_profile
            )
        )

        onboard_data_changed = (
            onboard_profile is not None
            and self._applied_onboard_profile
            != (
                status.active_profile,
                onboard_profile,
            )
        )

        changed = (
            status.active_profile
            != self.profile_manager
            .active_profile_name
            or onboard_data_changed
        )

        if changed:
            if onboard_profile is not None:
                snapshot = copy.deepcopy(
                    onboard_profile
                )

                self.profile_manager.set_active_profile_data(
                    status.active_profile,
                    copy.deepcopy(snapshot),
                )

                # Record success only after the data was applied.
                self._applied_onboard_profile = (
                    status.active_profile,
                    snapshot,
                )

            else:
                self.profile_manager.set_active_profile(
                    status.active_profile,
                    persist=False,
                )

                self._applied_onboard_profile = None

        if status != self.last_status:
            self._print_status(status)

        self.last_status = status

        return changed

    def select_manual_profile(
        self,
        filename: str,
    ) -> bool:
        """
        Change the manual fallback profile.

        A locked profile or matching application rule may still
        override it.
        """
        self._require_profile(filename)
        self.temporary_profile = None
        self.temporary_window_id = None

        self.profile_manager.set_active_profile(
            filename,
            persist=True,
        )

        return self.check()

    def select_temporary_profile(
        self,
        filename: str,
    ) -> bool:
        """
        Temporarily activate a profile for the current window.

        The saved manual fallback is not changed. When the
        active window changes, normal automatic matching and
        fallback behavior resume.
        """
        self._require_profile(filename)

        window: ActiveWindow | None = None

        if self.application_switcher.is_enabled():
            window = get_active_window()

        self.temporary_profile = filename

        self.temporary_window_id = (
            window.window_id
            if window is not None
            else None
        )

        return self.check()

    def lock_profile(
        self,
        filename: str | None = None,
    ) -> bool:
        """
        Lock a profile as the highest-priority runtime profile.

        When filename is omitted, lock the currently active
        runtime profile.
        """
        if filename is None:
            filename = (
                self.profile_manager
                .active_profile_name
            )

        self._require_profile(filename)

        self.temporary_profile = None
        self.temporary_window_id = None

        if filename in self.onboard_profiles:
            self.transient_locked_profile = (
                filename
            )

            self.profile_manager.set_locked_profile(
                None
            )

        else:
            self.transient_locked_profile = None

            self.profile_manager.set_locked_profile(
                filename
            )

        return self.check()

    def unlock_profile(self) -> bool:
        """
        Remove the profile lock and resume automatic switching.
        """
        self.transient_locked_profile = None

        self.profile_manager.set_locked_profile(
            None
        )

        return self.check()

    def toggle_lock(self) -> bool:
        locked_profile = (
            self.transient_locked_profile
            or self.profile_manager
            .get_locked_profile()
        )

        if locked_profile is None:
            return self.lock_profile()

        return self.unlock_profile()

    def list_profiles(self) -> list[str]:
        return (
            self.profile_manager.list_profiles()
            + list(
                self.onboard_profiles.keys()
            )
        )


    def set_onboard_profiles(
        self,
        profiles: dict[
            str,
            dict[str, Any],
        ],
    ) -> None:
        normalized = {}

        for identifier, profile_data in (
            profiles.items()
        ):
            if (
                not isinstance(
                    identifier,
                    str,
                )
                or not identifier.startswith(
                    "onboard:"
                )
            ):
                raise ValueError(
                    "Onboard runtime profile IDs "
                    "must start with 'onboard:'."
                )

            if not isinstance(
                profile_data,
                dict,
            ):
                raise ValueError(
                    f"Onboard profile "
                    f"{identifier!r} must contain "
                    "profile data."
                )

            normalized[
                identifier
            ] = copy.deepcopy(
                profile_data
            )

        self.onboard_profiles = normalized

        if (
            self.temporary_profile is not None
            and self.temporary_profile.startswith(
                "onboard:"
            )
            and self.temporary_profile
            not in self.onboard_profiles
        ):
            self.temporary_profile = None
            self.temporary_window_id = None

        if (
            self.transient_locked_profile
            is not None
            and self.transient_locked_profile
            not in self.onboard_profiles
        ):
            self.transient_locked_profile = None

        self.last_status = None

    def profile_after(
        self,
        filename: str,
        movement: int,
    ) -> str:
        """
        Return another profile filename for LCD-style scrolling.

        movement:
            -1 = previous
             1 = next
        """
        profiles = self.list_profiles()

        if not profiles:
            raise RuntimeError(
                "No profiles are available."
            )

        if movement == 0:
            return filename

        try:
            current_index = profiles.index(
                filename
            )
        except ValueError:
            current_index = 0

        new_index = (
            current_index + movement
        ) % len(profiles)

        return profiles[new_index]

    @staticmethod
    def _pid_is_running(
        pid: int | None,
    ) -> bool:
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
        ):
            return False

        try:
            os.kill(
                pid,
                0,
            )

        except ProcessLookupError:
            return False

        except PermissionError:
            return True

        return True

    def _clear_application_lock(
        self,
    ) -> None:
        self.application_locked_profile = None
        self.application_locked_pid = None
        self.application_locked_window_class = None

    def _application_lock_rule_exists(
        self,
    ) -> bool:
        profile = (
            self.application_locked_profile
        )

        window_class = (
            self.application_locked_window_class
        )

        if (
            profile is None
            or not window_class
        ):
            return False

        normalized_class = (
            window_class.strip().casefold()
        )

        for rule in (
            self.application_switcher
            .get_rules()
        ):
            if (
                rule["profile"] == profile
                and rule["window_class"]
                .strip()
                .casefold()
                == normalized_class
            ):
                return True

        return False

    def _application_lock_is_valid(
        self,
        available_profiles: set[str],
    ) -> bool:
        profile = (
            self.application_locked_profile
        )

        if profile is None:
            return False

        if profile not in available_profiles:
            self._clear_application_lock()
            return False

        if not self._pid_is_running(
            self.application_locked_pid
        ):
            print(
                "Application lock released:",
                profile,
            )

            self._clear_application_lock()
            return False

        if not self._application_lock_rule_exists():
            self._clear_application_lock()
            return False

        if not (
            self.profile_manager
            .get_profile_lock_while_running(
                profile
            )
        ):
            self._clear_application_lock()
            return False

        return True

    def _resolve_status(
        self,
    ) -> RuntimeProfileStatus:
        local_profiles = set(
            self.profile_manager.list_profiles()
        )

        available_profiles = (
            local_profiles
            | set(
                self.onboard_profiles.keys()
            )
        )

        if not available_profiles:
            raise RuntimeError(
                "No profiles are available."
            )

        locked_profile = (
            self.transient_locked_profile
            or self.profile_manager
            .get_locked_profile()
        )

        automatic_enabled = (
            self.application_switcher
            .is_enabled()
        )

        if not automatic_enabled:
            self._clear_application_lock()

        window: ActiveWindow | None = None

        if automatic_enabled:
            window = get_active_window()

        window_class = (
            window.window_class
            if window is not None
            else ""
        )

        if (
            self.temporary_profile is not None
            and self.temporary_window_id is not None
            and window is not None
            and window.window_id
            != self.temporary_window_id
        ):
            self.temporary_profile = None
            self.temporary_window_id = None

        # Highest priority: profile lock.
        if locked_profile is not None:
            if locked_profile in available_profiles:
                return RuntimeProfileStatus(
                    active_profile=locked_profile,
                    source="locked",
                    locked_profile=locked_profile,
                    window_class=window_class,
                    automatic_enabled=(
                        automatic_enabled
                    ),
                )

            print(
                "Locked profile no longer exists:",
                locked_profile,
            )

            if (
                locked_profile
                == self.transient_locked_profile
            ):
                self.transient_locked_profile = None

            else:
                self.profile_manager.set_locked_profile(
                    None
                )

            locked_profile = None

        # Second priority: temporary LCD selection for the
        # current active window.
        temporary_profile = (
            self.temporary_profile
        )

        if temporary_profile is not None:
            if temporary_profile in available_profiles:
                return RuntimeProfileStatus(
                    active_profile=temporary_profile,
                    source="temporary",
                    locked_profile=None,
                    window_class=window_class,
                    automatic_enabled=(
                        automatic_enabled
                    ),
                )

            print(
                "Temporary profile no longer exists:",
                temporary_profile,
            )

            self.temporary_profile = None
            self.temporary_window_id = None

        # Third priority: a profile that is locked while its
        # matched application process is still running.
        if (
            automatic_enabled
            and self._application_lock_is_valid(
                available_profiles
            )
        ):
            return RuntimeProfileStatus(
                active_profile=(
                    self.application_locked_profile
                ),
                source="application_locked",
                locked_profile=None,
                window_class=window_class,
                automatic_enabled=(
                    automatic_enabled
                ),
            )

        # Fourth priority: matching application rule.
        matched_profile = self._matching_profile(
            window
        )

        if (
            matched_profile is not None
            and matched_profile
            in available_profiles
        ):
            source = "automatic"

            if (
                window is not None
                and window.pid is not None
                and self.profile_manager
                .get_profile_lock_while_running(
                    matched_profile
                )
            ):
                self.application_locked_profile = (
                    matched_profile
                )

                self.application_locked_pid = (
                    window.pid
                )

                self.application_locked_window_class = (
                    window.window_class
                )

                source = "application_locked"

            return RuntimeProfileStatus(
                active_profile=matched_profile,
                source=source,
                locked_profile=None,
                window_class=window_class,
                automatic_enabled=(
                    automatic_enabled
                ),
            )

        if (
            matched_profile is not None
            and matched_profile
            not in available_profiles
        ):
            print(
                "Application profile does not exist:",
                matched_profile,
            )

        # Final priority: manual fallback.
        manual_profile = (
            self.application_switcher
            .get_manual_profile()
        )

        if manual_profile not in local_profiles:
            manual_profile = sorted(
                local_profiles
            )[0]

            print(
                "Manual fallback was not found; "
                f"using {manual_profile}."
            )

        return RuntimeProfileStatus(
            active_profile=manual_profile,
            source="manual",
            locked_profile=None,
            window_class=window_class,
            automatic_enabled=(
                automatic_enabled
            ),
        )

    def _matching_profile(
        self,
        window: ActiveWindow | None,
    ) -> str | None:
        if window is None:
            return None

        active_class = (
            window.window_class
            .strip()
            .casefold()
        )

        if not active_class:
            return None

        for rule in (
            self.application_switcher
            .get_rules()
        ):
            rule_class = (
                rule["window_class"]
                .strip()
                .casefold()
            )

            if active_class == rule_class:
                return rule["profile"]

        return None

    def _require_profile(
        self,
        filename: str,
    ) -> None:
        if filename not in self.list_profiles():
            raise FileNotFoundError(
                f"Profile was not found: "
                f"{filename}"
            )

    @staticmethod
    def _print_status(
        status: RuntimeProfileStatus,
    ) -> None:
        details = [
            f"profile={status.active_profile}",
            f"source={status.source}",
        ]

        if status.locked_profile:
            details.append(
                f"locked={status.locked_profile}"
            )

        if status.window_class:
            details.append(
                f"class={status.window_class}"
            )

        print(
            "Runtime profile:",
            ", ".join(details),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Test runtime profile selection and locking."
        )
    )

    action_group = (
        parser.add_mutually_exclusive_group()
    )

    action_group.add_argument(
        "--lock",
        metavar="PROFILE",
        help="Lock the named profile.",
    )

    action_group.add_argument(
        "--unlock",
        action="store_true",
        help="Remove the current profile lock.",
    )

    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Watch active applications continuously."
        ),
    )

    arguments = parser.parse_args()

    manager = ProfileManager()

    controller = RuntimeProfileController(
        manager
    )

    if arguments.lock:
        controller.lock_profile(
            arguments.lock
        )

    elif arguments.unlock:
        controller.unlock_profile()

    else:
        controller.check()

    status = controller.get_status()

    print(
        "Active:",
        status.active_profile,
    )

    print(
        "Source:",
        status.source,
    )

    print(
        "Locked:",
        (
            status.locked_profile
            if status.locked_profile
            else "none"
        ),
    )

    if not arguments.watch:
        return

    print(
        "\nWatching runtime profile state. "
        "Press Ctrl+C to stop."
    )

    try:
        while True:
            try:
                manager.reload_if_changed()
                controller.check()

            except Exception as error:
                print(
                    "Runtime profile error:",
                    error,
                )

            time.sleep(0.5)

    except KeyboardInterrupt:
        print(
            "\nRuntime profile test stopped."
        )


if __name__ == "__main__":
    main()
