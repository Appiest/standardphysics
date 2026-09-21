"""Real scanned scenes, and the same scenes with every name made meaningless.

Nothing here builds a room. A scene comes off a phone or it does not exist, because
a room somebody wrote down contains exactly the structure they thought to put in
it, which is the assumption the whole design exists to remove.

The scrambled copy is the special-case detector. The geometry is untouched and only
the words change, so any question that scores worse against it was being answered
from an English word rather than from a measurement.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import random
import sqlite3
from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import SceneGraph, measured_as

ROOM_JSON_LOCATIONS = (
    ("datasets/phone", "*/room.json"),
    ("services/api/var/scans", "*/artifacts/room-json"),
)
"""Where a scan that came off a phone lands, in the two places it lands."""

SYLLABLES = ("ka", "vor", "mel", "tuk", "shi", "bren", "nol", "quay", "dris", "fen",
             "gol", "hax", "jun", "lir", "morv", "pesh", "rud", "sev", "thal", "zib")


class NoRealScenes(RuntimeError):
    """Raised rather than falling back to something built by hand."""


@dataclass(frozen=True)
class Scene:
    """One scanned room, named the way the scan named it."""

    scan_id: str
    source: pathlib.Path
    graph: SceneGraph
    scrambled: bool = False

    title: str = ""

    @property
    def name(self) -> str:
        return self.title or self.source.parent.name

    def fingerprint(self) -> str:
        """What the scan measured, independent of where the file sits.

        The same room reaches this machine twice, once in `datasets/phone` and
        once uploaded, and the two copies must not land on opposite sides of the
        held-out split.
        """
        measured = sorted(
            tuple(round(value, 4) for value in node.dimensions.as_tuple())
            for node in self.graph.nodes
        )
        return hashlib.sha256(repr(measured).encode()).hexdigest()[:16]

    def regions(self) -> list[dict]:
        """What a model is allowed to see: measured extents, and a name per region.

        No kind, no category and no parent. A question writer that can see the
        ontology writes questions the ontology already answers.

        Width, depth and height are read the way a person with a tape measure
        would, through `measured_as`. They were taken straight off the region's
        own frame in the order they happened to be listed, which showed a
        bookcase 0.46 m deep and 1.96 m tall as 0.46 m tall and 1.96 m deep, and
        had every question and every verdict about either judged against the
        wrong figure.
        """
        return [
            {
                "id": str(node.id),
                "name": node.label,
                "width_m": round(measured_as(node).x, 3),
                "depth_m": round(measured_as(node).y, 3),
                "height_m": round(measured_as(node).z, 3),
                "centre_m": [round(value, 3) for value in node.transform.position.as_tuple()],
            }
            for node in self.graph.nodes
        ]


def real_scenes(root: pathlib.Path) -> list[Scene]:
    """Every scan on this machine, newest last, with none of them invented."""
    scenes = [
        _read(path)
        for directory, pattern in ROOM_JSON_LOCATIONS
        for path in sorted((root / directory).glob(pattern))
    ]
    found = _without_duplicates(scene for scene in scenes if scene is not None)
    if not found:
        raise NoRealScenes(
            "No scanned rooms found. Upload a scan or put one in datasets/phone; "
            "this suite does not run against a room built by hand."
        )
    return found


def _without_duplicates(scenes) -> list[Scene]:
    """One entry per room measured, keeping whichever copy was found first."""
    seen: dict[str, Scene] = {}
    for scene in scenes:
        seen.setdefault(scene.fingerprint(), scene)
    return list(seen.values())


def _read(path: pathlib.Path) -> Scene | None:
    from standardphysics_pipeline.ingest import parse_room_json

    try:
        graph = parse_room_json(json.loads(path.read_text()))
    except (json.JSONDecodeError, ValueError, KeyError):
        return None
    if not graph.nodes:
        return None
    return Scene(scan_id=str(graph.scan_id), source=path, graph=graph, title=_title(path))


def _title(path: pathlib.Path) -> str:
    """The name the owner typed, when the scan came in through the API."""
    if path.name == "room.json":
        return path.parent.name
    database = path.parents[2].parent / "standardphysics.sqlite3"
    if not database.exists():
        return path.parents[1].name
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "select name from scans where id = ?", (path.parents[1].name,)
        ).fetchone()
    return row[0] if row and row[0] else path.parents[1].name


def split(scenes: list[Scene], seed: int, held_out: int) -> tuple[list[Scene], list[Scene]]:
    """Scenes to build against, and scenes to be scored on.

    The split is drawn from the run seed rather than written down, so nobody can
    develop against the scenes the score comes from.
    """
    if len(scenes) <= held_out:
        raise NoRealScenes(
            f"{len(scenes)} scanned rooms is not enough to hold {held_out} out. Scan another room."
        )
    order = list(scenes)
    random.Random(seed).shuffle(order)
    return order[held_out:], order[:held_out]


def scramble(scene: Scene, seed: int) -> Scene:
    """The same room with every coined name replaced by a token nobody has a word for.

    `kind` is left alone because it is still a closed set of six; the ratchet in
    `tests/test_ontology_is_not_fixed.py` counts that, and this cannot scramble
    what the type system will not let it change.
    """
    tokens = _tokens(len(scene.graph.nodes), seed)
    nodes = [
        node.model_copy(update={"label": token, "raw_category": token})
        for node, token in zip(scene.graph.nodes, tokens)
    ]
    return Scene(
        scan_id=scene.scan_id,
        source=scene.source,
        graph=scene.graph.model_copy(update={"nodes": nodes}),
        scrambled=True,
        title=scene.title,
    )


def _tokens(count: int, seed: int) -> list[str]:
    """Enough distinct nonsense words to name every region once."""
    generator = random.Random(f"scramble-{seed}")
    seen: set[str] = set()
    while len(seen) < count:
        seen.add(generator.choice(SYLLABLES) + generator.choice(SYLLABLES))
    return sorted(seen)


def names(scene: Scene) -> dict[UUID, str]:
    return {node.id: node.label for node in scene.graph.nodes}
