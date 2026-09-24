"""Fetch annotation-only ARKitScenes OBBs and convert them to evaluation-only graphs."""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import urllib.error
import urllib.request
import uuid

from multiroom_data import _append, _rows, _write_json
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3

SPLITS_URL = "https://raw.githubusercontent.com/apple/ARKitScenes/main/threedod/3dod_train_val_splits.csv"
ANNOTATIONS_URL = "https://docs-assets.developer.apple.com/ml-research/datasets/arkitscenes/v1/raw"
LICENSE_URL = "https://github.com/apple/ARKitScenes/blob/main/LICENSE"
NAMESPACE = uuid.UUID("fe470d45-0928-4466-b0cf-98f9b784d31b")
MAX_BYTES = 5_000_000_000
MAX_FILE_BYTES = 20_000_000
LABELS = {
    "chair": ("chair", True), "stool": ("chair", True), "table": ("table", True),
    "desk": ("table", True), "sofa": ("sofa", True), "couch": ("sofa", True),
    "tv_monitor": ("television", False), "shelf": ("storage", False),
    "cabinet": ("storage", False), "bed": ("bed", True),
}


def _download(url: str, destination: pathlib.Path, remaining: int) -> int:
    if destination.exists():
        return destination.stat().st_size
    request = urllib.request.Request(url, headers={"User-Agent": "standardphysics-annotation-reader/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        size = int(response.headers.get("Content-Length", "0"))
        if size > min(remaining, MAX_FILE_BYTES):
            raise ValueError(f"annotation exceeds size cap: {url}")
        data = response.read(min(remaining, MAX_FILE_BYTES) + 1)
    if len(data) > min(remaining, MAX_FILE_BYTES):
        raise ValueError(f"annotation exceeds size cap: {url}")
    destination.write_bytes(data)
    return len(data)


def _axes_transform(obb: dict, floor_z: float) -> Mat4:
    axes = obb["normalizedAxes"]
    x, y, z = obb["centroid"]
    return Mat4(m=[axes[0], axes[3], axes[6], x,
                   axes[1], axes[4], axes[7], y,
                   axes[2], axes[5], axes[8], z - floor_z,
                   0, 0, 0, 1])


def _objects(data: list[dict], scan_id: str, floor_z: float) -> list[SceneNode]:
    objects = []
    for index, annotation in enumerate(data):
        obb = annotation.get("segments", {}).get("obbAligned")
        if not obb or any(length <= 0 for length in obb["axesLengths"]):
            continue
        category, movable = LABELS.get(annotation["label"], ("other", False))
        dimensions = obb["axesLengths"]
        objects.append(SceneNode(id=uuid.uuid5(NAMESPACE, f"{scan_id}:{index}"), kind="object",
                                 label=annotation["label"].replace("_", " ").title(), raw_category=category,
                                 dimensions=Vec3(x=dimensions[0], y=dimensions[1], z=dimensions[2]),
                                 transform=_axes_transform(obb, floor_z), movable=movable))
    return objects


def convert(annotation: dict, video_id: str) -> SceneGraph | None:
    data = [item for item in annotation.get("data", []) if item.get("segments", {}).get("obbAligned")]
    if not data:
        return None
    floor_z = min(item["segments"]["obbAligned"]["centroid"][2]
                  - item["segments"]["obbAligned"]["axesLengths"][2] / 2 for item in data)
    objects = _objects(data, video_id, floor_z)
    if not objects:
        return None
    x_min = min(node.transform.position.x - node.dimensions.x / 2 for node in objects) - 1.5
    x_max = max(node.transform.position.x + node.dimensions.x / 2 for node in objects) + 1.5
    y_min = min(node.transform.position.y - node.dimensions.y / 2 for node in objects) - 1.5
    y_max = max(node.transform.position.y + node.dimensions.y / 2 for node in objects) + 1.5
    floor = SceneNode(id=uuid.uuid5(NAMESPACE, f"{video_id}:floor"), kind="floor", label="Inferred floor",
                      raw_category="floor", dimensions=Vec3(x=x_max - x_min, y=y_max - y_min, z=0.01),
                      transform=Mat4.translation((x_min + x_max) / 2, (y_min + y_max) / 2, 0))
    return SceneGraph(scan_id=uuid.uuid5(NAMESPACE, video_id), nodes=[floor, *objects])


def run(destination: pathlib.Path, count: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    splits = destination / "splits.csv"
    _download(SPLITS_URL, splits, MAX_BYTES)
    rows = list(csv.DictReader(splits.open()))[:count]
    output = destination / "graphs.jsonl"
    existing = {row["video_id"] for row in _rows(output)}
    failures = {row["video_id"] for row in _rows(destination / "failures.jsonl")}
    spent = sum(file.stat().st_size for file in destination.glob("*.json"))
    for row in rows:
        video_id, split = row["video_id"], row["fold"]
        if video_id in existing or video_id in failures:
            continue
        annotation_path = destination / f"{video_id}.json"
        url = f"{ANNOTATIONS_URL}/{split}/{video_id}/{video_id}_3dod_annotation.json"
        try:
            spent += _download(url, annotation_path, MAX_BYTES - spent)
            graph = convert(json.loads(annotation_path.read_text()), video_id)
            if graph is None:
                raise ValueError("no OBB annotations")
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            _append(destination / "failures.jsonl", {"video_id": video_id, "error": str(error)})
            failures.add(video_id)
            continue
        _append(output, {"video_id": video_id, "split": split, "graph": graph.model_dump(mode="json"),
                         "note": "Floor extent inferred from object bounds; walls and doors omitted. "
                                 "No route or compliance assessment is supported."})
        existing.add(video_id)
        _write_json(destination / "progress.json", {"converted": len(existing), "attempted": len(existing | failures),
                                                     "maximum": count, "annotation_bytes": spent,
                                                     "use": "evaluation only; not training"})
        print(f"ARKitScenes annotations {len(existing)} converted / {len(existing | failures)} attempted", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=pathlib.Path, required=True)
    parser.add_argument("--count", type=int, default=300)
    args = parser.parse_args()
    run(args.destination, args.count)


if __name__ == "__main__":
    main()
