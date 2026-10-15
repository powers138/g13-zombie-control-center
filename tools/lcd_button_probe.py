from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(
    __file__
).resolve().parents[1]

if str(
    PROJECT_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            PROJECT_ROOT
        ),
    )


from device import open_g13


# Ignore analog-stick drift while looking for button changes.
IGNORED_BYTE_INDEXES = {
    1,
    2,
}


def format_changes(
    previous: bytes,
    current: bytes,
) -> str:
    changes = []

    maximum_length = max(
        len(previous),
        len(current),
    )

    for index in range(maximum_length):
        old_value = (
            previous[index]
            if index < len(previous)
            else 0
        )

        new_value = (
            current[index]
            if index < len(current)
            else 0
        )

        if old_value == new_value:
            continue

        if index in IGNORED_BYTE_INDEXES:
            continue

        changes.append(
            f"byte[{index}]: "
            f"0x{old_value:02X} -> "
            f"0x{new_value:02X}"
        )

    return ", ".join(changes)


def main() -> None:
    device = open_g13()
    previous: bytes | None = None

    print()
    print("LCD BUTTON PROBE")
    print("----------------")
    print(
        "Press and release each LCD button separately, "
        "working from left to right."
    )
    print("Keep the joystick centered.")
    print("Press Ctrl+C when finished.")
    print()

    try:
        while True:
            report = device.read(
                64,
                timeout=500,
            )

            if not report:
                continue

            current = bytes(report)

            if previous is None:
                previous = current

                print(
                    "Initial report:",
                    current.hex(" "),
                )

                continue

            changes = format_changes(
                previous,
                current,
            )

            if changes:
                print(
                    "Report:",
                    current.hex(" "),
                )

                print(
                    "Changes:",
                    changes,
                )

                print()

            previous = current

    except KeyboardInterrupt:
        print("\nProbe stopped.")

    finally:
        device.close()


if __name__ == "__main__":
    main()
