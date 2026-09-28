"""Choose distinct, useful rooms from ARKitScenes for training.

ARKitScenes (Apple, https://github.com/apple/ARKitScenes) has 5,047 captures of 1,661 rooms, all in homes.
Its commercial license covers companies under 700 million monthly users. Its 3D object detection
annotations are oriented boxes around furniture, with no walls or doors; a room shell comes later, from
each chosen capture's mesh.

    annotations   download every capture's box annotations (about 11 kB each)
    select        one capture per physical room (visit_id), a room type from its objects, then the most
                  mutually different rooms per type by farthest-point sampling, dropping near-duplicates

    python scripts/finetune/arkit_rooms.py annotations --data ~/sp-data/arkit
    python scripts/finetune/arkit_rooms.py select --data ~/sp-data/arkit --rooms 600
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import pathlib
import urllib.request
from collections import Counter

BASE = "https://docs-assets.developer.apple.com/ml-research/datasets/arkitscenes/v1"
SPLITS_URL = "https://raw.githubusercontent.com/apple/ARKitScenes/main/threedod/3dod_train_val_splits.csv"
LABELS = ("cabinet", "refrigerator", "shelf", "stove", "bed", "sink", "washer", "toilet", "bathtub", "oven",
          "dishwasher", "fireplace", "stool", "chair", "table", "tv_monitor", "sofa")
MOVABLE = frozenset({"stool", "chair", "table", "sofa"})
MIN_MOVABLE = 3
"""A room with fewer movable pieces gives a rearrangement nothing to work with."""
NEAR_DUPLICATE = 0.08
"""Feature distance under which two rooms count as the same layout."""
TYPE_SHARE = {"dining": 0.25, "living": 0.25, "study": 0.15, "kitchen": 0.15, "bathroom": 0.1, "bedroom": 0.1}
"""Target mix. Dining, living and study rooms are the closest a home gets to a cafe or an office; bathrooms
carry the restroom rules; bedrooms are kept few because they rarely resemble a public space."""


def _get(url: str, destination: pathlib.Path) -> bool:
    if destination.exists():
        return True
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "standardphysics-dataset/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            destination.write_bytes(response.read())
        return True
    except OSError:
        return False


def download_annotations(data: pathlib.Path, workers: int = 16) -> dict:
    annotations = data / "annotations"
    annotations.mkdir(parents=True, exist_ok=True)
    _get(SPLITS_URL, data / "splits.csv")
    rows = list(csv.DictReader((data / "splits.csv").open()))
    jobs = {row["video_id"]: f"{BASE}/raw/{row['fold']}/{row['video_id']}/{row['video_id']}_3dod_annotation.json"
            for row in rows}
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        done = list(pool.map(lambda item: _get(item[1], annotations / f"{item[0]}.json"), jobs.items()))
    return {"captures": len(rows), "downloaded": sum(done), "failed": len(done) - sum(done)}


def _boxes(path: pathlib.Path) -> list[dict]:
    data = json.loads(path.read_text()).get("data", [])
    return [item for item in data if item.get("segments", {}).get("obbAligned", {}).get("axesLengths")]


def describe(video_id: str, boxes: list[dict]) -> dict:
    labels = Counter(box["label"] for box in boxes)
    xs = [c for box in boxes for c in _extent(box, 0)]
    ys = [c for box in boxes for c in _extent(box, 1)]
    area = (max(xs) - min(xs)) * (max(ys) - min(ys)) if xs else 0.0
    return {"video_id": video_id, "labels": dict(labels), "objects": len(boxes), "area": round(area, 2),
            "movable": sum(count for label, count in labels.items() if label in MOVABLE),
            "type": room_type(labels)}


def _extent(box: dict, axis: int) -> tuple[float, float]:
    obb = box["segments"]["obbAligned"]
    reach = sum(abs(obb["normalizedAxes"][row * 3 + axis]) * obb["axesLengths"][row] / 2 for row in range(3))
    return obb["centroid"][axis] - reach, obb["centroid"][axis] + reach


def room_type(labels: Counter) -> str:
    if labels["toilet"] or labels["bathtub"]:
        return "bathroom"
    if labels["bed"]:
        return "bedroom"
    if labels["stove"] or labels["oven"] or labels["dishwasher"]:
        return "kitchen"
    if labels["table"] and labels["chair"] + labels["stool"] >= 3:
        return "dining"
    if labels["sofa"] or labels["tv_monitor"] or labels["fireplace"]:
        return "living"
    return "study" if labels["table"] or labels["shelf"] or labels["cabinet"] else "other"


def features(room: dict) -> list[float]:
    """Furniture mix as shares, plus size and clutter on log scales, so rooms compare on layout, not scale."""
    total = max(1, room["objects"])
    mix = [room["labels"].get(label, 0) / total for label in LABELS]
    return [*mix, math.log1p(room["area"]) / 4.0, math.log1p(room["objects"]) / 4.0]


def _distance(a: list[float], b: list[float]) -> float:
    return math.dist(a, b)


def farthest_first(rooms: list[dict], count: int) -> list[dict]:
    """Greedy farthest-point sampling: each pick is the room least like everything already picked."""
    if not rooms:
        return []
    vectors = [features(room) for room in rooms]
    picked = [max(range(len(rooms)), key=lambda i: rooms[i]["objects"])]
    nearest = [_distance(vectors[i], vectors[picked[0]]) for i in range(len(rooms))]
    while len(picked) < min(count, len(rooms)):
        choice = max(range(len(rooms)), key=lambda i: nearest[i])
        if nearest[choice] < NEAR_DUPLICATE:
            break
        picked.append(choice)
        nearest = [min(nearest[i], _distance(vectors[i], vectors[choice])) for i in range(len(rooms))]
    return [rooms[i] for i in picked]


def one_per_room(data: pathlib.Path) -> list[dict]:
    """The capture with the most annotated objects from each visit; a capture with no visit id stands alone."""
    visits = {row["video_id"]: row["visit_id"] for row in csv.DictReader((data / "splits.csv").open())}
    best: dict[str, dict] = {}
    for path in sorted((data / "annotations").glob("*.json")):
        boxes = _boxes(path)
        if not boxes:
            continue
        room = describe(path.stem, boxes)
        visit = visits.get(path.stem, "NA")
        key = path.stem if visit in ("", "NA") else f"visit-{visit}"
        room["room_key"] = key
        if key not in best or room["objects"] > best[key]["objects"]:
            best[key] = room
    return list(best.values())


def select(data: pathlib.Path, count: int) -> dict:
    rooms = [room for room in one_per_room(data) if room["movable"] >= MIN_MOVABLE]
    chosen = []
    for kind, share in TYPE_SHARE.items():
        chosen += farthest_first([room for room in rooms if room["type"] == kind], round(count * share))
    (data / "selected.jsonl").write_text("".join(json.dumps(room) + "\n" for room in chosen))
    report = {"unique_rooms_with_enough_furniture": len(rooms),
              "by_type_available": dict(Counter(room["type"] for room in rooms)),
              "by_type_chosen": dict(Counter(room["type"] for room in chosen)), "chosen": len(chosen)}
    (data / "selection_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("annotations", "select"))
    parser.add_argument("--data", type=pathlib.Path, required=True)
    parser.add_argument("--rooms", type=int, default=600)
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    result = download_annotations(args.data) if args.stage == "annotations" else select(args.data, args.rooms)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
