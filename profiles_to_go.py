import copy
import fcntl
import json
import os
import struct
import time
import xml.etree.ElementTree as ET
import zlib

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from device import open_g13


TOTAL_MEMORY_SIZE = 0x10000
BANK_SIZE = 0x8000

PAGE_COUNT = 256
PAGE_SIZE = 256
REPORT_SIZE = 258

ONBOARD_READ_ATTEMPTS = 5
ONBOARD_READ_RETRY_DELAY = 0.1
ONBOARD_WRITE_PAGE_DELAY = 0.010
ONBOARD_WRITE_READBACK_DELAY = 0.25

READ_COMMAND = bytes(
    (
        0x04,
        0x02,
        0x00,
        0x00,
        0x00,
    )
)

ZOMBIE_FORMAT = "g13-zombie-profiles"
ZOMBIE_FORMAT_VERSION = 1
MAX_ONBOARD_PROFILES = 5

ONBOARD_CACHE_PATH = (
    Path(
        os.environ.get(
            "XDG_RUNTIME_DIR",
            "/tmp",
        )
    )
    / (
        "g13-zombie-onboard-memory-"
        f"{os.getuid()}.bin"
    )
)

PROFILES_TO_GO_LOCK_PATH = (
    Path("/tmp")
    / (
        "g13-zombie-profiles-to-go-"
        f"{os.getuid()}.lock"
    )
)


@contextmanager
def _profiles_to_go_lock():
    with PROFILES_TO_GO_LOCK_PATH.open(
        "a+b"
    ) as lock_file:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX,
        )

        try:
            yield

        finally:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN,
            )

@dataclass(
    frozen=True
)
class OnboardBank:
    name: str
    device_name: str
    generation: int
    used_bytes: int
    free_bytes: int
    compressed_bytes: int
    xml_bytes: int
    profile_names: tuple[str, ...]
    xml_data: bytes
    storage_format: str


@dataclass(
    frozen=True
)
class OnboardStatus:
    current_bank: OnboardBank
    banks: tuple[OnboardBank, ...]
    bank_errors: tuple[str, ...]
    memory_image: bytes

@dataclass(
    frozen=True
)
class OnboardProfile:
    slot: int
    name: str
    storage_format: str
    filename: str | None
    profile_data: dict[str, Any] | None
    xml_data: bytes | None

def _local_name(
    tag: str,
) -> str:
    return tag.rsplit(
        "}",
        1,
    )[-1]

def _read_u32(
    data: bytes,
    offset: int,
) -> int:
    return struct.unpack_from(
        "<I",
        data,
        offset,
    )[0]


def _read_onboard_memory_once() -> bytes:
    device = open_g13()

    try:
        written = device.send_feature_report(
            READ_COMMAND
        )

        if written != len(READ_COMMAND):
            raise RuntimeError(
                "Incomplete Profiles To Go read command: "
                f"wrote {written} of "
                f"{len(READ_COMMAND)} bytes."
            )

        memory = bytearray()

        for expected_page in range(
            PAGE_COUNT
        ):
            report = bytes(
                device.get_feature_report(
                    0x06,
                    REPORT_SIZE,
                )
            )

            if len(report) != REPORT_SIZE:
                raise RuntimeError(
                    f"Onboard page "
                    f"0x{expected_page:02X}: "
                    f"expected {REPORT_SIZE} bytes, "
                    f"received {len(report)}."
                )

            if report[0] != 0x06:
                raise RuntimeError(
                    f"Onboard page "
                    f"0x{expected_page:02X}: "
                    "unexpected report ID "
                    f"0x{report[0]:02X}."
                )

            page_number = report[1]

            if page_number != expected_page:
                raise RuntimeError(
                    "Expected onboard page "
                    f"0x{expected_page:02X}, "
                    "received "
                    f"0x{page_number:02X}."
                )

            memory.extend(
                report[2:]
            )

        if len(memory) != TOTAL_MEMORY_SIZE:
            raise RuntimeError(
                "Expected "
                f"{TOTAL_MEMORY_SIZE:,} bytes of "
                "onboard memory, received "
                f"{len(memory):,}."
            )

        return bytes(
            memory
        )

    finally:
        device.close()

def _read_onboard_memory_unlocked() -> bytes:
    for attempt in range(
        ONBOARD_READ_ATTEMPTS
    ):
        try:
            return _read_onboard_memory_once()

        except (
            OSError,
            RuntimeError,
        ):
            if (
                attempt
                + 1
                >= ONBOARD_READ_ATTEMPTS
            ):
                raise

            time.sleep(
                ONBOARD_READ_RETRY_DELAY
            )

    raise RuntimeError(
        "Unable to read G13 onboard memory."
    )

def read_onboard_memory() -> bytes:
    with _profiles_to_go_lock():
        return _read_onboard_memory_unlocked()



def parse_bank(
    bank_data: bytes,
    bank_name: str,
) -> OnboardBank:
    if len(bank_data) != BANK_SIZE:
        raise RuntimeError(
            f"Bank {bank_name}: expected "
            f"{BANK_SIZE:,} bytes, received "
            f"{len(bank_data):,}."
        )

    if bank_data[:4] != b"SGL\x00":
        raise RuntimeError(
            f"Bank {bank_name}: "
            "missing SGL container header."
        )

    container_length = _read_u32(
        bank_data,
        4,
    )

    container_end = (
        8
        + container_length
    )

    if container_end > BANK_SIZE:
        raise RuntimeError(
            f"Bank {bank_name}: "
            "container extends beyond bank."
        )

    chunks = {}

    offset = 8

    while offset < container_end:
        if offset + 8 > container_end:
            raise RuntimeError(
                f"Bank {bank_name}: "
                "truncated chunk header."
            )

        tag = bank_data[
            offset:
            offset + 4
        ]

        chunk_length = _read_u32(
            bank_data,
            offset + 4,
        )

        data_start = (
            offset
            + 8
        )

        data_end = (
            data_start
            + chunk_length
        )

        if data_end > container_end:
            raise RuntimeError(
                f"Bank {bank_name}: "
                f"chunk {tag!r} extends "
                "past the container."
            )

        chunks[tag] = bank_data[
            data_start:
            data_end
        ]

        offset = data_end

    for required_tag in (
        b"EMAN",
        b"LMXZ",
        b"POTS",
    ):
        if required_tag not in chunks:
            raise RuntimeError(
                f"Bank {bank_name}: "
                f"missing {required_tag!r} chunk."
            )

    device_name = (
        chunks[b"EMAN"]
        .decode(
            "utf-16-le"
        )
        .rstrip("\x00")
    )

    compressed_payload = (
        chunks[b"LMXZ"]
    )

    try:
        payload_data = zlib.decompress(
            compressed_payload
        )

    except zlib.error as error:
        raise RuntimeError(
            f"Bank {bank_name}: "
            f"invalid compressed payload: {error}"
        ) from error

    stop_data = chunks[b"POTS"]

    if len(stop_data) != 4:
        raise RuntimeError(
            f"Bank {bank_name}: "
            "unexpected POTS length."
        )

    generation = _read_u32(
        stop_data,
        0,
    )

    profile_names = []

    try:
        json_payload = json.loads(
            payload_data.decode(
                "utf-8"
            )
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        json_payload = None

    if json_payload is not None:
        if not isinstance(
            json_payload,
            dict,
        ):
            raise RuntimeError(
                f"Bank {bank_name}: "
                "unsupported JSON payload."
            )

        if (
            json_payload.get("format")
            != ZOMBIE_FORMAT
        ):
            raise RuntimeError(
                f"Bank {bank_name}: "
                "unknown onboard JSON format."
            )

        if (
            json_payload.get("version")
            != ZOMBIE_FORMAT_VERSION
        ):
            raise RuntimeError(
                f"Bank {bank_name}: "
                "unsupported Zombie onboard "
                "format version."
            )

        profile_entries = (
            json_payload.get(
                "profiles"
            )
        )

        if not isinstance(
            profile_entries,
            list,
        ):
            raise RuntimeError(
                f"Bank {bank_name}: "
                "Zombie profile list is invalid."
            )

        if (
            len(profile_entries)
            > MAX_ONBOARD_PROFILES
        ):
            raise RuntimeError(
                f"Bank {bank_name}: "
                "Zombie profile list exceeds "
                "the five-profile limit."
            )

        for entry in profile_entries:
            if not isinstance(
                entry,
                dict,
            ):
                raise RuntimeError(
                    f"Bank {bank_name}: "
                    "Zombie profile entry "
                    "is invalid."
                )

            profile = entry.get(
                "profile"
            )

            if not isinstance(
                profile,
                dict,
            ):
                raise RuntimeError(
                    f"Bank {bank_name}: "
                    "Zombie profile data "
                    "is invalid."
                )

            profile_name = profile.get(
                "name"
            )

            if not isinstance(
                profile_name,
                str,
            ) or not profile_name.strip():
                raise RuntimeError(
                    f"Bank {bank_name}: "
                    "Zombie profile has no "
                    "valid name."
                )

            profile_names.append(
                profile_name
            )

        storage_format = "zombie"

    else:
        try:
            root = ET.fromstring(
                payload_data
            )

        except ET.ParseError as error:
            raise RuntimeError(
                f"Bank {bank_name}: "
                f"invalid profile XML: {error}"
            ) from error

        for element in root.iter():
            tag_name = (
                element.tag
                .split("}")[-1]
                .lower()
            )

            if tag_name != "profile":
                continue

            profile_name = (
                element.attrib.get(
                    "name"
                )
            )

            if (
                profile_name
                and profile_name
                not in profile_names
            ):
                profile_names.append(
                    profile_name
                )

        storage_format = "logitech"

    return OnboardBank(
        name=bank_name,
        device_name=device_name,
        generation=generation,
        used_bytes=container_end,
        free_bytes=(
            BANK_SIZE
            - container_end
        ),
        compressed_bytes=len(
            compressed_payload
        ),
        xml_bytes=len(
            payload_data
        ),
        profile_names=tuple(
            profile_names
        ),
        xml_data=payload_data,
        storage_format=storage_format,
    )

def build_bank(
    xml_data: bytes,
    generation: int,
    device_name: str = "G13",
) -> bytes:
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or not 0 <= generation <= 0xFFFFFFFF
    ):
        raise ValueError(
            "Generation must be an integer "
            "from 0 through 0xFFFFFFFF."
        )

    try:
        ET.fromstring(
            xml_data
        )

    except ET.ParseError as error:
        raise RuntimeError(
            f"Cannot build onboard bank from "
            f"invalid profile XML: {error}"
        ) from error

    name_data = (
        device_name.encode(
            "utf-16-le"
        )
        + b"\x00\x00"
    )

    compressed_xml = zlib.compress(
        xml_data,
        level=9,
    )

    name_chunk = (
        b"EMAN"
        + struct.pack(
            "<I",
            len(name_data),
        )
        + name_data
    )

    xml_chunk = (
        b"LMXZ"
        + struct.pack(
            "<I",
            len(compressed_xml),
        )
        + compressed_xml
    )

    stop_data = struct.pack(
        "<I",
        generation,
    )

    stop_chunk = (
        b"POTS"
        + struct.pack(
            "<I",
            len(stop_data),
        )
        + stop_data
    )

    payload = (
        name_chunk
        + xml_chunk
        + stop_chunk
    )

    container = (
        b"SGL\x00"
        + struct.pack(
            "<I",
            len(payload),
        )
        + payload
    )

    if len(container) > BANK_SIZE:
        raise RuntimeError(
            "Onboard profile data is too large: "
            f"{len(container):,} bytes required, "
            f"{BANK_SIZE:,} bytes available."
        )

    bank_data = container.ljust(
        BANK_SIZE,
        b"\x00",
    )

    validated = parse_bank(
        bank_data,
        "generated",
    )

    if validated.generation != generation:
        raise RuntimeError(
            "Generated onboard bank failed "
            "generation validation."
        )

    if validated.device_name != device_name:
        raise RuntimeError(
            "Generated onboard bank failed "
            "device-name validation."
        )

    if validated.xml_data != xml_data:
        raise RuntimeError(
            "Generated onboard bank failed "
            "XML round-trip validation."
        )

    return bank_data

def build_zombie_bank(
    profile_entries: list[dict[str, Any]],
    generation: int,
    device_name: str = "G13",
) -> bytes:
    if (
        not isinstance(
            profile_entries,
            list,
        )
    ):
        raise ValueError(
            "Zombie onboard profiles "
            "must be a list."
        )

    if (
        len(profile_entries)
        > MAX_ONBOARD_PROFILES
    ):
        raise ValueError(
            "The G13 can store at most "
            "five onboard profiles."
        )

    normalized_entries = []

    for entry in profile_entries:
        if not isinstance(
            entry,
            dict,
        ):
            raise ValueError(
                "Each Zombie onboard profile "
                "entry must be an object."
            )

        filename = entry.get(
            "filename"
        )

        profile = entry.get(
            "profile"
        )

        if not isinstance(
            filename,
            str,
        ) or not filename.strip():
            raise ValueError(
                "Each Zombie onboard profile "
                "must have a filename."
            )

        if not isinstance(
            profile,
            dict,
        ):
            raise ValueError(
                "Each Zombie onboard profile "
                "must contain profile data."
            )

        profile_name = profile.get(
            "name"
        )

        if not isinstance(
            profile_name,
            str,
        ) or not profile_name.strip():
            raise ValueError(
                "Each Zombie onboard profile "
                "must have a valid name."
            )

        normalized_entries.append(
            {
                "filename": filename,
                "profile": profile,
            }
        )

    payload_object = {
        "format": ZOMBIE_FORMAT,
        "version": ZOMBIE_FORMAT_VERSION,
        "profiles": normalized_entries,
    }

    json_data = json.dumps(
        payload_object,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8"
    )

    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or not 0 <= generation <= 0xFFFFFFFF
    ):
        raise ValueError(
            "Generation must be an integer "
            "from 0 through 0xFFFFFFFF."
        )

    name_data = (
        device_name.encode(
            "utf-16-le"
        )
        + b"\x00\x00"
    )

    compressed_payload = zlib.compress(
        json_data,
        level=9,
    )

    name_chunk = (
        b"EMAN"
        + struct.pack(
            "<I",
            len(name_data),
        )
        + name_data
    )

    payload_chunk = (
        b"LMXZ"
        + struct.pack(
            "<I",
            len(compressed_payload),
        )
        + compressed_payload
    )

    stop_data = struct.pack(
        "<I",
        generation,
    )

    stop_chunk = (
        b"POTS"
        + struct.pack(
            "<I",
            len(stop_data),
        )
        + stop_data
    )

    payload = (
        name_chunk
        + payload_chunk
        + stop_chunk
    )

    container = (
        b"SGL\x00"
        + struct.pack(
            "<I",
            len(payload),
        )
        + payload
    )

    if len(container) > BANK_SIZE:
        raise RuntimeError(
            "Zombie onboard profiles are too large: "
            f"{len(container):,} bytes required, "
            f"{BANK_SIZE:,} bytes available."
        )

    bank_data = container.ljust(
        BANK_SIZE,
        b"\x00",
    )

    validated = parse_bank(
        bank_data,
        "generated",
    )

    if (
        validated.storage_format
        != "zombie"
    ):
        raise RuntimeError(
            "Generated Zombie bank failed "
            "format validation."
        )

    if (
        validated.generation
        != generation
    ):
        raise RuntimeError(
            "Generated Zombie bank failed "
            "generation validation."
        )

    expected_names = tuple(
        entry["profile"]["name"]
        for entry in normalized_entries
    )

    if (
        validated.profile_names
        != expected_names
    ):
        raise RuntimeError(
            "Generated Zombie bank failed "
            "profile-list validation."
        )

    return bank_data

def get_zombie_profile_entries(
    bank: OnboardBank,
) -> tuple[dict[str, Any], ...]:
    if (
        bank.storage_format
        != "zombie"
    ):
        raise ValueError(
            "Onboard bank is not in "
            "Zombie format."
        )

    payload = json.loads(
        bank.xml_data.decode(
            "utf-8"
        )
    )

    profiles = payload.get(
        "profiles",
        [],
    )

    return tuple(
        profiles
    )

def get_onboard_profiles(
    bank: OnboardBank,
) -> tuple[OnboardProfile, ...]:
    profiles = []

    if bank.storage_format == "zombie":
        entries = get_zombie_profile_entries(
            bank
        )

        for slot, entry in enumerate(
            entries,
            start=1,
        ):
            filename = entry.get(
                "filename"
            )

            profile_data = entry.get(
                "profile"
            )

            if not isinstance(
                filename,
                str,
            ):
                raise RuntimeError(
                    f"Zombie onboard slot {slot} "
                    "has an invalid filename."
                )

            if not isinstance(
                profile_data,
                dict,
            ):
                raise RuntimeError(
                    f"Zombie onboard slot {slot} "
                    "has invalid profile data."
                )

            name = profile_data.get(
                "name"
            )

            if not isinstance(
                name,
                str,
            ) or not name.strip():
                raise RuntimeError(
                    f"Zombie onboard slot {slot} "
                    "has no valid profile name."
                )

            profiles.append(
                OnboardProfile(
                    slot=slot,
                    name=name,
                    storage_format="zombie",
                    filename=filename,
                    profile_data=copy.deepcopy(
                        profile_data
                    ),
                    xml_data=None,
                )
            )

        return tuple(
            profiles
        )

    if bank.storage_format != "logitech":
        raise RuntimeError(
            "Unsupported onboard storage format: "
            f"{bank.storage_format!r}."
        )

    try:
        root = ET.fromstring(
            bank.xml_data
        )

    except ET.ParseError as error:
        raise RuntimeError(
            f"Unable to parse Logitech "
            f"onboard XML: {error}"
        ) from error

    profile_elements = [
        element
        for element in root
        if _local_name(
            element.tag
        ) == "profile"
    ]

    namespace = ""

    if root.tag.startswith(
        "{"
    ):
        namespace = root.tag[
            1:
            root.tag.index(
                "}"
            )
        ]

    if namespace:
        ET.register_namespace(
            "",
            namespace,
        )

    for slot, profile_element in enumerate(
        profile_elements,
        start=1,
    ):
        name = profile_element.get(
            "name",
            "Unnamed",
        )

        output_root = ET.Element(
            root.tag,
            dict(
                root.attrib
            ),
        )

        output_root.append(
            copy.deepcopy(
                profile_element
            )
        )

        ET.indent(
            output_root,
            space="  ",
        )

        profile_xml = ET.tostring(
            output_root,
            encoding="utf-8",
            xml_declaration=True,
            short_empty_elements=True,
        )

        profiles.append(
            OnboardProfile(
                slot=slot,
                name=name,
                storage_format="logitech",
                filename=None,
                profile_data=None,
                xml_data=(
                    profile_xml
                    + b"\n"
                ),
            )
        )

    if (
        len(profiles)
        > MAX_ONBOARD_PROFILES
    ):
        raise RuntimeError(
            "Logitech onboard profile data "
            "exceeds the five-profile limit."
        )

    return tuple(
        profiles
    )


def get_onboard_profile(
    bank: OnboardBank,
    slot: int,
) -> OnboardProfile:
    if (
        not isinstance(
            slot,
            int,
        )
        or isinstance(
            slot,
            bool,
        )
        or not 1 <= slot <= MAX_ONBOARD_PROFILES
    ):
        raise ValueError(
            "Onboard profile slot must be "
            "an integer from 1 through 5."
        )

    profiles = get_onboard_profiles(
        bank
    )

    if slot > len(profiles):
        raise ValueError(
            f"Onboard profile slot {slot} "
            "is empty."
        )

    return profiles[
        slot - 1
    ]

def _write_bank_unlocked(
    bank_name: str,
    bank_data: bytes,
) -> None:
    normalized_bank_name = (
        bank_name.strip().upper()
    )

    if normalized_bank_name == "A":
        bank_selector = 0x00
        first_page = 0x00

    elif normalized_bank_name == "B":
        bank_selector = 0x01
        first_page = 0x80

    else:
        raise ValueError(
            "Bank name must be A or B."
        )

    if len(bank_data) != BANK_SIZE:
        raise RuntimeError(
            f"Bank {normalized_bank_name}: "
            f"expected {BANK_SIZE:,} bytes, "
            f"received {len(bank_data):,}."
        )

    parse_bank(
        bank_data,
        normalized_bank_name,
    )

    command = bytes(
        (
            0x04,
            0x01,
            bank_selector,
            0x00,
            0x00,
        )
    )

    device = open_g13()

    try:
        written = device.send_feature_report(
            command
        )

        if written != len(command):
            raise RuntimeError(
                "Incomplete Profiles To Go write "
                f"command: wrote {written} of "
                f"{len(command)} bytes."
            )

        time.sleep(
            0.75
        )

        bank_page_count = (
            BANK_SIZE
            // PAGE_SIZE
        )

        for page_offset in range(
            bank_page_count
        ):
            page_number = (
                first_page
                + page_offset
            )

            data_start = (
                page_offset
                * PAGE_SIZE
            )

            data_end = (
                data_start
                + PAGE_SIZE
            )

            page_data = bank_data[
                data_start:
                data_end
            ]

            report = (
                bytes(
                    (
                        0x06,
                        page_number,
                    )
                )
                + page_data
            )

            if len(report) != REPORT_SIZE:
                raise RuntimeError(
                    f"Bank {normalized_bank_name}, "
                    f"page 0x{page_number:02X}: "
                    "unexpected report size."
                )

            written = (
                device.send_feature_report(
                    report
                )
            )

            if written != len(report):
                raise RuntimeError(
                    f"Bank {normalized_bank_name}, "
                    f"page 0x{page_number:02X}: "
                    f"wrote {written} of "
                    f"{len(report)} bytes."
                )

            time.sleep(
                ONBOARD_WRITE_PAGE_DELAY
            )

    finally:
        device.close()

def _generation_is_newer(
    first: int,
    second: int,
) -> bool:
    difference = (
        first
        - second
    ) & 0xFFFFFFFF

    return (
        difference != 0
        and difference < 0x80000000
    )


def _select_current_bank(
    banks: tuple[OnboardBank, ...],
) -> OnboardBank:
    if not banks:
        raise RuntimeError(
            "No valid onboard profile banks found."
        )

    current = banks[0]

    for bank in banks[1:]:
        if _generation_is_newer(
            bank.generation,
            current.generation,
        ):
            current = bank

    return current


def parse_onboard_memory(
    memory: bytes,
) -> OnboardStatus:
    if len(memory) != TOTAL_MEMORY_SIZE:
        raise RuntimeError(
            "Expected a "
            f"{TOTAL_MEMORY_SIZE:,}-byte "
            "G13 onboard memory image; got "
            f"{len(memory):,} bytes."
        )

    banks = []
    errors = []

    for bank_name, start in (
        ("A", 0x0000),
        ("B", 0x8000),
    ):
        try:
            banks.append(
                parse_bank(
                    memory[
                        start:
                        start + BANK_SIZE
                    ],
                    bank_name,
                )
            )

        except Exception as error:
            errors.append(
                f"Bank {bank_name}: {error}"
            )

    valid_banks = tuple(
        banks
    )

    current_bank = (
        _select_current_bank(
            valid_banks
        )
    )

    return OnboardStatus(
        current_bank=current_bank,
        banks=valid_banks,
        bank_errors=tuple(
            errors
        ),
        memory_image=memory,
    )


def get_onboard_status() -> OnboardStatus:
    return parse_onboard_memory(
        read_onboard_memory()
    )

def write_onboard_cache(
    memory: bytes,
) -> None:
    if len(memory) != TOTAL_MEMORY_SIZE:
        raise ValueError(
            "Onboard cache image must contain "
            f"exactly {TOTAL_MEMORY_SIZE:,} bytes."
        )

    # Validate before publishing the cache.
    parse_onboard_memory(
        memory
    )

    ONBOARD_CACHE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = (
        ONBOARD_CACHE_PATH.with_name(
            "."
            + ONBOARD_CACHE_PATH.name
            + f".{os.getpid()}.tmp"
        )
    )

    try:
        temporary_path.write_bytes(
            memory
        )

        os.replace(
            temporary_path,
            ONBOARD_CACHE_PATH,
        )

    finally:
        temporary_path.unlink(
            missing_ok=True
        )


def get_cached_onboard_status(
) -> OnboardStatus | None:
    try:
        memory = ONBOARD_CACHE_PATH.read_bytes()

    except FileNotFoundError:
        return None

    if len(memory) != TOTAL_MEMORY_SIZE:
        raise RuntimeError(
            "Cached onboard memory image has "
            f"{len(memory):,} bytes; expected "
            f"{TOTAL_MEMORY_SIZE:,}."
        )

    return parse_onboard_memory(
        memory
    )

def _bank_start(
    bank_name: str,
) -> int:
    if bank_name == "A":
        return 0x0000

    if bank_name == "B":
        return 0x8000

    raise ValueError(
        "Bank name must be A or B."
    )


def _inactive_bank_name(
    current_bank_name: str,
) -> str:
    if current_bank_name == "A":
        return "B"

    if current_bank_name == "B":
        return "A"

    raise ValueError(
        "Current bank name must be A or B."
    )


def _write_and_verify(
    before_memory: bytes,
    new_bank: bytes,
    target_bank_name: str,
    expected_generation: int,
    expected_format: str,
    expected_profile_names: tuple[str, ...],
    verify_readback: bool = True,
) -> OnboardStatus:
    before_status = parse_onboard_memory(
        before_memory
    )

    previous_bank = (
        before_status.current_bank
    )

    if (
        target_bank_name
        != _inactive_bank_name(
            previous_bank.name
        )
    ):
        raise RuntimeError(
            "Onboard write refused: "
            "the target must be the inactive bank."
        )

    candidate = parse_bank(
        new_bank,
        "candidate",
    )

    if (
        candidate.generation
        != expected_generation
    ):
        raise RuntimeError(
            "Candidate bank has the wrong "
            "generation."
        )

    if (
        candidate.storage_format
        != expected_format
    ):
        raise RuntimeError(
            "Candidate bank has the wrong "
            "storage format."
        )

    if (
        candidate.profile_names
        != expected_profile_names
    ):
        raise RuntimeError(
            "Candidate bank profile list "
            "does not match the expected list."
        )

    with _profiles_to_go_lock():
        current_memory = (
            _read_onboard_memory_unlocked()
        )

        if current_memory != before_memory:
            raise RuntimeError(
                "Onboard write cancelled: "
                "the G13 memory has changed. "
                "Refresh onboard profiles and "
                "try again. No data was written "
                "by this operation."
            )

        _write_bank_unlocked(
            target_bank_name,
            new_bank,
        )

        if verify_readback:
            time.sleep(
                ONBOARD_WRITE_READBACK_DELAY
            )

            after_memory = (
                _read_onboard_memory_unlocked()
            )

            target_start = _bank_start(
                target_bank_name
            )

            written_bank = after_memory[
                target_start:
                target_start + BANK_SIZE
            ]

            if written_bank != new_bank:
                raise RuntimeError(
                    "Onboard write verification failed: "
                    f"Bank {target_bank_name} readback "
                    "does not exactly match the "
                    "requested bank data."
                )

        else:
            target_start = _bank_start(
                target_bank_name
            )

            after_memory = (
                before_memory[
                    :target_start
                ]
                + new_bank
                + before_memory[
                    target_start
                    + BANK_SIZE:
                ]
            )

    after_status = parse_onboard_memory(
        after_memory
    )

    current_bank = (
        after_status.current_bank
    )

    if (
        current_bank.name
        != target_bank_name
    ):
        raise RuntimeError(
            "Onboard write verification failed: "
            f"expected Bank {target_bank_name} "
            f"to become current, but Bank "
            f"{current_bank.name} is current."
        )

    if (
        current_bank.generation
        != expected_generation
    ):
        raise RuntimeError(
            "Onboard write verification failed: "
            "generation does not match."
        )

    if (
        current_bank.storage_format
        != expected_format
    ):
        raise RuntimeError(
            "Onboard write verification failed: "
            "storage format does not match."
        )

    if (
        current_bank.profile_names
        != expected_profile_names
    ):
        raise RuntimeError(
            "Onboard write verification failed: "
            "profile list does not match."
        )

    previous_start = _bank_start(
        previous_bank.name
    )

    previous_before = before_memory[
        previous_start:
        previous_start + BANK_SIZE
    ]

    previous_after = after_memory[
        previous_start:
        previous_start + BANK_SIZE
    ]

    if (
        verify_readback
        and previous_after != previous_before
    ):
        raise RuntimeError(
            "Onboard write verification failed: "
            "the previous active bank changed."
        )

    return after_status


def replace_with_zombie_profiles(
    profile_entries: list[dict[str, Any]],
    before_memory: bytes | None = None,
    verify_readback: bool = True,
) -> OnboardStatus:
    if before_memory is None:
        before_memory = read_onboard_memory()

    before_status = parse_onboard_memory(
        before_memory
    )

    current_bank = (
        before_status.current_bank
    )

    target_bank_name = (
        _inactive_bank_name(
            current_bank.name
        )
    )

    next_generation = (
        current_bank.generation
        + 1
    ) & 0xFFFFFFFF

    new_bank = build_zombie_bank(
        profile_entries,
        next_generation,
        current_bank.device_name,
    )

    expected_names = tuple(
        entry["profile"]["name"]
        for entry in profile_entries
    )

    return _write_and_verify(
        before_memory,
        new_bank,
        target_bank_name,
        next_generation,
        "zombie",
        expected_names,
        verify_readback=verify_readback,
    )


def replace_with_logitech_xml(
    xml_data: bytes,
    before_memory: bytes | None = None,
    verify_readback: bool = True,
) -> OnboardStatus:
    if before_memory is None:
        before_memory = read_onboard_memory()

    before_status = parse_onboard_memory(
        before_memory
    )

    current_bank = (
        before_status.current_bank
    )

    target_bank_name = (
        _inactive_bank_name(
            current_bank.name
        )
    )

    next_generation = (
        current_bank.generation
        + 1
    ) & 0xFFFFFFFF

    new_bank = build_bank(
        xml_data,
        next_generation,
        current_bank.device_name,
    )

    candidate = parse_bank(
        new_bank,
        "candidate",
    )

    return _write_and_verify(
        before_memory,
        new_bank,
        target_bank_name,
        next_generation,
        "logitech",
        candidate.profile_names,
        verify_readback=verify_readback,
    )

def remove_onboard_profile(
    slot: int,
    before_memory: bytes | None = None,
    verify_readback: bool = True,
) -> OnboardStatus:
    if before_memory is None:
        before_memory = read_onboard_memory()

    status = parse_onboard_memory(
        before_memory
    )

    current_bank = (
        status.current_bank
    )

    profile = get_onboard_profile(
        current_bank,
        slot,
    )

    if (
        current_bank.storage_format
        == "zombie"
    ):
        entries = list(
            get_zombie_profile_entries(
                current_bank
            )
        )

        entries.pop(
            profile.slot - 1
        )

        return replace_with_zombie_profiles(
            entries,
            before_memory=before_memory,
            verify_readback=verify_readback,
        )

    if (
        current_bank.storage_format
        != "logitech"
    ):
        raise RuntimeError(
            "Unsupported onboard storage format: "
            f"{current_bank.storage_format!r}."
        )

    try:
        root = ET.fromstring(
            current_bank.xml_data
        )

    except ET.ParseError as error:
        raise RuntimeError(
            "Unable to parse Logitech "
            f"onboard XML: {error}"
        ) from error

    profile_elements = [
        element
        for element in root
        if _local_name(
            element.tag
        ) == "profile"
    ]

    if (
        profile.slot
        > len(profile_elements)
    ):
        raise RuntimeError(
            f"Onboard profile slot "
            f"{profile.slot} disappeared "
            "before removal."
        )

    root.remove(
        profile_elements[
            profile.slot - 1
        ]
    )

    namespace = ""

    if root.tag.startswith(
        "{"
    ):
        namespace = root.tag[
            1:
            root.tag.index(
                "}"
            )
        ]

    if namespace:
        ET.register_namespace(
            "",
            namespace,
        )

    ET.indent(
        root,
        space="  ",
    )

    xml_data = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )

    return replace_with_logitech_xml(
        xml_data
        + b"\n",
        before_memory=before_memory,
        verify_readback=verify_readback,
    )

def add_logitech_onboard_profile(
    profile_xml: bytes,
    before_memory: bytes | None = None,
    verify_readback: bool = True,
) -> OnboardStatus:
    if before_memory is None:
        before_memory = read_onboard_memory()

    status = parse_onboard_memory(
        before_memory
    )

    current_bank = (
        status.current_bank
    )

    if (
        current_bank.storage_format
        != "logitech"
    ):
        raise RuntimeError(
            "The current onboard storage "
            "is not in Logitech format."
        )

    existing_profiles = (
        get_onboard_profiles(
            current_bank
        )
    )

    if (
        len(existing_profiles)
        >= MAX_ONBOARD_PROFILES
    ):
        raise RuntimeError(
            "The G13 already contains "
            "five onboard profiles."
        )

    try:
        current_root = ET.fromstring(
            current_bank.xml_data
        )

    except ET.ParseError as error:
        raise RuntimeError(
            "Unable to parse current Logitech "
            f"onboard XML: {error}"
        ) from error

    try:
        incoming_root = ET.fromstring(
            profile_xml
        )

    except ET.ParseError as error:
        raise RuntimeError(
            "Unable to parse Logitech "
            f"profile XML: {error}"
        ) from error

    incoming_profiles = [
        element
        for element in incoming_root
        if _local_name(
            element.tag
        ) == "profile"
    ]

    if len(incoming_profiles) != 1:
        raise ValueError(
            "A Logitech onboard import must "
            "contain exactly one profile."
        )

    incoming_profile = (
        incoming_profiles[0]
    )

    incoming_name = incoming_profile.get(
        "name",
        "Unnamed",
    )

    incoming_guid = (
        incoming_profile.get(
            "guid",
            ""
        )
        .strip()
        .upper()
    )

    if not incoming_guid:
        raise ValueError(
            f"Logitech profile "
            f"{incoming_name!r} has no GUID."
        )

    for existing_element in current_root:
        if (
            _local_name(
                existing_element.tag
            )
            != "profile"
        ):
            continue

        existing_guid = (
            existing_element.get(
                "guid",
                ""
            )
            .strip()
            .upper()
        )

        if (
            existing_guid
            and existing_guid
            == incoming_guid
        ):
            raise ValueError(
                f"Logitech profile "
                f"{incoming_name!r} uses a GUID "
                "that already exists onboard."
            )

    current_root.append(
        copy.deepcopy(
            incoming_profile
        )
    )

    namespace = ""

    if current_root.tag.startswith(
        "{"
    ):
        namespace = current_root.tag[
            1:
            current_root.tag.index(
                "}"
            )
        ]

    if namespace:
        ET.register_namespace(
            "",
            namespace,
        )

    ET.indent(
        current_root,
        space="  ",
    )

    xml_data = ET.tostring(
        current_root,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )

    result = replace_with_logitech_xml(
        xml_data
        + b"\n",
        before_memory=before_memory,
        verify_readback=verify_readback,
    )

    expected_names = tuple(
        profile.name
        for profile in existing_profiles
    ) + (
        incoming_name,
    )

    if (
        result.current_bank.profile_names
        != expected_names
    ):
        raise RuntimeError(
            "Logitech onboard add verification "
            "failed: profile order changed."
        )

    return result

def add_zombie_onboard_profile(
    filename: str,
    profile_data: dict[str, Any],
    before_memory: bytes | None = None,
    verify_readback: bool = True,
) -> OnboardStatus:
    if (
        not isinstance(
            filename,
            str,
        )
        or not filename.strip()
    ):
        raise ValueError(
            "Zombie onboard profile "
            "must have a filename."
        )

    if not isinstance(
        profile_data,
        dict,
    ):
        raise ValueError(
            "Zombie onboard profile "
            "data must be a dictionary."
        )

    profile_name = profile_data.get(
        "name"
    )

    if (
        not isinstance(
            profile_name,
            str,
        )
        or not profile_name.strip()
    ):
        raise ValueError(
            "Zombie onboard profile "
            "must have a valid name."
        )

    if before_memory is None:
        before_memory = read_onboard_memory()

    status = parse_onboard_memory(
        before_memory
    )

    current_bank = (
        status.current_bank
    )

    if (
        current_bank.storage_format
        != "zombie"
    ):
        raise RuntimeError(
            "The current onboard storage "
            "is not in Zombie format. "
            "The entire onboard profile set "
            "must be converted before adding "
            "a Zombie-native profile."
        )

    entries = list(
        get_zombie_profile_entries(
            current_bank
        )
    )

    if (
        len(entries)
        >= MAX_ONBOARD_PROFILES
    ):
        raise RuntimeError(
            "The G13 already contains "
            "five onboard profiles."
        )

    normalized_filename = (
        filename.strip()
    )

    for entry in entries:
        existing_filename = entry.get(
            "filename"
        )

        if (
            isinstance(
                existing_filename,
                str,
            )
            and existing_filename.casefold()
            == normalized_filename.casefold()
        ):
            raise ValueError(
                "A Zombie onboard profile "
                f"with filename "
                f"{normalized_filename!r} "
                "already exists."
            )

    entries.append(
        {
            "filename": normalized_filename,
            "profile": copy.deepcopy(
                profile_data
            ),
        }
    )

    result = replace_with_zombie_profiles(
        entries,
        before_memory=before_memory,
        verify_readback=verify_readback,
    )

    expected_names = tuple(
        entry["profile"]["name"]
        for entry in entries
    )

    if (
        result.current_bank.profile_names
        != expected_names
    ):
        raise RuntimeError(
            "Zombie onboard add verification "
            "failed: profile order changed."
        )

    return result

def build_zombie_entries_from_logitech_bank(
    bank: OnboardBank,
) -> tuple[dict[str, Any], ...]:
    if (
        bank.storage_format
        != "logitech"
    ):
        raise ValueError(
            "Onboard bank is not in "
            "Logitech format."
        )

    from import_logitech_profile import (
        convert_profile_xml,
    )

    entries = []

    for onboard_profile in get_onboard_profiles(
        bank
    ):
        if onboard_profile.xml_data is None:
            raise RuntimeError(
                f"Logitech onboard slot "
                f"{onboard_profile.slot} "
                "has no XML data."
            )

        source_name = (
            f"onboard-slot-"
            f"{onboard_profile.slot}.xml"
        )

        converted = convert_profile_xml(
            onboard_profile.xml_data,
            source_name=source_name,
        )

        entries.append(
            {
                "filename": (
                    f"onboard-slot-"
                    f"{onboard_profile.slot}.json"
                ),
                "profile": converted,
            }
        )

    return tuple(
        entries
    )


def build_logitech_xml_from_zombie_bank(
    bank: OnboardBank,
) -> bytes:
    if (
        bank.storage_format
        != "zombie"
    ):
        raise ValueError(
            "Onboard bank is not in "
            "Zombie format."
        )

    from export_logitech_profile import (
        PROFILE_NAMESPACE,
        export_profile_xml,
    )

    entries = get_zombie_profile_entries(
        bank
    )

    if (
        len(entries)
        > MAX_ONBOARD_PROFILES
    ):
        raise RuntimeError(
            "Zombie onboard profile data "
            "exceeds the five-profile limit."
        )

    ET.register_namespace(
        "",
        PROFILE_NAMESPACE,
    )

    combined_root = ET.Element(
        (
            "{"
            + PROFILE_NAMESPACE
            + "}profiles"
        )
    )

    for slot, entry in enumerate(
        entries,
        start=1,
    ):
        filename = entry.get(
            "filename"
        )

        profile_data = entry.get(
            "profile"
        )

        if not isinstance(
            filename,
            str,
        ) or not filename.strip():
            raise RuntimeError(
                f"Zombie onboard slot {slot} "
                "has an invalid filename."
            )

        if not isinstance(
            profile_data,
            dict,
        ):
            raise RuntimeError(
                f"Zombie onboard slot {slot} "
                "has invalid profile data."
            )

        profile_xml = export_profile_xml(
            profile_data,
            source_name=filename,
        )

        try:
            profile_root = ET.fromstring(
                profile_xml
            )

        except ET.ParseError as error:
            raise RuntimeError(
                f"Generated Logitech XML for "
                f"Zombie slot {slot} is invalid: "
                f"{error}"
            ) from error

        profile_elements = [
            element
            for element in profile_root
            if _local_name(
                element.tag
            ) == "profile"
        ]

        if len(profile_elements) != 1:
            raise RuntimeError(
                f"Generated Logitech XML for "
                f"Zombie slot {slot} does not "
                "contain exactly one profile."
            )

        combined_root.append(
            copy.deepcopy(
                profile_elements[0]
            )
        )

    ET.indent(
        combined_root,
        space="  ",
    )

    return (
        ET.tostring(
            combined_root,
            encoding="utf-8",
            xml_declaration=True,
            short_empty_elements=True,
        )
        + b"\n"
    )

def convert_onboard_to_zombie(
    before_memory: bytes | None = None,
    verify_readback: bool = True,
) -> OnboardStatus:
    if before_memory is None:
        before_memory = read_onboard_memory()

    before_status = parse_onboard_memory(
        before_memory
    )

    current_bank = (
        before_status.current_bank
    )

    if (
        current_bank.storage_format
        == "zombie"
    ):
        return before_status

    if (
        current_bank.storage_format
        != "logitech"
    ):
        raise RuntimeError(
            "Unsupported onboard storage format: "
            f"{current_bank.storage_format!r}."
        )

    entries = (
        build_zombie_entries_from_logitech_bank(
            current_bank
        )
    )

    target_bank_name = (
        _inactive_bank_name(
            current_bank.name
        )
    )

    next_generation = (
        current_bank.generation
        + 1
    ) & 0xFFFFFFFF

    new_bank = build_zombie_bank(
        list(
            entries
        ),
        next_generation,
        current_bank.device_name,
    )

    expected_names = tuple(
        entry["profile"]["name"]
        for entry in entries
    )

    return _write_and_verify(
        before_memory,
        new_bank,
        target_bank_name,
        next_generation,
        "zombie",
        expected_names,
        verify_readback=verify_readback,
    )


def convert_onboard_to_logitech(
    before_memory: bytes | None = None,
    verify_readback: bool = True,
) -> OnboardStatus:
    if before_memory is None:
        before_memory = read_onboard_memory()

    before_status = parse_onboard_memory(
        before_memory
    )

    current_bank = (
        before_status.current_bank
    )

    if (
        current_bank.storage_format
        == "logitech"
    ):
        return before_status

    if (
        current_bank.storage_format
        != "zombie"
    ):
        raise RuntimeError(
            "Unsupported onboard storage format: "
            f"{current_bank.storage_format!r}."
        )

    xml_data = (
        build_logitech_xml_from_zombie_bank(
            current_bank
        )
    )

    target_bank_name = (
        _inactive_bank_name(
            current_bank.name
        )
    )

    next_generation = (
        current_bank.generation
        + 1
    ) & 0xFFFFFFFF

    new_bank = build_bank(
        xml_data,
        next_generation,
        current_bank.device_name,
    )

    candidate = parse_bank(
        new_bank,
        "candidate",
    )

    return _write_and_verify(
        before_memory,
        new_bank,
        target_bank_name,
        next_generation,
        "logitech",
        candidate.profile_names,
        verify_readback=verify_readback,
    )
