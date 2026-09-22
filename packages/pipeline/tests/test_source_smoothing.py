"""Source-consistency smoothing tests: visibility-safe relabeling only."""

import numpy as np
from standardphysics_pipeline.render_efficiency.photo_mesh_500 import RasterCamera  # noqa: F401
from standardphysics_pipeline.textures.camera import PhotoCamera
from standardphysics_pipeline.textures.surface_photos import choose_views_partial, smooth_assignment


def two_tri_quad():
    vertices = np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float64)
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    return vertices, triangles


def test_relabel_uses_only_own_second_choice():
    assignment = np.array([3, 4], dtype=np.int32)
    second_assignment = np.array([4, -1], dtype=np.int32)
    best = np.array([1.0, 1.0], dtype=np.float32)
    second = np.array([0.9, 0.0], dtype=np.float32)
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    smoothed = smooth_assignment(triangles, assignment, second_assignment,
                                 best, second, smooth_factor=0.85, min_votes=1, rounds=1)
    allowed = [v for v in smoothed if v != assignment[0]]
    assert set(allowed).issubset({3, 4, -1})  # never invents a new source


def test_smoothing_vote_requires_matching_neighbor():
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    assignment = np.array([3, 1], dtype=np.int32)      # neighbours disagree
    second_assignment = np.array([4, 4], dtype=np.int32)  # but both could take 4
    best = np.array([1.0, 1.0], dtype=np.float32)
    second = np.array([0.95, 0.95], dtype=np.float32)
    smoothed = smooth_assignment(triangles, assignment, second_assignment,
                                 best, second, smooth_factor=0.85, min_votes=2, rounds=1)
    # votes are from the neighbour whose label matches the candidate: face0's
    # candidate 4 matches face1's label? face1 label is 1, not 4 -> no votes.
    assert np.array_equal(smoothed, assignment)


def test_smoothing_relabels_with_two_votes():
    triangles = np.array([[0, 1, 2], [0, 2, 3], [0, 3, 4]])
    assignment = np.array([3, 4, 3], dtype=np.int32)
    second_assignment = np.array([-1, 3, -1], dtype=np.int32)
    best = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    second = np.array([0.0, 0.95, 0.0], dtype=np.float32)
    smoothed = smooth_assignment(triangles, assignment, second_assignment,
                                 best, second, smooth_factor=0.85, min_votes=2, rounds=1)
    assert np.array_equal(smoothed, np.array([3, 3, 3], dtype=np.int32))


def test_choose_views_partial_smooth_preserves_unassigned_and_acceptance():
    vertices, triangles = two_tri_quad()
    cameras = []
    for shift in (0.6, 0.8):
        rotation = np.diag([1.0, -1.0, -1.0])
        translation = np.array([shift, shift, 3.0])
        cameras.append(PhotoCamera(f"cam-{shift}", np.column_stack([rotation, translation]),
                                   500, 500, 250, 250, 500, 500, 0.0))
    assignment, areas = choose_views_partial(
        vertices, triangles, cameras, vertices, triangles,
        min_facing=0.01, centre_required=True, smooth_factor=0.85, smooth_min_votes=1)
    assert len(assignment) == 2
    assert set(np.unique(assignment)).issubset({0, 1, -1})
