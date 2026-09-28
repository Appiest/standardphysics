"""Turn an extracted Roblox OBJ storey into a room export the generator can refurnish.

Reads `<stem>_parts.json` and `<stem>_objects.json` written by the OBJ extractor
(one axis-aligned box per OBJ group, clustered into objects per storey) and
writes `{"name", "latest"}` like the real scan exports, so `ScanShell` treats
it as one more measured shell.

Scale is the one decision that matters, and it has to be read from the true
floor: the extractor's storey elevation can be a raised plate such as a counter
slab. Measured from the McDonald's `Floor1` top, the furniture agrees: table
tops 3.0 studs (0.74 m real, 0.25 m per stud), toilet seat 1.8 (0.43 m, 0.24),
booth backs 4.0 (1.1 m, 0.28). Walls (8.2 studs) and the counter slab (2.8)
imply 0.36 because Roblox builds its architecture taller than its props.
Furniture decides ADA clearances, so `METRES_PER_STUD` follows the furniture.

Kept: the storey floor, every wall part standing on it, each doorway as a door
(door leaves come out of the extractor as thin floor-standing wall parts; runs
of them become one door, and the one on the outer wall is the front door), and
every object cluster as a fixed
piece named from its shape: booth benches and their tables, toilets and
lavatories, everything else as a fixture. Movable furniture is left to the
generator, which is how scan shells are refurnished too.

    python scripts/finetune/obj_rooms.py --parts ~/Documents/sp-data/obj/mcdonalds_parts.json \
        --objects ~/Documents/sp-data/obj/mcdonalds_objects.json --name "McDonald's (Roblox)" --out room.json
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import uuid

from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3

METRES_PER_STUD = 0.27
NAMESPACE = uuid.UUID("c3a1f0d2-5b7e-4d19-8e44-0b6a2f9d7c31")
FLOOR_TOLERANCE_STUDS = 2.0
WALL_BASE_BELOW_FLOOR_STUDS = 2.5
"""Roblox walls often start inside the floor plate, a stud or two below its top."""
WALL_MIN_RISE_STUDS = 3.0
DOOR_BASE_MAX_METERS = 0.05
DOOR_LEAF_HEIGHT = (1.6, 2.2)
DOOR_LEAF_MAX_THICKNESS = 0.12
LEAF_JOIN_METERS = 0.15
DOORWAY_MIN_METERS = 0.6
"""Narrower runs are frame and mullion pieces that happen to stand at door height."""
DOOR_HEIGHT_METERS = 2.1
STANDARD_TOILET = (0.40, 0.70, 0.80)
STANDARD_LAVATORY = (0.50, 0.45, 0.85)
BOOTH_BACK_STUDS = (3.5, 4.6)
TABLE_TOP_STUDS = (2.5, 3.4)
COUNTER_TOP_STUDS = (2.0, 4.0)
COUNTER_MIN_ASPECT = 3.0
ON_A_COUNTER_STUDS = 2.0
"""An object whose underside is this far above the floor stands on something, such as a counter."""
BOOTH_FOOTPRINT_MAX_STUDS = 4.0


def _metres(studs: float) -> float:
    return studs * METRES_PER_STUD


def _box(name: str, kind: str, label: str, category: str, lo, hi, floor_y: float) -> SceneNode:
    """A node from a stud box; OBJ x stays x and OBJ z becomes -y so the plan keeps its handedness."""
    base = max(lo[1], floor_y)
    width, depth, height = _metres(hi[0] - lo[0]), _metres(hi[2] - lo[2]), _metres(hi[1] - base)
    centre = (_metres((lo[0] + hi[0]) / 2), -_metres((lo[2] + hi[2]) / 2), _metres(base - floor_y) + height / 2)
    return SceneNode(id=uuid.uuid5(NAMESPACE, name), kind=kind, label=label, raw_category=category,
                     dimensions=Vec3(x=width, y=depth, z=height), transform=Mat4.translation(*centre),
                     movable=False)


def _sized(node: SceneNode, size) -> SceneNode:
    position = node.transform.position
    return node.model_copy(update={"dimensions": Vec3(x=size[0], y=size[1], z=size[2]),
                                   "transform": Mat4.translation(position.x, position.y, size[2] / 2)})


def _on_storey(box: dict, elevation: float) -> bool:
    """Standing on this floor: based within the floor plate's thickness of it and rising well above it."""
    return (elevation - WALL_BASE_BELOW_FLOOR_STUDS <= box["min"][1] <= elevation + FLOOR_TOLERANCE_STUDS
            and box["max"][1] - elevation >= WALL_MIN_RISE_STUDS)


def walls(parts: list[dict], elevation: float) -> list[SceneNode]:
    return [_box(f"wall-{p['index']}", "wall", "Wall", "wall", p["aabb"]["min"], p["aabb"]["max"], elevation)
            for p in parts if p["shape"] == "wall" and _on_storey(p["aabb"], elevation)]


def true_floor(parts: list[dict], storey: dict) -> float:
    """The top of the storey's largest floor plate, which a raised counter slab never is."""
    plates = [parts_by_index[i] for parts_by_index in [{p["index"]: p for p in parts}]
              for i in storey["floor_member_indices"]]
    widest = max(plates, key=lambda p: (p["aabb"]["max"][0] - p["aabb"]["min"][0])
                 * (p["aabb"]["max"][2] - p["aabb"]["min"][2]))
    return min(widest["aabb"]["max"][1], storey["elevation"])


def counter_tops(parts: list[dict], floor_y: float) -> list[tuple]:
    """Raised flat plates at counter height and much longer than deep, as (min, max) stud corners."""
    found = []
    for part in parts:
        lo, hi = part["aabb"]["min"], part["aabb"]["max"]
        spans = sorted((hi[0] - lo[0], hi[2] - lo[2]))
        top = hi[1] - floor_y
        if part["shape"] == "floor" and COUNTER_TOP_STUDS[0] <= top <= COUNTER_TOP_STUDS[1] \
                and spans[1] >= COUNTER_MIN_ASPECT * spans[0]:
            found.append((lo, hi))
    return found


def counters(tops: list[tuple], floor_y: float) -> list[SceneNode]:
    return [_box(f"counter-{index}", "object", "Ordering counter", "counter", (lo[0], floor_y, lo[2]), hi, floor_y)
            for index, (lo, hi) in enumerate(tops)]


def floor(storey: dict, floor_y: float) -> SceneNode:
    lo, hi = storey["extent_xz"]["min"], storey["extent_xz"]["max"]
    elevation = floor_y
    node = _box("floor", "floor", "Floor", "floor", (lo[0], elevation - 0.02, lo[1]), (hi[0], elevation, hi[1]),
                elevation - 0.02)
    return node.model_copy(update={"transform": Mat4.translation(node.transform.position.x,
                                                                   node.transform.position.y, 0.0)})


def _on_counter(obj: dict, counter_tops: list) -> bool:
    x = (obj["aabb"]["min"][0] + obj["aabb"]["max"][0]) / 2
    z = (obj["aabb"]["min"][2] + obj["aabb"]["max"][2]) / 2
    return any(lo[0] <= x <= hi[0] and lo[2] <= z <= hi[2] for lo, hi in counter_tops)


def _label(obj: dict, elevation: float, counter_tops: list) -> tuple[str, str, tuple | None]:
    names = " ".join(obj["meaningful_names"]).casefold()
    top = obj["aabb"]["max"][1] - elevation
    small = max(obj["footprint_xz"]) <= BOOTH_FOOTPRINT_MAX_STUDS
    raised = obj["aabb"]["min"][1] - elevation >= ON_A_COUNTER_STUDS
    if small and raised and _on_counter(obj, counter_tops):
        return ("Receipt printer", "storage", None) if "printer" in names else ("Cash register", "storage", None)
    if raised:
        return "Fixture", "storage", None
    if "toilet" in names:
        return "Toilet", "toilet", STANDARD_TOILET
    if "faucet" in names:
        return "Lavatory", "sink", STANDARD_LAVATORY
    if small and BOOTH_BACK_STUDS[0] <= top <= BOOTH_BACK_STUDS[1]:
        return "Bench", "bench", None
    if small and TABLE_TOP_STUDS[0] <= top <= TABLE_TOP_STUDS[1]:
        return "Table", "table", None
    return "Fixture", "storage", None


def pieces(objects: list[dict], storey_index: int, elevation: float, counter_tops: list) -> list[SceneNode]:
    """Every object cluster as a fixed piece, except a counter's own body, which `counters` already stands for."""
    made = []
    for obj in objects:
        if obj["storey"] != storey_index:
            continue
        if _on_counter(obj, counter_tops) and obj["aabb"]["min"][1] - elevation < ON_A_COUNTER_STUDS:
            continue
        label, category, size = _label(obj, elevation, counter_tops)
        node = _box(f"object-{obj['id']}", "object", label, category, obj["aabb"]["min"], obj["aabb"]["max"],
                    elevation)
        made.append(_sized(node, size) if size else node)
    return made


def is_door_leaf(node: SceneNode) -> bool:
    """A thin wall part standing on the floor at door height: a door leaf, which the extractor reads as wall."""
    base = node.transform.position.z - node.dimensions.z / 2
    thickness = min(node.dimensions.x, node.dimensions.y)
    return base <= DOOR_BASE_MAX_METERS and DOOR_LEAF_HEIGHT[0] <= node.dimensions.z <= DOOR_LEAF_HEIGHT[1] \
        and thickness <= DOOR_LEAF_MAX_THICKNESS


def _span(node: SceneNode) -> tuple[bool, float, float, float]:
    """(runs along y, fixed coordinate, start, end) of a wall part's long side."""
    x, y = node.transform.position.x, node.transform.position.y
    if node.dimensions.y >= node.dimensions.x:
        return True, round(x, 1), y - node.dimensions.y / 2, y + node.dimensions.y / 2
    return False, round(y, 1), x - node.dimensions.x / 2, x + node.dimensions.x / 2


def door_openings(leaves: list[SceneNode]) -> list[tuple[bool, float, float, float]]:
    """Contiguous runs of door leaves along one line, each one doorway."""
    openings: list[list] = []
    for along_y, line, start, end in sorted(_span(leaf) for leaf in leaves):
        last = openings[-1] if openings else None
        if last and last[0] == along_y and last[1] == line and start <= last[3] + LEAF_JOIN_METERS:
            last[3] = max(last[3], end)
        else:
            openings.append([along_y, line, start, end])
    return [tuple(opening) for opening in openings]


def _door(opening, label: str, index: int) -> SceneNode:
    along_y, line, start, end = opening
    middle = (start + end) / 2
    x, y = (line, middle) if along_y else (middle, line)
    c, s = (0.0, 1.0) if along_y else (1.0, 0.0)
    return SceneNode(id=uuid.uuid5(NAMESPACE, f"door-{index}"), kind="door", label=label, raw_category="door",
                     dimensions=Vec3(x=end - start, y=0.1, z=DOOR_HEIGHT_METERS),
                     transform=Mat4(m=[c, -s, 0, x, s, c, 0, y, 0, 0, 1, DOOR_HEIGHT_METERS / 2, 0, 0, 0, 1]),
                     movable=False)


def _to_edge(opening, floor_node: SceneNode) -> float:
    along_y, line = opening[0], opening[1]
    centre, half = (floor_node.transform.position.x, floor_node.dimensions.x / 2) if along_y else \
        (floor_node.transform.position.y, floor_node.dimensions.y / 2)
    return abs(abs(line - centre) - half)


def doors(leaves: list[SceneNode], floor_node: SceneNode) -> list[SceneNode]:
    """Every doorway as a door; the one on the outer wall is the front door."""
    wide = [opening for opening in door_openings(leaves) if opening[3] - opening[2] >= DOORWAY_MIN_METERS]
    openings = sorted(wide, key=lambda opening: _to_edge(opening, floor_node))
    return [_door(opening, "Front door" if index == 0 else "Door", index) for index, opening in enumerate(openings)]


def room(parts_file: pathlib.Path, objects_file: pathlib.Path, name: str, storey_index: int) -> SceneGraph:
    parts = json.loads(parts_file.read_text())
    storey = parts["storeys"][storey_index]
    elevation = true_floor(parts["parts"], storey)
    floor_node = floor(storey, elevation)
    standing = walls(parts["parts"], elevation)
    wall_nodes = [node for node in standing if not is_door_leaf(node)]
    door_nodes = doors([node for node in standing if is_door_leaf(node)], floor_node)
    objects = json.loads(objects_file.read_text())["objects"]
    tops = counter_tops(parts["parts"], elevation)
    nodes = [floor_node, *wall_nodes, *door_nodes, *counters(tops, elevation),
             *pieces(objects, storey_index, elevation, tops)]
    return SceneGraph(scan_id=uuid.uuid5(NAMESPACE, name), nodes=nodes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts", type=pathlib.Path, required=True)
    parser.add_argument("--objects", type=pathlib.Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--storey", type=int, default=0)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    graph = room(args.parts, args.objects, args.name, args.storey)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"name": args.name, "source": "roblox obj", "metres_per_stud": METRES_PER_STUD,
                                    "latest": graph.model_dump(mode="json")}))
    counts: dict[str, int] = {}
    for node in graph.nodes:
        counts[node.label] = counts.get(node.label, 0) + 1
    extent = next(n for n in graph.nodes if n.kind == "floor").dimensions
    print(f"{args.out}: {extent.x:.1f} x {extent.y:.1f} m, {json.dumps(counts)}")
    print(f"diagonal {math.hypot(extent.x, extent.y):.1f} m")


if __name__ == "__main__":
    main()
