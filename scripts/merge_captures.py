"""Put several captures of one floor into a single scan, ready to be aligned by hand.

Four walks of the same library floor are four ARKit sessions, each with its own
origin, and nothing in the data says how they sit relative to one another. The
geometry cannot say either: a floor of parallel shelf rows correlates with
itself at many angles, so the best fit beats a rival at a completely different
turn by a per cent or two. Somebody who was standing there has to place them.

This makes that possible rather than doing it. Every capture's rooms are copied
into one scan and laid out side by side so none starts on top of another, and a
`rooms.json` records which nodes came from which walk. The workspace reads that
file, shows a "Combine rooms" tab, and lets the owner drag and turn each walk
into place on the floor plan and save the result.

What it does not do is merge the LiDAR or the photographs. Those can only be
placed once the alignment is known, so they come after: this writes the boxes
the owner aligns, and the saved alignment is what a full-floor bake would then
be built from.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import uuid

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services" / "api"))

from standardphysics_contracts import CreateScanRequest, SceneGraph  # noqa: E402
from standardphysics_pipeline.ingest import parse_room_json  # noqa: E402

from standardphysics_api import repository  # noqa: E402
from standardphysics_api.db import Database  # noqa: E402

GAP_METRES = 4.0
"""How much clear floor to leave between two walks that have not been placed yet."""


def _capture_name(directory: pathlib.Path) -> str:
    """What the phone called this walk, falling back to the folder."""
    capture = directory / "capture.json"
    if capture.is_file():
        name = json.loads(capture.read_text()).get("name")
        if name:
            return str(name)
    return directory.name[:8]


def _graph_of(directory: pathlib.Path) -> SceneGraph:
    room = directory / "room.json"
    if not room.is_file():
        room = directory / "artifacts" / "room-json"
    return parse_room_json(json.loads(room.read_text()))


def _footprint(graph: SceneGraph) -> tuple[float, float, float, float]:
    points = np.array([node.transform.m[3::4][:3] for node in graph.nodes], dtype=float)
    across, along = points[:, 0], points[:, 1]
    return float(across.min()), float(across.max()), float(along.min()), float(along.max())


def _slid(graph: SceneGraph, dx: float, dy: float, scan_id: uuid.UUID) -> list:
    """Every node of one walk, moved clear of the walks already laid down."""
    moved = []
    for node in graph.nodes:
        m = list(node.transform.m)
        m[3] += dx
        m[7] += dy
        moved.append(
            node.model_copy(
                update={
                    "id": uuid.uuid5(scan_id, f"{graph.scan_id}:{node.id}"),
                    "parent_id": (
                        uuid.uuid5(scan_id, f"{graph.scan_id}:{node.parent_id}")
                        if node.parent_id
                        else None
                    ),
                    "transform": node.transform.model_copy(update={"m": m}),
                }
            )
        )
    return moved


def _source_scan_of(directory: pathlib.Path, scans: pathlib.Path) -> str | None:
    """The scan this walk was already uploaded as, so its textured mesh can be shown.

    Placing four walks against each other means recognising them, and one white
    box is much like another. The captures were imported as scans of their own
    and those have been photographed onto their meshes, so the merged scan
    points back at them and the owner drags a room they can actually see.
    """
    mesh = directory / "lidar-mesh.json"
    if not mesh.is_file() or not scans.is_dir():
        return None
    size = mesh.stat().st_size
    for candidate in scans.iterdir():
        theirs = candidate / "artifacts" / "lidar-mesh"
        if theirs.is_file() and theirs.stat().st_size == size:
            return candidate.name
    return None


def merge(
    directories: list[pathlib.Path], scan_id: uuid.UUID, scans: pathlib.Path
) -> tuple[SceneGraph, dict]:
    """One graph holding every walk, spread out, and the manifest naming each.

    Laid out in a square rather than a line. Four walks of thirty metres set end
    to end are a hundred and twenty metres of floor to look at, which no camera
    frames without putting every room too far away to recognise; the same four
    in a square are sixty by sixty and all legible at once.
    """
    graphs = [_graph_of(directory) for directory in directories]
    footprints = [_footprint(graph) for graph in graphs]
    widest = max(right - left for left, right, _b, _t in footprints)
    deepest = max(top - bottom for _l, _r, bottom, top in footprints)
    across = math.ceil(math.sqrt(len(graphs)))

    nodes, rooms = [], []
    for index, (directory, graph, box) in enumerate(zip(directories, graphs, footprints)):
        left, _right, bottom, _top = box
        column, row = index % across, index // across
        placed = _slid(
            graph,
            column * (widest + GAP_METRES) - left,
            -row * (deepest + GAP_METRES) - bottom,
            scan_id,
        )
        nodes.extend(placed)
        rooms.append({
            "name": _capture_name(directory),
            "node_ids": [str(n.id) for n in placed],
            "source_scan_id": _source_scan_of(directory, scans),
        })
    return SceneGraph(scan_id=scan_id, revision=0, nodes=nodes), {"rooms": rooms}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=pathlib.Path)
    parser.add_argument("--name", required=True, help="what to call the merged scan")
    parser.add_argument("--owner", required=True, help="owner id the scan belongs to")
    parser.add_argument("--db", type=pathlib.Path, default=pathlib.Path("services/api/var/standardphysics.sqlite3"))
    parser.add_argument("--scans", type=pathlib.Path, default=pathlib.Path("services/api/var/scans"))
    args = parser.parse_args()

    scan_id = uuid.uuid4()
    graph, manifest = merge(args.directories, scan_id, args.scans)

    database = Database(args.db)
    with database.transaction() as connection:
        repository.insert_scan(
            connection,
            CreateScanRequest(name=args.name, device_model="merged", duration_seconds=0),
            uuid.UUID(args.owner),
            scan_id=scan_id,
            state="ready",
        )
        repository.save_revision(connection, graph, source="owner")

    directory = args.scans / str(scan_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "rooms.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"{args.name}: {scan_id}")
    for room in manifest["rooms"]:
        print(f"  {room['name']:12} {len(room['node_ids']):3} things")
    print(f"\nOpen the scan and pick Combine rooms to place the {len(manifest['rooms'])} walks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
