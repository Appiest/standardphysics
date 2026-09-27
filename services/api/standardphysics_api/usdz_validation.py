"""Byte-level receipt checks for uploaded usdz exports.

A usdz is a ZIP container with at least one usd payload entry inside. The
phone's room.usdz is only consumed by the display fallback, but accepting
malformed bytes pushes the first visible failure deep into a later job, or
worse, silently falls back to a geometry that was never real. Validating at
staging keeps a bad capture a specific 400 at the moment it is sent.
"""

from __future__ import annotations

import io
import zipfile

ZIP_SIGNATURE = b"PK"
USD_ENTRIES = (".usda", ".usdc", ".usd", ".usdz")
MAX_ENTRIES = 1000
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
"""A RoomPlan room.usdz is one usdc and a few textures, well under a megabyte
for the largest walk on file. The caps are far above that, and low enough that
a small archive can't claim gigabytes for the Blender conversion to unpack."""


class InvalidUsdz(ValueError):
    pass


def validate_room_usdz(payload: bytes) -> None:
    """Accept only an archive that parses and carries a usd payload entry."""
    if len(payload) < 4 or payload[:2] != ZIP_SIGNATURE:
        raise InvalidUsdz("not a usdz archive")
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            _check_entries(archive.infolist())
    except zipfile.BadZipFile as error:
        raise InvalidUsdz(f"not a valid usdz archive: {error}") from error


def _check_entries(entries: list[zipfile.ZipInfo]) -> None:
    """Sizes are the ones each entry declares. zipfile stops reading an entry there and fails its CRC."""
    if len(entries) > MAX_ENTRIES:
        raise InvalidUsdz(f"usdz has more than {MAX_ENTRIES} entries")
    if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        raise InvalidUsdz(f"usdz expands past {MAX_EXPANDED_BYTES // (1024 * 1024)} MiB")
    names = [entry.filename for entry in entries]
    if any(name.startswith("/") or ".." in name for name in names):
        raise InvalidUsdz("usdz contains unsafe entry paths")
    if not any(name.lower().endswith(USD_ENTRIES) for name in names):
        raise InvalidUsdz("usdz has no usd payload entry")
