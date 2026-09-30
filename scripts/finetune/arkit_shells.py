"""Walls, a doorway and a route for an ARKitScenes capture, read from its mesh, around its annotated furniture.

ARKitScenes annotates furniture as oriented boxes and gives each capture a phone-built mesh with z up, in
the same frame as the boxes. The room shell is read from that mesh on a `CELL` metre grid:

    floor     mesh points within `FLOOR_BAND` of the floor height, gaps closed, holes filled, and every
              annotated footprint added, since furniture hides the floor under it
    walls     mesh points between `WALL_BAND` above the floor, which clears most furniture, minus the
              footprints of tall annotated pieces such as shelves and fridges
    frame     the plan turned to the angle that lines the wall points up best, so walls run along the axes
    outline   the floor's boundary traced as one loop on a coarser `OUTLINE_CELL` grid, then straightened by
              Douglas-Peucker (points within `STRAIGHTEN` metres of the line between their neighbours are dropped),
              so a room has a handful of walls rather than a staircase of slivers
    open      along each straight wall, stretches with no wall points behind them are open: the scan ends
              there, or the room opens onto another
    doorway   the widest open run at least `DOOR_WIDTH[0]` wide holds the front door, up to `DOOR_WIDTH[1]` of
              it; a room with no open run that wide is skipped rather than given an invented door. Every
              other run, open or not, becomes wall, so a route cannot leave through a place the scan never saw

The route runs from the door to a table and on to a second place people use (a sofa, the kitchen, a
toilet), then back out. Homes carry no ADA space type, so commercial layout directives do not apply.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import pathlib
import urllib.request
import uuid

import numpy as np
from scipy import ndimage
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3

NAMESPACE = uuid.UUID("7c0b6f7e-1a52-4e3a-9d7b-2a8f5c1e9b44")
CELL = 0.05
FLOOR_BAND = 0.06
WALL_BAND = (1.2, 2.0)
WALL_THICKNESS = 0.1
DOOR_WIDTH = (0.8, 2.5)
OUTLINE_CELL = 0.2
"""The outline is traced on this coarser grid, so scan jitter does not break walls into slivers."""
BACKED_SHARE = 0.5
"""A run of edge is a wall when at least this share of it has wall points within `WALL_REACH`."""
WALL_REACH = 0.25
TALL = frozenset({"shelf", "cabinet", "refrigerator"})
PIECES = {
    "chair": ("Chair", "chair", True), "stool": ("Stool", "stool", True), "table": ("Table", "table", True),
    "sofa": ("Sofa", "sofa", True), "bed": ("Bed", "bed", False), "toilet": ("Toilet", "toilet", False),
    "sink": ("Sink", "sink", False), "bathtub": ("Bathtub", "bathtub", False),
    "cabinet": ("Cabinet", "storage", False), "shelf": ("Shelving unit", "storage", False),
    "refrigerator": ("Refrigerator", "refrigerator", False), "stove": ("Stove", "appliance", False),
    "oven": ("Oven", "appliance", False), "dishwasher": ("Dishwasher", "appliance", False),
    "washer": ("Washer", "appliance", False), "fireplace": ("Fireplace", "fireplace", False),
    "tv_monitor": ("Television", "television", False),
}


STRAIGHTEN = 0.3


class NoDoorway(ValueError):
    pass


def vertices(path: pathlib.Path) -> np.ndarray:
    raw = path.read_bytes()
    start = raw.index(b"end_header\n") + len(b"end_header\n")
    header = raw[:start].decode()
    count = int(next(line for line in header.splitlines() if line.startswith("element vertex")).split()[-1])
    kind = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1"), ("a", "u1")])
    data = np.frombuffer(raw, dtype=kind, count=count, offset=start)
    return np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float64)


def boxes(annotation: dict) -> list[dict]:
    found = []
    for item in annotation.get("data", []):
        obb = item.get("segments", {}).get("obbAligned")
        if obb and all(length > 0 for length in obb["axesLengths"]) and item["label"] in PIECES:
            found.append({"label": item["label"], "centre": np.array(obb["centroid"]),
                          "axes": np.array(obb["normalizedAxes"]).reshape(3, 3), "size": np.array(obb["axesLengths"])})
    return found


def floor_height(points: np.ndarray, pieces: list[dict]) -> float:
    lowest = min(piece["centre"][2] - piece["size"][2] / 2 for piece in pieces)
    near = points[np.abs(points[:, 2] - lowest) < 0.15, 2]
    return float(np.median(near)) if near.size else lowest


def footprint(piece: dict) -> np.ndarray:
    """The box's four floor corners, from its first two axes."""
    centre, axes, size = piece["centre"][:2], piece["axes"], piece["size"]
    a, b = axes[0][:2] * size[0] / 2, axes[1][:2] * size[1] / 2
    return np.array([centre - a - b, centre + a - b, centre + a + b, centre - a + b])


def best_angle(points: np.ndarray) -> float:
    """The turn, in degrees under 90, that stacks wall points into the fewest rows and columns."""
    sample = points[:: max(1, len(points) // 20000)]

    def sharpness(angle: float) -> float:
        turned = rotate(sample, -angle)
        rows = np.histogram(turned[:, 1], bins=np.arange(turned[:, 1].min(), turned[:, 1].max() + CELL, CELL))[0]
        cols = np.histogram(turned[:, 0], bins=np.arange(turned[:, 0].min(), turned[:, 0].max() + CELL, CELL))[0]
        return float((rows.astype(float) ** 2).sum() + (cols.astype(float) ** 2).sum())
    return float(max(range(0, 90), key=lambda angle: sharpness(float(angle))))


def rotate(points: np.ndarray, degrees: float) -> np.ndarray:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return points[:, :2] @ np.array([[c, s], [-s, c]])


class Grid:
    def __init__(self, points: np.ndarray):
        self.origin = points.min(axis=0) - 1.0
        self.shape = tuple(((points.max(axis=0) + 1.0 - self.origin) / CELL).astype(int) + 1)

    def cells(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        index = ((points - self.origin) / CELL).astype(int)
        keep = (index[:, 0] >= 0) & (index[:, 1] >= 0) & (index[:, 0] < self.shape[0]) & (index[:, 1] < self.shape[1])
        return index[keep, 0], index[keep, 1]

    def mask(self, points: np.ndarray, least: int = 1) -> np.ndarray:
        counts = np.zeros(self.shape, dtype=np.int32)
        np.add.at(counts, self.cells(points), 1)
        return counts >= least

    def polygon_mask(self, corners: np.ndarray) -> np.ndarray:
        """Cells whose centres lie inside a convex polygon, whichever way round its corners run."""
        xs, ys = np.meshgrid(np.arange(self.shape[0]), np.arange(self.shape[1]), indexing="ij")
        centres = np.stack([xs.ravel(), ys.ravel()], axis=1) * CELL + self.origin + CELL / 2
        sides = []
        for a, b in zip(corners, np.roll(corners, -1, axis=0), strict=True):
            edge = b - a
            sides.append(edge[0] * (centres[:, 1] - a[1]) - edge[1] * (centres[:, 0] - a[0]))
        sides = np.array(sides)
        return (np.all(sides >= 0, axis=0) | np.all(sides <= 0, axis=0)).reshape(self.shape)

    def point(self, i: float, j: float) -> np.ndarray:
        return self.origin + np.array([i, j]) * CELL


def floor_region(grid: Grid, floor_points: np.ndarray, footprints: list[np.ndarray]) -> np.ndarray:
    """The largest connected patch of floor, with furniture footprints added and holes filled."""
    mask = grid.mask(floor_points, least=2)
    for corners in footprints:
        mask |= grid.polygon_mask(corners)
    mask = ndimage.binary_closing(mask, iterations=3)
    mask = ndimage.binary_fill_holes(mask)
    labels, count = ndimage.label(mask)
    if count == 0:
        raise NoDoorway("no floor found")
    sizes = ndimage.sum(mask, labels, range(1, count + 1))
    return labels == (int(np.argmax(sizes)) + 1)


def wall_near(grid: Grid, wall_points: np.ndarray, tall: list[np.ndarray]) -> np.ndarray:
    walls = grid.mask(wall_points, least=3)
    for corners in tall:
        walls &= ~ndimage.binary_dilation(grid.polygon_mask(corners), iterations=2)
    return ndimage.binary_dilation(walls, iterations=max(1, int(WALL_REACH / CELL)))


def coarse(mask: np.ndarray, rule: str) -> np.ndarray:
    """`mask` on the outline grid: a coarse cell is floor when most of it is, and backed when any of it is."""
    factor = int(round(OUTLINE_CELL / CELL))
    rows, cols = (mask.shape[0] // factor) * factor, (mask.shape[1] // factor) * factor
    blocks = mask[:rows, :cols].reshape(rows // factor, factor, cols // factor, factor)
    return blocks.mean(axis=(1, 3)) >= 0.5 if rule == "most" else blocks.any(axis=(1, 3))


def _yaw_matrix(x: float, y: float, z: float, heading: float) -> Mat4:
    c, s = math.cos(math.radians(heading)), math.sin(math.radians(heading))
    return Mat4(m=[c, -s, 0, x, s, c, 0, y, 0, 0, 1, z, 0, 0, 0, 1])


def wall_node(name: str, start, end, outward, height: float, kind: str = "wall", label: str = "Wall") -> SceneNode:
    length = float(np.linalg.norm(end - start))
    centre = (start + end) / 2 + outward * (WALL_THICKNESS / 2 if kind == "wall" else 0.0)
    heading = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    tall = height if kind == "wall" else 2.1
    return SceneNode(id=uuid.uuid5(NAMESPACE, name), kind=kind, label=label, raw_category=kind,
                     dimensions=Vec3(x=length, y=WALL_THICKNESS, z=tall),
                     transform=_yaw_matrix(float(centre[0]), float(centre[1]), tall / 2, heading), movable=False)


def piece_node(name: str, piece: dict, floor_z: float) -> SceneNode:
    label, category, movable = PIECES[piece["label"]]
    axes, (x, y, z), size = piece["axes"], piece["centre"], piece["size"]
    return SceneNode(id=uuid.uuid5(NAMESPACE, name), kind="object", label=label, raw_category=category,
                     dimensions=Vec3(x=float(size[0]), y=float(size[1]), z=float(size[2])),
                     transform=Mat4(m=[axes[0][0], axes[1][0], axes[2][0], float(x), axes[0][1], axes[1][1],
                                       axes[2][1], float(y), axes[0][2], axes[1][2], axes[2][2], float(z - floor_z),
                                       0, 0, 0, 1]),
                     movable=movable)


SMOOTHING = 3
ENTRY_STEP = 0.6
STOP_CLEARANCE = 0.45
DESTINATIONS = ("sofa", "toilet", "sink", "bed", "stove", "refrigerator")


def _smoothed(floor: np.ndarray) -> np.ndarray:
    opened = ndimage.binary_opening(floor, iterations=SMOOTHING)
    labels, count = ndimage.label(opened)
    if count == 0:
        return floor
    sizes = ndimage.sum(opened, labels, range(1, count + 1))
    return labels == (int(np.argmax(sizes)) + 1)


def _floor_node(name: str, floor: np.ndarray, grid: Grid, angle: float) -> SceneNode:
    cells = np.argwhere(floor)
    low, high = grid.point(*cells.min(axis=0)), grid.point(*(cells.max(axis=0) + 1))
    centre = rotate(np.atleast_2d((low + high) / 2), angle)[0]
    size = high - low
    return SceneNode(id=uuid.uuid5(NAMESPACE, name), kind="floor", label="Floor", raw_category="floor",
                     dimensions=Vec3(x=float(size[0]), y=float(size[1]), z=0.01),
                     transform=_yaw_matrix(float(centre[0]), float(centre[1]), 0.0, angle), movable=False)


class Room:
    """One capture's shell and furniture, worked in the frame where its walls run along the axes."""

    def __init__(self, video_id: str, points: np.ndarray, pieces: list[dict]):
        self.video_id, self.pieces = video_id, pieces
        self.floor_z = floor_height(points, pieces)
        self.height = float(np.percentile(points[:, 2], 99) - self.floor_z)
        floor_points = points[np.abs(points[:, 2] - self.floor_z) < FLOOR_BAND]
        band = (points[:, 2] > self.floor_z + WALL_BAND[0]) & (points[:, 2] < self.floor_z + WALL_BAND[1])
        self.angle = best_angle(points[band])
        turned = [rotate(footprint(piece), -self.angle) for piece in pieces]
        self.grid = Grid(rotate(floor_points, -self.angle))
        self.floor = _smoothed(floor_region(self.grid, rotate(floor_points, -self.angle), turned))
        tall = [corners for corners, piece in zip(turned, pieces, strict=True) if piece["label"] in TALL]
        self.backed = wall_near(self.grid, rotate(points[band], -self.angle), tall)
        self.blocked = np.zeros_like(self.floor)
        for corners in turned:
            self.blocked |= self.grid.polygon_mask(corners)

    def local(self, corner: np.ndarray) -> np.ndarray:
        """An outline-grid corner in the aligned frame, in metres."""
        return self.grid.origin + corner * OUTLINE_CELL

    def world(self, point: np.ndarray) -> np.ndarray:
        return rotate(np.atleast_2d(point), self.angle)[0]

    def sides(self) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Each straight wall as (start, end, outward normal) in the aligned frame; the floor is on the left."""
        corners = [self.local(corner) for corner in outline(ndimage.binary_fill_holes(coarse(self.floor, "most")))]
        found = []
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            along = end - start
            length = float(np.linalg.norm(along))
            if length > 0:
                found.append((start, end, np.array([along[1], -along[0]]) / length))
        return found

    def backed_at(self, point: np.ndarray, outward: np.ndarray) -> bool:
        i, j = ((point + outward * CELL * 2 - self.grid.origin) / CELL).astype(int)
        inside = 0 <= i < self.backed.shape[0] and 0 <= j < self.backed.shape[1]
        return bool(inside and self.backed[i, j])

    def doorway(self, sides) -> tuple[int, float, float]:
        """(side, from, to) in metres along it: the widest open stretch with clear floor just inside."""
        best = None
        for index, (start, end, outward) in enumerate(sides):
            for low, high in self._open_stretches(start, end, outward):
                if high - low < DOOR_WIDTH[0] or not self._clear_inside(start, end, outward, (low + high) / 2):
                    continue
                if best is None or high - low > best[2] - best[1]:
                    best = (index, low, high)
        if best is None:
            raise NoDoorway("no open stretch wide enough for a door")
        index, low, high = best
        middle, width = (low + high) / 2, min(high - low, DOOR_WIDTH[1])
        return index, middle - width / 2, middle + width / 2

    def _open_stretches(self, start, end, outward) -> list[tuple[float, float]]:
        length = float(np.linalg.norm(end - start))
        direction = (end - start) / length
        steps = np.arange(0.0, length, CELL)
        open_flags = [not self.backed_at(start + direction * t, outward) for t in steps]
        stretches, begin = [], None
        for t, is_open in zip([*steps, length], [*open_flags, False], strict=True):
            if is_open and begin is None:
                begin = t
            elif not is_open and begin is not None:
                stretches.append((begin, t))
                begin = None
        return stretches

    def _clear_inside(self, start, end, outward, along: float) -> bool:
        direction = (end - start) / float(np.linalg.norm(end - start))
        inside = start + direction * along - outward * ENTRY_STEP
        spot = self.free_spot_near(self.world(inside))
        return spot is not None and float(np.linalg.norm(spot - self.world(inside))) <= 0.4

    def shell(self) -> tuple[list[SceneNode], SceneNode, np.ndarray]:
        sides = self.sides()
        door_side, low, high = self.doorway(sides)
        nodes = [_floor_node(f"{self.video_id}:floor", self.floor, self.grid, self.angle)]
        door_node = None
        for index, (start, end, outward) in enumerate(sides):
            pieces = [(start, end, "wall")]
            if index == door_side:
                direction = (end - start) / float(np.linalg.norm(end - start))
                a, b = start + direction * low, start + direction * high
                pieces = [(start, a, "wall"), (a, b, "door"), (b, end, "wall")]
            for part, (p, q, kind) in enumerate(pieces):
                if float(np.linalg.norm(q - p)) < CELL:
                    continue
                label = "Front door" if kind == "door" else "Wall"
                node = wall_node(f"{self.video_id}:{kind}:{index}:{part}", self.world(p), self.world(q),
                                 rotate(np.atleast_2d(outward), self.angle)[0], self.height, kind=kind, label=label)
                nodes.append(node)
                door_node = node if kind == "door" else door_node
        entry = self.world((a + b) / 2 - sides[door_side][2] * ENTRY_STEP)
        return nodes, door_node, entry

    def free_spot_near(self, target: np.ndarray) -> np.ndarray | None:
        """The nearest floor cell at least `STOP_CLEARANCE` from any furniture and from the floor's edge."""
        clear = self.floor & ~ndimage.binary_dilation(self.blocked, iterations=int(STOP_CLEARANCE / CELL))
        clear &= ndimage.binary_erosion(self.floor, iterations=int(STOP_CLEARANCE / CELL))
        cells = np.argwhere(clear)
        if cells.size == 0:
            return None
        local = rotate(np.atleast_2d(target), -self.angle)[0]
        centres = self.grid.origin + (cells + 0.5) * CELL
        best = centres[int(np.argmin(np.linalg.norm(centres - local, axis=1)))]
        return rotate(np.atleast_2d(best), self.angle)[0]


def _stop(name: str, room: Room, target, anchor: SceneNode | None) -> Stop | None:
    spot = room.free_spot_near(np.asarray(target, dtype=float))
    if spot is None:
        return None
    return Stop(name=name, position=Vec3(x=float(spot[0]), y=float(spot[1]), z=0.0),
                anchor_node_id=anchor.id if anchor else None)


def _first(nodes: list[SceneNode], pieces: list[dict], labels) -> tuple[SceneNode, dict] | None:
    for wanted in labels:
        for node, piece in zip(nodes, pieces, strict=False):
            if piece["label"] == wanted:
                return node, piece
    return None


def route(room: Room, door: SceneNode, entry, pieces_nodes: list[SceneNode]) -> Scenario:
    """Door, a table, one more place people use, and back out; stops sit on clear floor near each."""
    stops = [_stop("Entrance", room, entry, door)]
    for name, labels in (("Seat", ("table",)), ("Destination", DESTINATIONS)):
        found = _first(pieces_nodes, room.pieces, labels)
        if found:
            stops.append(_stop(name, room, found[1]["centre"][:2], found[0]))
    stops.append(_stop("Exit", room, entry, door))
    stops = [stop for stop in stops if stop is not None]
    if len(stops) < 3:
        raise NoDoorway("fewer than three reachable stops")
    return Scenario(name="Walk through the room", stops=stops)


def build(video_id: str, mesh: pathlib.Path, annotation: dict) -> tuple[SceneGraph, Scenario]:
    pieces = boxes(annotation)
    if not pieces:
        raise NoDoorway("no annotated furniture")
    room = Room(video_id, vertices(mesh), pieces)
    shell, door, entry = room.shell()
    furniture = [piece_node(f"{video_id}:piece:{index}", piece, room.floor_z) for index, piece in enumerate(pieces)]
    graph = SceneGraph(scan_id=uuid.uuid5(NAMESPACE, video_id), nodes=[*shell, *furniture])
    return graph, route(room, door, entry, furniture)


EDGE_STEPS = {(1, 0): ((1, 0), (1, 1)), (-1, 0): ((0, 1), (0, 0)), (0, 1): ((1, 1), (0, 1)), (0, -1): ((0, 0), (1, 0))}
"""For a floor cell whose neighbour in this direction is outside: that side's corners, in the order that keeps
the floor on the left of the edge."""


def boundary_loop(mask: np.ndarray) -> list[tuple[int, int]]:
    """The longest closed loop of cell corners around `mask`, floor on its left."""
    nexts: dict[tuple[int, int], list[tuple[int, int]]] = {}
    padded = np.pad(mask, 1)
    for i, j in np.argwhere(mask):
        for (di, dj), (a, b) in EDGE_STEPS.items():
            if not padded[i + 1 + di, j + 1 + dj]:
                nexts.setdefault((i + a[0], j + a[1]), []).append((i + b[0], j + b[1]))
    loops = []
    while nexts:
        start = next(iter(nexts))
        loop, point = [start], start
        while point in nexts:
            following = nexts[point].pop()
            if not nexts[point]:
                del nexts[point]
            point = following
            if point == start:
                break
            loop.append(point)
        loops.append(loop)
    return max(loops, key=len)


def straighten(points: list[np.ndarray], tolerance: float) -> list[np.ndarray]:
    """Douglas-Peucker on an open chain of points."""
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    line = end - start
    length = float(np.linalg.norm(line)) or 1.0
    offsets = [abs(line[0] * (p[1] - start[1]) - line[1] * (p[0] - start[0])) / length for p in points[1:-1]]
    far = int(np.argmax(offsets)) + 1
    if offsets[far - 1] <= tolerance:
        return [start, end]
    return straighten(points[:far + 1], tolerance)[:-1] + straighten(points[far:], tolerance)


def outline(mask: np.ndarray) -> list[np.ndarray]:
    """The floor's outline as a closed list of straightened corners, in outline-grid units."""
    loop = [np.array(point, dtype=float) for point in boundary_loop(mask)]
    far = int(np.argmax([np.linalg.norm(point - loop[0]) for point in loop]))
    tolerance = STRAIGHTEN / OUTLINE_CELL
    first = straighten(loop[:far + 1], tolerance)
    second = straighten([*loop[far:], loop[0]], tolerance)
    return first[:-1] + second[:-1]


MESH_URL = "https://docs-assets.developer.apple.com/ml-research/datasets/arkitscenes/v1/raw/{fold}/{video}/{video}_3dod_mesh.ply"
WINDOW_PREFIX = "arkit-"


def window_row(video_id: str, room_type: str, graph: SceneGraph, scenario: Scenario) -> dict:
    from standardphysics_agents.training.windows import Window

    window = Window(window_id=f"{WINDOW_PREFIX}{video_id}:whole", scan_id=str(graph.scan_id), centre=None,
                    graph=graph, scenario=scenario, route="scan")
    return {**window.as_dict(), "source": "arkitscenes", "room_type": room_type,
            "note": "Walls traced from the capture mesh; open stretches other than the doorway closed with wall."}


def shell_task(job: tuple[str, str, str, str]) -> dict:
    """Download one mesh, build its room, delete the mesh; a failure comes back as a reason, not an exception."""
    video_id, fold, room_type, data = job
    root = pathlib.Path(data)
    mesh = root / "meshes" / f"{video_id}.ply"
    try:
        if not mesh.exists():
            urllib.request.urlretrieve(MESH_URL.format(fold=fold, video=video_id), mesh)
        annotation = json.loads((root / "annotations" / f"{video_id}.json").read_text())
        graph, scenario = build(video_id, mesh, annotation)
        return {"ok": True, "row": window_row(video_id, room_type, graph, scenario)}
    except (NoDoorway, OSError, ValueError, KeyError) as error:
        return {"ok": False, "video_id": video_id, "why": f"{type(error).__name__}: {error}"}
    finally:
        mesh.unlink(missing_ok=True)


def run_shells(data: pathlib.Path, workers: int) -> dict:
    (data / "meshes").mkdir(exist_ok=True)
    folds = {row["video_id"]: row["fold"] for row in csv.DictReader((data / "splits.csv").open())}
    chosen = [json.loads(line) for line in (data / "selected.jsonl").read_text().splitlines() if line.strip()]
    done = {json.loads(line)["window_id"] for line in _lines(data / "windows.jsonl")}
    done |= {f"{WINDOW_PREFIX}{json.loads(line)['video_id']}:whole" for line in _lines(data / "failures.jsonl")}
    jobs = [(room["video_id"], folds[room["video_id"]], room["type"], str(data)) for room in chosen
            if f"{WINDOW_PREFIX}{room['video_id']}:whole" not in done]
    with concurrent.futures.ProcessPoolExecutor(workers) as pool:
        for result in pool.map(shell_task, jobs):
            target = "windows.jsonl" if result["ok"] else "failures.jsonl"
            with (data / target).open("a") as handle:
                handle.write(json.dumps(result["row"] if result["ok"] else result) + "\n")
    return {"rooms": len(_lines(data / "windows.jsonl")), "skipped": len(_lines(data / "failures.jsonl"))}


def _lines(path: pathlib.Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=pathlib.Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(run_shells(args.data, args.workers), indent=2))


if __name__ == "__main__":
    main()
