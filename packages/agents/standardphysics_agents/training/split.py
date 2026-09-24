"""Train and held-out rooms, divided by place and never by window.

Evaluation has to measure how a model does somewhere it has never seen, so a
held-out room shares no furniture with any training room. Whole scans are held
out, and so is one area of a big scan: every window standing on one of its
floor sheets, which on a merged library capture is one room of the library.
A training window that shares a piece with a held-out window is dropped rather
than kept, because keeping it would put held-out furniture in training.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

TRAIN = "train"
HELDOUT = "heldout"
DROPPED = "dropped_shares_heldout_furniture"


@dataclass(frozen=True)
class RoomRecord:
    window_id: str
    scan_id: str
    floor_id: str | None
    pieces: frozenset[str]
    """Ids of the non-structural nodes in the window."""


@dataclass(frozen=True)
class HoldOut:
    scans: frozenset[str] = frozenset()
    floors: frozenset[tuple[str, str]] = frozenset()
    """(scan id, floor node id) areas held out of a big scan."""

    def holds(self, room: RoomRecord) -> bool:
        return room.scan_id in self.scans or (room.scan_id, room.floor_id) in self.floors


def pick_floor(rooms: list[RoomRecord], scan_id: str, wanted: int) -> str | None:
    """The floor of `scan_id` whose window count is closest to `wanted`, fewer windows winning a tie."""
    counts = Counter(room.floor_id for room in rooms if room.scan_id == scan_id and room.floor_id)
    if not counts:
        return None
    return min(counts, key=lambda floor: (abs(counts[floor] - wanted), counts[floor], floor))


def assign(rooms: list[RoomRecord], hold_out: HoldOut) -> dict[str, str]:
    """Each window's split: train, held out, or dropped for sharing furniture with a held-out window."""
    held = [room for room in rooms if hold_out.holds(room)]
    held_pieces = frozenset().union(*(room.pieces for room in held)) if held else frozenset()
    splits = {}
    for room in rooms:
        if hold_out.holds(room):
            splits[room.window_id] = HELDOUT
        elif room.pieces & held_pieces:
            splits[room.window_id] = DROPPED
        else:
            splits[room.window_id] = TRAIN
    return splits


def split_report(rooms: list[RoomRecord], splits: dict[str, str], names: dict[str, str]) -> dict:
    by_scan: dict[str, Counter] = {}
    for room in rooms:
        by_scan.setdefault(names.get(room.scan_id, room.scan_id), Counter())[splits[room.window_id]] += 1
    return {
        "totals": dict(Counter(splits.values())),
        "by_scan": {name: dict(counts) for name, counts in sorted(by_scan.items())},
    }
