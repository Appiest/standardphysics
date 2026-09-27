"""Stage pinned public source metadata for dataset review.

The output is source material and an inventory, not SFT or RL training rows.
Run ``.venv/bin/python scripts/stage_external_sources.py`` from the checkout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "datasets/external_sources.json"
OUTPUT = ROOT / "datasets/external"


def _download(repository: str, revision: str, remote_path: str, target: Path) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/datasets/{repository}/resolve/{revision}/{quote(remote_path)}"
    temporary = target.with_name(target.name + ".partial")
    request = Request(url, headers={"User-Agent": "standardphysics-dataset-review/1"})
    try:
        with urlopen(request, timeout=90) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _marketgen_summary(paths: list[Path]) -> dict:
    shelves = 0
    items = 0
    for path in paths:
        scene = json.loads(path.read_text())
        if scene.get("ExportConfig", {}).get("Unit") != "cm":
            raise ValueError(f"MarketGen units are not centimeters: {path}")
        if not scene.get("Framework") or not scene.get("ShelfBoxes"):
            raise ValueError(f"MarketGen scene has no framework or shelves: {path}")
        shelves += sum(len(box["Shelves"]) for box in scene["ShelfBoxes"])
        items += len(scene.get("Items", []))
    return {"scenes": len(paths), "shelves": shelves, "items": items}


def _imaginarium_summary(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"id", "bbx", "class_en", "source", "license"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("Imaginarium asset metadata is missing required columns")
        licenses = Counter(row["license"] or "unspecified" for row in reader)
    return {"assets": sum(licenses.values()), "asset_licenses": dict(sorted(licenses.items()))}


def _inventory(source: dict, directory: Path) -> dict:
    paths = []
    for remote_path in source["files"]:
        target = directory / remote_path
        _download(source["repository"], source["revision"], remote_path, target)
        payload = target.read_bytes()
        paths.append({"path": remote_path, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    local_paths = [directory / entry["path"] for entry in paths]
    summary = (_marketgen_summary(local_paths) if source["id"] == "marketgen"
               else _imaginarium_summary(local_paths[0]))
    return {
        "repository": source["repository"],
        "revision": source["revision"],
        "license": source["license"],
        "training_status": source["training_status"],
        "training_ready": source["training_ready"],
        "summary": summary,
        "files": paths,
    }


def main() -> None:
    sources = {source["id"]: source for source in json.loads(CATALOG.read_text())["sources"]}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["all", *sources], default="all")
    args = parser.parse_args()
    selected = sources.values() if args.source == "all" else [sources[args.source]]
    for source in selected:
        directory = OUTPUT / source["id"] / source["revision"]
        inventory = _inventory(source, directory)
        output = directory / "inventory.json"
        output.write_text(json.dumps(inventory, indent=2) + "\n")
        print(f"{source['id']}: {inventory['summary']} ({output})")


if __name__ == "__main__":
    main()
