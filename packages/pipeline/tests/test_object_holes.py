"""Taking people out of the displayed scan and closing the holes they leave in furniture."""

from __future__ import annotations

import uuid

import numpy as np
from PIL import Image
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.discovery.cache import DetectionCache
from standardphysics_pipeline.discovery.detect import Detection
from standardphysics_pipeline.discovery.detector_transport import DEFAULT_MODEL, MODEL_ENV
from standardphysics_pipeline.discovery.discover import detections_digest, known_detections
from standardphysics_pipeline.discovery.people import mostly_people
from standardphysics_pipeline.textures.camera import PhotoCamera
from standardphysics_pipeline.textures.object_holes import closed_object_holes, people_masks, without_vertices
from standardphysics_pipeline.textures.scan_colour import vertex_normals


def chair_at(x: float, y: float, size: float = 0.6) -> SceneNode:
    return SceneNode(
        id=uuid.uuid4(), kind="object", label="Chair", raw_category="chair",
        dimensions=Vec3(x=size, y=size, z=0.9), transform=Mat4.translation(x, y, 0.45),
    )


def graph_with(*nodes: SceneNode) -> SceneGraph:
    return SceneGraph(scan_id=uuid.uuid4(), nodes=list(nodes))


def seat_with_a_hole(hole_half: float = 0.1, size: float = 1.0, spacing: float = 0.05, z: float = 0.45):
    """A flat square facing up at seat height, with a square hole in its middle."""
    steps = int(round(size / spacing))
    xs = np.linspace(-size / 2, size / 2, steps + 1)
    grid_x, grid_y = np.meshgrid(xs, xs)
    vertices = np.stack([grid_x.ravel(), grid_y.ravel(), np.full(grid_x.size, z)], axis=1)
    row = steps + 1
    triangles = []
    for j in range(steps):
        for i in range(steps):
            corner = j * row + i
            centre = vertices[[corner, corner + 1, corner + row, corner + row + 1]].mean(axis=0)
            if np.all(np.abs(centre[:2]) < hole_half):
                continue
            triangles += [[corner, corner + 1, corner + row + 1], [corner, corner + row + 1, corner + row]]
    return vertices, np.asarray(triangles)


def test_a_hole_in_a_chair_is_closed_facing_the_same_way_as_the_seat():
    vertices, triangles = seat_with_a_hole()
    capped = closed_object_holes(vertices, triangles, graph_with(chair_at(0.0, 0.0)))
    assert capped.closed == 1
    added = capped.inferred
    assert added.any() and not added[: len(vertices)].any()
    normals = vertex_normals(capped.vertices, capped.triangles)
    assert (normals[added][:, 2] > 0.99).all()


def test_a_hole_away_from_every_object_stays_open():
    vertices, triangles = seat_with_a_hole()
    capped = closed_object_holes(vertices, triangles, graph_with(chair_at(5.0, 5.0)))
    assert capped.closed == 0 and len(capped.vertices) == len(vertices)


def test_a_hole_too_large_to_be_one_gap_in_an_object_stays_open():
    vertices, triangles = seat_with_a_hole(hole_half=0.7, size=2.0)
    capped = closed_object_holes(vertices, triangles, graph_with(chair_at(0.0, 0.0, size=2.0)))
    assert capped.closed == 0


def test_dropping_vertices_drops_every_triangle_that_used_them():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=float)
    triangles = np.array([[0, 1, 2], [1, 3, 2]])
    kept_vertices, kept_triangles = without_vertices(vertices, triangles, np.array([True, False, False, False]))
    assert len(kept_vertices) == 3
    np.testing.assert_array_equal(kept_triangles, [[0, 2, 1]])


class CameraSized:
    def __init__(self, frame_id: str, width: int, height: int):
        self.frame_id, self.width, self.height = frame_id, width, height


def test_a_person_is_masked_where_they_stood_in_the_resized_photo():
    person = Detection(frame_id="frame-0001", name="person", box=(100, 50, 300, 250), movable=True, confidence=0.9)
    table = Detection(frame_id="frame-0001", name="table", box=(0, 0, 400, 400), movable=True, confidence=0.9)
    masks = people_masks({"frame-0001": [person, table]}, [CameraSized("frame-0001", 400, 400)], {"frame-0001": (200, 200)})
    mask = masks["frame-0001"]
    assert mask[60, 100] == 0.0
    assert mask[10, 10] == 1.0 and mask[190, 190] == 1.0


def test_known_detections_come_from_the_cache_and_never_ask(tmp_path, monkeypatch):
    monkeypatch.delenv(MODEL_ENV, raising=False)
    photo = tmp_path / "frame-0001.jpg"
    Image.new("RGB", (8, 8)).save(photo)
    unread = tmp_path / "frame-0002.jpg"
    Image.new("RGB", (8, 8), "white").save(unread)
    poses = tmp_path / "poses.json"
    poses.write_text("[]")
    person = Detection(frame_id="frame-0001", name="person", box=(1, 1, 4, 4), movable=True, confidence=0.9)
    DetectionCache(tmp_path / "detections", DEFAULT_MODEL).put(photo, [person])
    known = known_detections({"frame-0001": photo, "frame-0002": unread}, poses, tmp_path / "detections")
    assert list(known) == ["frame-0001"] and known["frame-0001"][0].is_person
    assert detections_digest(tmp_path / "detections") != ""
    assert detections_digest(tmp_path / "nothing-here") == ""


def camera_at(frame_id: str, position, looking_at, width=64, height=48, focal=50.0):
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    return PhotoCamera(
        frame_id=frame_id,
        room_to_camera=np.vstack([
            np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
            [0.0, 0.0, 0.0, 1.0],
        ]),
        fx=focal, fy=focal, cx=width / 2 - 0.5, cy=height / 2 - 0.5,
        width=width, height=height, timestamp=0.0,
    )


def whole_frame_person(frame_id: str) -> Detection:
    return Detection(frame_id=frame_id, name="person", box=(0, 0, 64, 48), movable=True, confidence=0.9)


def something_in_view():
    xs, zs = np.meshgrid(np.linspace(-0.2, 0.2, 5), np.linspace(0.8, 1.2, 5))
    return np.stack([xs.ravel(), np.full(xs.size, 2.0), zs.ravel()], axis=1)


def views_with_people_in(count_with_person: int, total: int):
    views = []
    for index in range(total):
        frame_id = f"frame-{index:04d}"
        camera = camera_at(frame_id, (0.3 * index - 0.6, 0.0, 1.0), (0.0, 2.0, 1.0))
        detections = [whole_frame_person(frame_id)] if index < count_with_person else []
        views.append((camera, detections, None))
    return views


def test_a_surface_in_a_person_outline_once_is_kept():
    points = something_in_view()
    assert not mostly_people(points, graph_with(), views_with_people_in(1, 5)).any()


def test_a_surface_that_is_a_person_in_most_photos_is_removed():
    points = something_in_view()
    assert mostly_people(points, graph_with(), views_with_people_in(4, 5)).all()
