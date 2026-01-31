"""ESP-IDF OTA binary metadata parser."""
from __future__ import annotations

import re
from typing import Optional, Tuple


_APP_DESC_MAGIC = 0xABCD5432
_APP_DESC_SIZE = 256
_APP_DESC_OFFSET = 24 + 8  # esp_image_header_t + esp_image_segment_header_t
_VERSION_OFFSET = 16
_VERSION_LEN = 32
_MAGIC_BYTES = _APP_DESC_MAGIC.to_bytes(4, "little", signed=False)


def _parse_desc(desc: bytes) -> tuple[Optional[str], Optional[int], Optional[str]]:
    raw_bytes = desc[_VERSION_OFFSET:_VERSION_OFFSET + _VERSION_LEN]
    raw_version = raw_bytes.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()
    if not raw_version:
        return None, None, None

    version = None
    build = None

    semver_match = re.search(r"(\d+\.\d+\.\d+)", raw_version)
    if semver_match:
        version = semver_match.group(1)

    plus_match = re.search(r"\+(\d+)", raw_version)
    if plus_match:
        try:
            build = int(plus_match.group(1))
        except ValueError:
            build = None
    else:
        build_match = re.search(r"build\s*(\d+)", raw_version, re.IGNORECASE)
        if build_match:
            try:
                build = int(build_match.group(1))
            except ValueError:
                build = None

    return version, build, raw_version


def _parse_desc_at_offset(data: bytes, offset: int) -> tuple[Optional[str], Optional[int], Optional[str]]:
    if offset < 0 or offset + _APP_DESC_SIZE > len(data):
        return None, None, None
    desc = data[offset:offset + _APP_DESC_SIZE]
    magic = int.from_bytes(desc[0:4], "little", signed=False)
    if magic != _APP_DESC_MAGIC:
        return None, None, None
    return _parse_desc(desc)


def parse_esp_app_desc_version(data: bytes) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """Parse ESP-IDF app version and build number from a firmware binary.

    Returns (version, build, raw_version).
    - version: semantic version (e.g., "1.2.3") if found
    - build: integer build number if present (e.g., from "1.2.3+4")
    - raw_version: raw version string from the binary
    """
    if not data or len(data) < _APP_DESC_SIZE:
        return None, None, None

    version, build, raw_version = _parse_desc_at_offset(data, _APP_DESC_OFFSET)
    if version or raw_version:
        return version, build, raw_version

    offset = data.find(_MAGIC_BYTES)
    while offset != -1:
        version, build, raw_version = _parse_desc_at_offset(data, offset)
        if version or raw_version:
            return version, build, raw_version
        offset = data.find(_MAGIC_BYTES, offset + 1)

    return None, None, None
