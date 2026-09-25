"""Scans as compact JSON, read from the API's database without writing to it.

An export holds what a training room needs and nothing a person photographed:
the scene graph of the newest revision and of revision 0, and the scenario.
Display-only fields (`appearance`, `reconstruction`, surface `texts`, unplaced
photo observations) are dropped; the checker reads none of them. Meshes,
photos and videos never leave the database's machine.

A graph that names one node id twice cannot be loaded as a `SceneGraph` in any
useful way, so the export keeps the first node with each id and records every
dropped duplicate, with how far it sat from the node that was kept.
"""

from __future__ import annotations

import json
import math
import pathlib
import sqlite3
from dataclasses import dataclass, field

from standardphysics_contracts import Scenario, SceneGraph

DISPLAY_ONLY_NODE_FIELDS = ("appearance", "reconstruction", "texts")
DISPLAY_ONLY_GRAPH_FIELDS = ("unlocalized_observations",)


@dataclass
class ScanExport:
    scan_id: str
    name: str
    latest_revision: int
    latest: dict
    original: dict
    scenario: dict | None
    duplicates_dropped: list[dict] = field(default_factory=list)

    def graph(self) -> SceneGraph:
        return SceneGraph.model_validate(self.latest)

    def scenario_model(self) -> Scenario | None:
        return None if self.scenario is None else Scenario.model_validate(self.scenario)

    def as_dict(self) -> dict:
        return {
            "scan_id": self.scan_id, "name": self.name, "latest_revision": self.latest_revision,
            "latest": self.latest, "original": self.original, "scenario": self.scenario,
            "duplicates_dropped": self.duplicates_dropped,
        }


def read_only(database: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _position(node: dict) -> tuple[float, float]:
    matrix = node["transform"]["m"]
    return matrix[3], matrix[7]


def dedupe_nodes(graph: dict) -> tuple[dict, list[dict]]:
    """The graph with each node id kept once, first occurrence wins, and what was dropped."""
    kept: dict[str, dict] = {}
    dropped = []
    for node in graph["nodes"]:
        first = kept.get(node["id"])
        if first is None:
            kept[node["id"]] = node
            continue
        (x0, y0), (x1, y1) = _position(first), _position(node)
        dropped.append({"node_id": node["id"], "kind": node.get("kind"), "label": node.get("label"),
                        "metres_from_kept": round(math.hypot(x1 - x0, y1 - y0), 3)})
    return {**graph, "nodes": list(kept.values())}, dropped


def compact(graph: dict) -> dict:
    nodes = [{key: value for key, value in node.items() if key not in DISPLAY_ONLY_NODE_FIELDS}
             for node in graph["nodes"]]
    return {**{key: value for key, value in graph.items() if key not in DISPLAY_ONLY_GRAPH_FIELDS}, "nodes": nodes}


def _graph_json(connection: sqlite3.Connection, scan_id: str, revision: int) -> tuple[dict, list[dict]]:
    row = connection.execute(
        "SELECT graph_json FROM revisions WHERE scan_id = ? AND revision = ?", (scan_id, revision)
    ).fetchone()
    graph, dropped = dedupe_nodes(json.loads(row["graph_json"]))
    SceneGraph.model_validate(graph)
    return compact(graph), dropped


def export_scan(connection: sqlite3.Connection, name: str) -> ScanExport:
    scan = connection.execute("SELECT id FROM scans WHERE name = ?", (name,)).fetchone()
    if scan is None:
        raise KeyError(f"no scan named {name!r}")
    scan_id = scan["id"]
    latest_revision = connection.execute(
        "SELECT MAX(revision) AS revision FROM revisions WHERE scan_id = ?", (scan_id,)
    ).fetchone()["revision"]
    latest, dropped = _graph_json(connection, scan_id, latest_revision)
    original, _ = _graph_json(connection, scan_id, 0)
    scenario = connection.execute("SELECT scenario_json FROM scenarios WHERE scan_id = ?", (scan_id,)).fetchone()
    return ScanExport(
        scan_id=scan_id, name=name, latest_revision=latest_revision, latest=latest, original=original,
        scenario=None if scenario is None else json.loads(scenario["scenario_json"]), duplicates_dropped=dropped,
    )


def export_path(directory: pathlib.Path, scan_id: str) -> pathlib.Path:
    return directory / f"{scan_id}.json"


def write_export(directory: pathlib.Path, export: ScanExport) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = export_path(directory, export.scan_id)
    path.write_text(json.dumps(export.as_dict(), separators=(",", ":")))
    return path


def load_export(path: pathlib.Path) -> ScanExport:
    return ScanExport(**json.loads(path.read_text()))
