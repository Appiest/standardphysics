"""Painting the scanned surface: which vertex gets which photo's colour.

The geometry here is a wall two metres away with a camera pointed at it, so
the right answer is arithmetic rather than opinion.
"""

from __future__ import annotations

import numpy as np
import pytest
from standardphysics_pipeline.textures.camera import PhotoCamera
from standardphysics_pipeline.textures.scan_colour import (
    UNSEEN,
    colour_the_scan,
    unused_vertices_removed,
    vertex_normals,
)


def camera_at(position, looking_at, width=64, height=48, focal=50.0) -> PhotoCamera:
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    return PhotoCamera(
        frame_id="frame-0001",
        room_to_camera=np.vstack([
            np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
            [0.0, 0.0, 0.0, 1.0],
        ]),
        fx=focal, fy=focal, cx=width / 2 - 0.5, cy=height / 2 - 0.5,
        width=width, height=height, timestamp=0.0,
    )


def wall(z_centre=1.0, half=0.4, at_y=2.0):
    """A small square of wall facing back toward the origin, as two triangles."""
    vertices = np.array([
        [-half, at_y, z_centre - half], [half, at_y, z_centre - half],
        [half, at_y, z_centre + half], [-half, at_y, z_centre + half],
    ], dtype=np.float64)
    return vertices, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)


def flat_photo(colour, width=64, height=48):
    return np.tile(np.asarray(colour, dtype=np.float32), (height, width, 1))


class TestNormals:
    def test_a_wall_faces_the_room(self):
        vertices, triangles = wall()
        normals = vertex_normals(vertices, triangles)
        assert np.allclose(np.abs(normals @ np.array([0.0, 1.0, 0.0])), 1.0, atol=0.01)

    def test_normals_are_unit_length(self):
        vertices, triangles = wall()
        assert np.allclose(np.linalg.norm(vertex_normals(vertices, triangles), axis=1), 1.0)


class TestColouring:
    def test_a_vertex_takes_the_colour_of_the_photo_that_saw_it(self):
        vertices, triangles = wall()
        camera = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        scan = colour_the_scan(vertices, triangles, [camera], [flat_photo((0.2, 0.6, 0.9))])
        assert scan.seen.all()
        assert scan.colours == pytest.approx(np.tile((0.2, 0.6, 0.9), (4, 1)), abs=0.02)

    def test_a_wall_behind_the_camera_keeps_the_scan_its_own_colour(self):
        vertices, triangles = wall(at_y=-2.0)
        camera = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        scan = colour_the_scan(vertices, triangles, [camera], [flat_photo((0.2, 0.6, 0.9))])
        assert not scan.seen.any()
        assert scan.colours == pytest.approx(np.tile(UNSEEN, (4, 1)), abs=0.01)

    def test_the_nearer_camera_wins_the_vertex(self):
        """Both frame the whole wall; the closer one resolves it better."""
        vertices, triangles = wall()
        near = camera_at((0.0, 0.4, 1.0), (0.0, 2.0, 1.0))
        far = camera_at((0.0, -4.0, 1.0), (0.0, 2.0, 1.0))
        scan = colour_the_scan(
            vertices, triangles, [far, near],
            [flat_photo((1.0, 0.0, 0.0)), flat_photo((0.0, 1.0, 0.0))],
        )
        assert scan.colours[:, 1].mean() > scan.colours[:, 0].mean()

    def test_a_photo_that_never_reaches_a_vertex_does_not_darken_it(self):
        """A second camera facing away must not overwrite what the first saw."""
        vertices, triangles = wall()
        seeing = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        away = camera_at((0.0, 6.0, 1.0), (0.0, 9.0, 1.0))
        scan = colour_the_scan(
            vertices, triangles, [seeing, away],
            [flat_photo((0.2, 0.6, 0.9)), flat_photo((0.0, 0.0, 0.0))],
        )
        assert scan.colours == pytest.approx(np.tile((0.2, 0.6, 0.9), (4, 1)), abs=0.02)


class TestTidyingUp:
    def test_vertices_no_triangle_uses_are_dropped_and_the_rest_still_index_right(self):
        vertices, triangles = wall()
        with_stray = np.vstack([vertices, [[9.0, 9.0, 9.0]]])
        camera = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        scan = colour_the_scan(with_stray, triangles, [camera], [flat_photo((0.3, 0.3, 0.3))])
        tidied = unused_vertices_removed(scan)
        assert len(tidied.vertices) == 4
        assert tidied.triangles.max() == 3
        assert np.allclose(tidied.vertices[tidied.triangles], vertices[triangles])

    def test_the_painted_share_is_reported(self):
        vertices, triangles = wall()
        camera = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        scan = colour_the_scan(vertices, triangles, [camera], [flat_photo((0.5, 0.5, 0.5))])
        assert scan.painted_fraction == 1.0
