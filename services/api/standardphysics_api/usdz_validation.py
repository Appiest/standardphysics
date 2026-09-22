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


class InvalidUsdz(ValueError):
    pass


def validate_room_usdz(payload: bytes) -> None:
    """Accept only an archive that parses and carries a usd payload entry."""
    if len(payload) < 4 or payload[:2] != ZIP_SIGNATURE:
        raise InvalidUsdz("not a usdz archive")
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            entries = archive.namelist()
            if any(entry.startswith("/") or ".." in entry for entry in entries):
                raise InvalidUsdz("usdz contains unsafe entry paths")
            if not any(entry.lower().endswith(USD_ENTRIES) for entry in entries):
                raise InvalidUsdz("usdz has no usd payload entry")
    except zipfile.BadZipFile as error:
        raise InvalidUsdz(f"not a valid usdz archive: {error}") from error
