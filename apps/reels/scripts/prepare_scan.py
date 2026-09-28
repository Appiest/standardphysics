"""Turn a phone capture in datasets/phone into what the reels read: a merged LiDAR mesh and a floor plan."""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "public" / "scan"


def column_major(values):
    return np.array(values, dtype=np.float64).reshape(4, 4).T


def merged_mesh(capture: Path):
    mesh = json.loads((capture / "lidar-mesh.json").read_text())
    positions, indices, offset = [], [], 0
    for part in mesh["parts"]:
        local = np.array(part["vertices"], dtype=np.float64).reshape(-1, 3)
        world = (column_major(part["transform"]) @ np.c_[local, np.ones(len(local))].T).T[:, :3]
        positions.append(world)
        indices.append(np.array(part["triangles"], dtype=np.uint32) + offset)
        offset += len(local)
    return np.concatenate(positions).astype(np.float32), np.concatenate(indices), float(mesh["floorY"])


def footprint(surface):
    matrix = column_major(surface["transform"])
    width, _, depth = surface["dimensions"]
    corners = [(-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2)]
    return [list(map(float, (matrix @ np.array([x, 0, z, 1]))[[0, 2]])) for x, z in corners]


def wall_segment(wall):
    matrix = column_major(wall["transform"])
    half = wall["dimensions"][0] / 2
    return [list(map(float, (matrix @ np.array([x, 0, 0, 1]))[[0, 2]])) for x in (-half, half)]


def category_of(surface):
    return next(iter(surface["category"]))


def walked_path(capture: Path):
    poses = json.loads((capture / "poses.json").read_text())
    return [[float(p["transform"][12]), float(p["transform"][14])] for p in poses]


def floor_plan(capture: Path):
    room = json.loads((capture / "room.json").read_text())
    return {
        "walls": [wall_segment(wall) for wall in room["walls"]],
        "windows": [wall_segment(window) for window in room["windows"]],
        "doors": [wall_segment(door) for door in room["doors"]],
        "objects": [
            {"name": category_of(obj), "footprint": footprint(obj), "size": [float(v) for v in obj["dimensions"]], "center": [float(obj["transform"][12]), float(obj["transform"][13]), float(obj["transform"][14])]}
            for obj in room["objects"]
        ],
        "path": walked_path(capture),
    }


def preview(plan, target: Path):
    points = np.array([p for wall in plan["walls"] for p in wall] + plan["path"])
    low, high = points.min(0) - 0.5, points.max(0) + 0.5
    scale = 900 / (high - low).max()
    to_px = lambda p: tuple(((np.array(p) - low) * scale).tolist())
    image = Image.new("RGB", (1000, 1000), "#ecebe6")
    draw = ImageDraw.Draw(image)
    for wall in plan["walls"]:
        draw.line([to_px(p) for p in wall], fill="#121212", width=6)
    for obj in plan["objects"]:
        draw.polygon([to_px(p) for p in obj["footprint"]], outline="#56554f", width=2)
    draw.line([to_px(p) for p in plan["path"]], fill="#f6be1a", width=3)
    image.save(target)


def main(name: str):
    capture = REPO / "datasets" / "phone" / name
    target = OUT / name
    target.mkdir(parents=True, exist_ok=True)
    positions, indices, floor_y = merged_mesh(capture)
    positions.tofile(target / "positions.bin")
    indices.tofile(target / "indices.bin")
    plan = floor_plan(capture) | {"floorY": floor_y, "vertexCount": len(positions), "indexCount": len(indices)}
    (target / "plan.json").write_text(json.dumps(plan))
    preview(plan, target / "preview.png")
    print(name, len(positions), "vertices", len(indices) // 3, "triangles", positions.min(0), positions.max(0))


if __name__ == "__main__":
    for capture_name in sys.argv[1:]:
        main(capture_name)
