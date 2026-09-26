"""Asking each object only about the vertices around its box gives the answers testing the whole scan gave.

The per-object steps used to test every vertex of the scan against every
labelled object, which on a merged library floor cost more than ten minutes of
a small server's time. The index is only allowed to be faster, never
different, so each step is compared with the whole-scan version it replaced,
kept here as the reference, on a room really scanned on this machine.
"""

from __future__ import annotations

import json
import pathlib
import time

import numpy as np
import pytest
from scipy.spatial import cKDTree
from standardphysics_contracts import bounds_the_room
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.textures import surface_materials, symmetry
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.project import depth_buffer
from standardphysics_pipeline.textures.regions import VertexIndex
from standardphysics_pipeline.textures.scan_colour import scan_geometry, vertex_normals

ROOT = pathlib.Path(__file__).resolve().parents[3]
CAMERAS_USED = 40


def _smallest_scan():
    found = []
    for artifacts in sorted(ROOT.glob("services/api/var/scans/*/artifacts")):
        mesh, poses, room = artifacts / "lidar-mesh", artifacts / "poses", artifacts / "room-json"
        if mesh.is_file() and poses.is_file() and room.is_file() and any(artifacts.glob("frame-*")):
            found.append((mesh.stat().st_size, artifacts))
    return min(found)[1] if found else None


@pytest.fixture(scope="module")
def scan():
    artifacts = _smallest_scan()
    if artifacts is None:
        pytest.skip("no scanned room with photos on this machine")
    graph = parse_room_json(json.loads((artifacts / "room-json").read_text()))
    vertices, triangles = scan_geometry(artifacts / "lidar-mesh", graph.capture_to_room)
    frames = [path.name for path in sorted(artifacts.glob("frame-*"))]
    cameras = load_cameras(artifacts / "poses", frames, graph.capture_to_room)
    cameras = cameras[:: max(1, len(cameras) // CAMERAS_USED)]
    seen_through = symmetry.seen_through_by(cameras, [depth_buffer(camera, vertices) for camera in cameras])
    return vertices, triangles, graph, seen_through


def _whole_scan_owners(vertices, normals, graph, patches=None):
    owners = np.full(len(vertices), -1, dtype=np.int32)
    claimable = ~patches if patches is not None else np.ones(len(vertices), dtype=bool)
    nodes = list(enumerate(graph.nodes))
    for index, node in [(i, n) for i, n in nodes if not surface_materials._is_room_sheet(n)]:
        owners[surface_materials._inside(node, vertices, surface_materials.OBJECT_REACH) & (owners < 0) & claimable] = index
    for index, node in [(i, n) for i, n in nodes if surface_materials._is_room_sheet(n)]:
        owners[surface_materials._on_sheet(node, vertices, normals) & (owners < 0)] = index
    return owners


def _whole_scan_planes(index, node, vertices, seen_through):
    rotation, centre, half = symmetry._frame(node)
    local = (vertices - centre) @ rotation
    own = local[np.all(np.abs(local) <= half + symmetry.OBJECT_REACH, axis=1) & (vertices[:, 2] > symmetry.ABOVE_FLOOR)]
    if len(own) < symmetry.MIN_POINTS:
        return []
    tree = cKDTree(own)
    planes = []
    for axis in symmetry._upright_planes(rotation):
        best = None
        for offset in symmetry.OFFSETS * min(1.0, half[axis] / 0.3):
            agreement, contradiction = symmetry._score(
                own, tree, lambda points: points @ rotation.T + centre, seen_through, axis, float(offset),
            )
            margin = agreement - contradiction
            if agreement >= symmetry.MIN_AGREEMENT and margin >= symmetry.MIN_MARGIN and (best is None or margin > best.margin):
                best = symmetry.MirrorPlane(index, axis, float(offset), margin)
        if best is not None:
            planes.append(best)
    return sorted(planes, key=lambda plane: -plane.margin)


def _whole_scan_reflection(plane, node, vertices, triangles, scanned, seen_through):
    rotation, centre, half = symmetry._frame(node)
    local = (vertices - centre) @ rotation
    inside = np.all(np.abs(local) <= half + symmetry.OBJECT_REACH, axis=1)
    own = triangles[inside[triangles].all(axis=1)]
    if not len(own):
        return np.empty((0, 3)), np.empty((0, 3), dtype=np.int64), np.empty(0, dtype=np.int64)
    used = np.unique(own)
    mirrored = symmetry._reflected(local[used], plane.axis, plane.offset) @ rotation.T + centre
    missing = (scanned.query(mirrored)[0] > symmetry.MISSING_DISTANCE) & ~seen_through(mirrored)
    within = np.all(np.abs(symmetry._reflected(local[used], plane.axis, plane.offset)) <= half + symmetry.OBJECT_REACH, axis=1)
    position = np.full(len(vertices), -1, dtype=np.int64)
    position[used] = np.arange(len(used))
    corners = position[own]
    keep = (missing & within)[corners].all(axis=1)
    return mirrored, corners[keep][:, ::-1], used


def _whole_scan_completion(vertices, triangles, graph, seen_through):
    new_vertices, new_triangles, sources, planes = [], [], [], []
    next_index = len(vertices)
    for index, node in enumerate(graph.nodes):
        if bounds_the_room(node):
            continue
        for plane in _whole_scan_planes(index, node, vertices, seen_through):
            present = cKDTree(np.concatenate([vertices, *new_vertices]))
            mirrored, faces, used = _whole_scan_reflection(plane, node, vertices, triangles, present, seen_through)
            if not len(faces):
                continue
            kept = np.unique(faces)
            remap = np.full(len(mirrored), -1, dtype=np.int64)
            remap[kept] = next_index + np.arange(len(kept))
            new_vertices.append(mirrored[kept])
            new_triangles.append(remap[faces])
            sources.append(used[kept])
            next_index += len(kept)
            planes.append(plane)
    return (
        np.concatenate([vertices, *new_vertices]),
        np.concatenate([triangles, *new_triangles]).astype(np.int64),
        np.concatenate([np.full(len(vertices), -1, dtype=np.int64), *sources]),
        planes,
    )


def test_every_vertex_inside_a_node_is_among_its_candidates(scan):
    vertices, _, graph, _ = scan
    regions = VertexIndex(vertices)
    for node in graph.nodes:
        for reach in (surface_materials.OBJECT_REACH, surface_materials.SURFACE_REACH):
            truly_inside = np.flatnonzero(surface_materials._inside(node, vertices, reach))
            assert np.isin(truly_inside, regions.near_box(node, reach)).all(), node.id


def test_the_index_leaves_most_of_the_scan_out_for_an_object(scan):
    """An index that handed back every vertex would pass the comparisons and save nothing."""
    vertices, _, graph, _ = scan
    regions = VertexIndex(vertices)
    objects = [node for node in graph.nodes if not bounds_the_room(node)]
    assert objects
    shares = [len(regions.near_box(node, symmetry.OBJECT_REACH)) / len(vertices) for node in objects]
    assert np.mean(shares) < 0.5


def test_room_owners_matches_testing_the_whole_scan_against_every_node(scan):
    vertices, triangles, graph, _ = scan
    normals = vertex_normals(vertices, triangles)
    started = time.monotonic()
    reference = _whole_scan_owners(vertices, normals, graph)
    whole_scan_seconds = time.monotonic() - started
    started = time.monotonic()
    indexed = surface_materials.room_owners(vertices, normals, graph)
    print(f"room owners: whole scan {whole_scan_seconds:.2f} s, indexed {time.monotonic() - started:.2f} s")
    assert (reference >= 0).any()
    assert np.array_equal(indexed, reference)


def test_mirrored_completion_matches_testing_the_whole_scan_against_every_object(scan):
    vertices, triangles, graph, seen_through = scan
    started = time.monotonic()
    reference_vertices, reference_triangles, reference_source, reference_planes = _whole_scan_completion(
        vertices, triangles, graph, seen_through,
    )
    whole_scan_seconds = time.monotonic() - started
    started = time.monotonic()
    completed = symmetry.mirrored_completion(vertices, triangles, graph, seen_through)
    print(f"mirrored completion: whole scan {whole_scan_seconds:.2f} s, indexed {time.monotonic() - started:.2f} s")
    assert completed.planes == reference_planes
    assert np.array_equal(completed.vertices, reference_vertices)
    assert np.array_equal(completed.triangles, reference_triangles)
    assert np.array_equal(completed.source, reference_source)
    assert np.array_equal(completed.added, np.arange(len(reference_vertices)) >= len(vertices))
