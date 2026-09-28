"""Replay preserved Moffett photos through discovery and publish new floor labels.

The imported A-102 floor predates evidence uploads. This script reads its four
preserved captures without pretending they were uploaded to the overlay scan.
Cached frame detections make retries safe and avoid repeat model requests.
"""

from __future__ import annotations

import argparse
import json
import shutil

import numpy as np
from align_moffett_floor import (
    DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M,
    load_room_transforms,
)
from bake_library_textures import DATA_DIR, DATASETS_DIR, ROOMS, SCAN_ID, TRANSFORMS_PATH, room_capture
from standardphysics_contracts import Mat4, SceneGraph, Vec3, graph_hash
from standardphysics_pipeline.discovery import DiscoveryInputs, discover_objects

from standardphysics_api import repository as repo
from standardphysics_api.db import Database
from standardphysics_api.settings import Settings
from standardphysics_api.worker import ASSESS

RESULTS = DATA_DIR / "scans" / str(SCAN_ID) / "enhancements"


def enhanced_room(name: str, capture_id: str, max_photos: int, transform) -> tuple[SceneGraph, dict]:
    room = room_capture(name, capture_id, max_photos, transform, use_enhancements=False)
    if room.graph is None:
        raise RuntimeError(f"{name} has no measured room graph")
    directory = DATASETS_DIR / capture_id
    inputs = DiscoveryInputs(
        graph=room.graph,
        poses_path=room.poses_path,
        frame_paths=room.frame_paths,
        lidar_mesh_path=room.mesh_path,
        cache_dir=directory / "detections",
        crop_dir=directory / "crops",
    )
    result = discover_objects(inputs)
    by_id = {node.id: node for node in room.graph.nodes}
    for node in result.nodes:
        by_id[node.id] = node
    graph = room.graph.model_copy(update={"nodes": list(by_id.values())})
    report = {
        "name": name,
        "frames_read": result.frames_read,
        "detections_failed": len(result.failures),
        "new_nodes": len(by_id) - len(room.graph.nodes),
        "people_points_removed": result.people_points_removed,
        "model_requests": len(result.model_requests),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}-graph.json").write_text(graph.model_dump_json())
    (RESULTS / f"{name}-discovery.json").write_text(json.dumps(report, indent=2) + "\n")
    return graph, report


def floor_point(point: Vec3, matrix: np.ndarray) -> Vec3:
    moved = matrix[:3, :3] @ np.array([point.x, point.y, point.z]) + matrix[:3, 3]
    return Vec3(x=float(moved[0]), y=float(moved[1]), z=float(moved[2]))


def floor_direction(direction: Vec3, matrix: np.ndarray) -> Vec3:
    moved = matrix[:3, :3] @ np.array([direction.x, direction.y, direction.z])
    return Vec3(x=float(moved[0]), y=float(moved[1]), z=float(moved[2]))


def floor_node(node, transform, floor_correction, room_name: str, available_ids: set):
    room_to_floor = np.asarray(transform, dtype=np.float64).reshape(4, 4).copy()
    room_to_floor[2, 3] += floor_correction
    matrix = room_to_floor @ np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    attachment = node.attachment
    if attachment is not None:
        observations = [
            observation.model_copy(update={
                "image_url": f"{room_name}-{observation.image_url}" if observation.image_url else None,
            })
            for observation in attachment.observations
        ]
        changes = {
            "observations": observations,
            "observed_region": [floor_point(point, room_to_floor) for point in attachment.observed_region],
            "normal": floor_direction(attachment.normal, room_to_floor) if attachment.normal is not None else None,
        }
        if attachment.support_node_id is not None and attachment.support_node_id not in available_ids:
            changes.update({
                "support_node_id": None, "support_type": "unanchored", "local_anchor": None,
                "localization_quality": "needs_verification",
                "uncertainty_reasons": [*attachment.uncertainty_reasons, "support surface is absent from the combined floor"],
            })
        attachment = attachment.model_copy(update=changes)
    parent_id = node.parent_id if node.parent_id in available_ids else None
    return node.model_copy(update={
        "transform": Mat4(m=matrix.reshape(-1).tolist()), "attachment": attachment,
        "parent_id": parent_id, "relation": node.relation if parent_id is not None else None,
    })


def copy_room_crops() -> None:
    destination = RESULTS.parent / "crops"
    destination.mkdir(parents=True, exist_ok=True)
    for name, (capture_id, _) in ROOMS.items():
        for crop in (DATASETS_DIR / capture_id / "crops").glob("*.jpg"):
            shutil.copyfile(crop, destination / f"{name}-{crop.name}")


def enhanced_floor_graph(head: SceneGraph, room_graphs: dict[str, SceneGraph], transforms: dict) -> tuple[SceneGraph, dict]:
    nodes = list(head.nodes)
    existing = {node.id: index for index, node in enumerate(nodes)}
    seen = set(existing)
    available = seen | {
        node.id for graph in room_graphs.values() for node in graph.nodes
        if node.labeled_by == "discovery" or node.attachment is not None
    }
    additions = []
    relabelled = 0
    for name, graph in room_graphs.items():
        transform_name = "bottom" if name == "bottom_left" else name
        correction = DEFAULT_FLOOR_ORIGIN_CORRECTIONS_M[transform_name]
        for node in graph.nodes:
            index = existing.get(node.id)
            if index is not None:
                floor = nodes[index]
                if floor.labeled_by == "owner":
                    continue
                if node.labeled_by == "discovery" or floor.labeled_by == "discovery":
                    if (floor.label, floor.labeled_by) != (node.label, node.labeled_by):
                        nodes[index] = floor.model_copy(update={"label": node.label, "labeled_by": node.labeled_by})
                        relabelled += 1
            elif node.id not in seen and node.id in available:
                additions.append(floor_node(node, transforms[transform_name], correction, name, available))
                seen.add(node.id)
    report = {"new_nodes": len(additions), "relabelled_nodes": relabelled}
    if not additions and not relabelled:
        return head, report
    updated = head.model_copy(update={
        "revision": head.revision + 1,
        "base_hash": graph_hash(head),
        "nodes": [*nodes, *additions],
    })
    return SceneGraph.model_validate(updated.model_dump()), report


def publish_discovered_nodes(database: Database, room_graphs: dict[str, SceneGraph], transforms: dict) -> dict:
    copy_room_crops()
    with database.transaction() as connection:
        head = repo.graph_of(repo.get_revision(connection, SCAN_ID))
        updated, report = enhanced_floor_graph(head, room_graphs, transforms)
        if updated.revision == head.revision:
            return {"revision": head.revision, **report}
        repo.save_revision(connection, updated, source="discovery", base_revision=head.revision)
        repo.enqueue_job(connection, SCAN_ID, ASSESS, updated.revision)
    return {"revision": updated.revision, **report}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detect-only", action="store_true")
    parser.add_argument("--room", choices=tuple(ROOMS))
    parser.add_argument("--publish-only", action="store_true")
    args = parser.parse_args()
    Settings.from_environment()
    transforms, _ = load_room_transforms(TRANSFORMS_PATH)
    graphs, reports = {}, []
    for name, (capture_id, photos) in ROOMS.items():
        if args.room is not None and args.room != name:
            continue
        if args.publish_only:
            graph = SceneGraph.model_validate_json((RESULTS / f"{name}-graph.json").read_text())
            report = json.loads((RESULTS / f"{name}-discovery.json").read_text())
        else:
            transform_name = "bottom" if name == "bottom_left" else name
            graph, report = enhanced_room(name, capture_id, photos, transforms[transform_name])
        graphs[name] = graph
        reports.append(report)
        print(json.dumps(report), flush=True)
    publication = None if args.detect_only or args.room is not None else publish_discovered_nodes(Database(DATA_DIR / "standardphysics.sqlite3"), graphs, transforms)
    (RESULTS / "recognition.json").write_text(json.dumps({"rooms": reports, "publication": publication}, indent=2) + "\n")
    print(json.dumps({"publication": publication}), flush=True)


if __name__ == "__main__":
    main()
