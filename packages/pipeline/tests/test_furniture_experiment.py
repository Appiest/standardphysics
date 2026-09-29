"""Measured furniture boxes and generated meshes stay in the same Z-up frame."""

import numpy as np
import pytest
from standardphysics_pipeline.textures.atlas_texels import sample_surface
from standardphysics_pipeline.textures.camera import PhotoCamera
from standardphysics_pipeline.textures.furniture_experiment import (
    MeasuredBox,
    box_iou,
    choose_yaw,
    fit_to_box,
    scanned_object_points,
    spar_point_cloud,
    yawed,
)
from standardphysics_pipeline.textures.scan_colour import ColouredScan


def test_box_frame_round_trip_preserves_room_points():
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    box = MeasuredBox(rotation, np.array([2.0, -3.0, 0.8]), np.array([0.5, 0.7, 1.0]))
    local = np.array([[0.2, 0.1, -0.4], [-0.1, 0.3, 0.5]])
    assert box.to_local(box.to_room(local)) == pytest.approx(local)


def test_object_points_exclude_every_inferred_vertex():
    box = MeasuredBox(np.eye(3), np.array([1.0, 2.0, 0.0]), np.ones(3))
    scan = ColouredScan(
        vertices=np.array([[1.2, 2.0, 0.1], [1.3, 2.0, 0.2], [1.4, 2.0, 0.3]]),
        triangles=np.array([[0, 1, 2]]), colours=np.ones((3, 3)),
        seen=np.ones(3, dtype=bool), inferred=np.array([False, True, False]),
    )
    points, colours = scanned_object_points(scan, np.array([0, 0, 1]), 0, box)
    assert points[0] == pytest.approx([0.2, 0.0, 0.1])
    assert colours.shape == (1, 3)


def test_fit_fills_box_and_rests_on_its_bottom():
    vertices = np.array([[-2.0, -1.0, -3.0], [4.0, 3.0, 5.0], [0.0, 1.0, 2.0]])
    dimensions = np.array([0.5, 0.8, 1.0])
    fitted = fit_to_box(vertices, dimensions)
    assert fitted.min(axis=0) == pytest.approx(-dimensions / 2)
    assert fitted.max(axis=0) == pytest.approx(dimensions / 2)
    assert box_iou(fitted, dimensions) == pytest.approx(1.0)


def test_iou_counts_partial_overlap_in_three_dimensions():
    vertices = np.array([[0.0, -1.0, -1.0], [2.0, 1.0, 1.0]])
    assert box_iou(vertices, np.array([2.0, 2.0, 2.0])) == pytest.approx(1 / 3)


def test_yaw_choice_uses_scanned_surface_without_tilting():
    vertices = np.array([
        [-0.3, -0.2, -0.4], [0.3, -0.2, -0.4], [-0.3, 0.2, -0.4],
        [-0.1, 0.1, 0.4], [0.2, 0.2, 0.1],
    ])
    triangles = np.array([[0, 1, 3], [1, 4, 3], [1, 2, 4], [2, 0, 3]])
    dimensions = np.array([0.6, 0.8, 0.8])
    expected = fit_to_box(yawed(vertices, 1), dimensions)
    scanned, _ = sample_surface(expected[triangles], spacing=0.01, seed=0)
    fitted, yaw, distance = choose_yaw(vertices, triangles, dimensions, scanned)
    assert yaw == 1
    assert distance == pytest.approx(0.0, abs=1e-6)
    assert fitted.min(axis=0) == pytest.approx(-dimensions / 2)


def test_spar_cloud_has_512_normalized_xyzrgb_rows():
    box = MeasuredBox(np.eye(3), np.zeros(3), np.ones(3))
    camera = PhotoCamera(
        "frame-1", np.array([[0, 1, 0, 0], [0, 0, -1, 0], [-1, 0, 0, 2], [0, 0, 0, 1]], dtype=float),
        50, 50, 32, 24, 64, 48, 0,
    )
    points = np.array([[-0.5, -0.2, -0.5], [0.5, 0.2, 0.5], [0.1, 0.0, 0.0]])
    colours = np.array([[1.0, 0.0, 0.5], [0.0, 1.0, 0.5], [0.2, 0.4, 0.6]])
    cloud = spar_point_cloud(points, colours, box, camera)
    assert cloud.shape == (512, 6)
    assert np.abs(cloud[:, :3]).max() <= 0.5
    assert ((cloud[:, 3:] >= 0) & (cloud[:, 3:] <= 1)).all()
