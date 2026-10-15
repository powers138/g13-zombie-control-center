import hid


G13_VENDOR_ID = 0x046D
G13_PRODUCT_ID = 0xC21C


def _format_path(path) -> str:
    if isinstance(path, bytes):
        return path.decode(errors="replace")

    return str(path)


def open_g13():
    candidate_paths = []
    seen_paths = set()

    for device_info in hid.enumerate():
        if device_info.get("vendor_id") != G13_VENDOR_ID:
            continue

        if device_info.get("product_id") != G13_PRODUCT_ID:
            continue

        path = device_info.get("path")

        if path and path not in seen_paths:
            seen_paths.add(path)
            candidate_paths.append(path)

    if not candidate_paths:
        raise RuntimeError(
            "Logitech G13 not found. "
            "Expected USB device 046d:c21c."
        )

    errors = []

    for path in candidate_paths:
        try:
            device = hid.Device(path=path)

            print(
                f"Opened Logitech G13 at "
                f"{_format_path(path)}"
            )

            return device

        except Exception as error:
            errors.append(
                f"{_format_path(path)}: {error}"
            )

    error_text = "\n".join(errors)

    raise RuntimeError(
        "The Logitech G13 was detected, but none of its "
        f"HID interfaces could be opened:\n{error_text}"
    )

def set_g13_backlight(
    device,
    red: int,
    green: int,
    blue: int,
) -> None:
    """
    Set the Logitech G13 RGB backlight.

    RGB values must be integers from 0 through 255.
    """
    colors = {
        "red": red,
        "green": green,
        "blue": blue,
    }

    for name, value in colors.items():
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 255
        ):
            raise ValueError(
                f"{name} must be an integer "
                "between 0 and 255."
            )

    # Five bytes total:
    # report ID, red, green, blue, padding.
    report = bytes(
        (
            0x07,
            red,
            green,
            blue,
            0x00,
        )
    )

    written = device.send_feature_report(report)

    if written != len(report):
        raise RuntimeError(
            "Incomplete G13 backlight report: "
            f"wrote {written} of {len(report)} bytes."
        )

def set_g13_indicator_leds(
    device,
    mask: int,
) -> None:
    """
    Set the physical M1, M2, M3, and MR indicator LEDs.

    Mask bits:
        M1 = 0x01
        M2 = 0x02
        M3 = 0x04
        MR = 0x08
    """
    if (
        not isinstance(mask, int)
        or isinstance(mask, bool)
        or not 0 <= mask <= 0x0F
    ):
        raise ValueError(
            "Indicator LED mask must be "
            "an integer from 0x00 through 0x0F."
        )

    report = bytes(
        (
            0x05,
            mask,
            0x00,
            0x00,
            0x00,
        )
    )

    written = device.send_feature_report(
        report
    )

    if written != len(report):
        raise RuntimeError(
            "Incomplete G13 indicator report: "
            f"wrote {written} of {len(report)} bytes."
        )
