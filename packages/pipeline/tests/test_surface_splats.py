"""Synthetic fixtures for the deterministic surface-Gaussian builder."""

from __future__ import annotations

import numpy as np
import pytest
from standardphysics_pipeline.render_efficiency import (
    MetricError,
    SurfaceGaussians,
    build_surface_gaussians,
    inverse_sigmoid,
    quaternion_from_rotation,
    rgb_from_sh0,
    sh0_from_rgb,
    surface_covariance,
    tangent_basis,
)
from standardphysics_pipeline.render_efficiency.builder import sample_mesh


def _grid_plane(side: int = 8, z: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = np.meshgrid(np.arange(side, dtype=np.float64), np.arange(side, dtype=np.float64))
    vertices = np.stack([xs.ravel(), ys.ravel(), np.full(side * side, z)], axis=1)
    triangles = []
    for r in range(side - 1):
        for c in range(side - 1):
            i = r * side + c
            triangles.append([i, i + side, i + 1])
            triangles.append([i + 1, i + side, i + side + 1])
    return vertices, np.asarray(triangles, dtype=np.int64)


def test_sh0_roundtrip():
    rgb = np.array([[1.0, 0.5, 0.25], [0.1, 0.2, 0.3]])
    assert np.allclose(rgb_from_sh0(sh0_from_rgb(rgb)), rgb, atol=1e-5)


def test_sh0_known_zero_rgb():
    f_dc = sh0_from_rgb(np.zeros(3))
    # (0 - 0.5) / 0.28209479177387814
    expected = -0.5 / 0.28209479177387814
    assert np.allclose(f_dc, expected)


def test_sh0_rejects_bad_shape():
    with pytest.raises(MetricError):
        sh0_from_rgb(np.zeros((2, 2)))


def test_tangent_basis_plus_z():
    t1, t2 = tangent_basis(np.array([0.0, 0.0, 1.0]))
    assert np.allclose(np.linalg.norm(t1), 1.0)
    assert np.allclose(np.linalg.norm(t2), 1.0)
    assert np.allclose(np.dot(t1, t2), 0.0, atol=1e-6)
    assert np.allclose(t2, [0.0, 0.0, 1.0]) or np.allclose(np.cross(t1, t2), [0.0, 0.0, 1.0])


def test_tangent_basis_minus_z():
    t1, t2 = tangent_basis(np.array([0.0, 0.0, -1.0]))
    assert np.allclose(np.linalg.norm(t1), 1.0)
    assert np.allclose(np.linalg.norm(t2), 1.0)
    assert np.allclose(np.dot(t1, t2), 0.0, atol=1e-6)


def test_tangent_basis_orthonormal_set():
    oblique = np.array([1.0, 1.0, 1.0]) / np.sqrt(3.0)
    normals = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], oblique, [0.0, 0.0, -1.0]])
    t1, t2 = tangent_basis(normals)
    for row in range(4):
        assert t1[row].dot(t2[row]) == pytest.approx(0.0, abs=1e-6)
        assert np.linalg.norm(t1[row]) == pytest.approx(1.0, abs=1e-6)
        assert np.linalg.norm(t2[row]) == pytest.approx(1.0, abs=1e-6)
        # [t1, t2, n] right-handed
        assert np.linalg.det(np.stack([t1[row], t2[row], normals[row]], axis=1)) == pytest.approx(1.0, abs=1e-4)


def test_covariance_normal_is_smallest_axis():
    normal = np.array([0.0, 0.0, 1.0])
    frames, scales = surface_covariance(normal, spacing_h=0.05, tangent_factor=0.75, normal_ratio=0.1)
    rotation = frames  # 3x3, columns = [t1, t2, n]
    covariance = rotation @ np.diag(scales**2) @ rotation.T
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    assert np.allclose(eigenvectors[:, 0], [0.0, 0.0, 1.0], atol=1e-4) or np.allclose(
        eigenvectors[:, 0], [0.0, 0.0, -1.0], atol=1e-4
    )
    assert eigenvalues[0] == pytest.approx((0.75 * 0.05 * 0.1) ** 2, rel=1e-4)


def test_covariance_rejects_bad_inputs():
    with pytest.raises(MetricError):
        surface_covariance(np.array([0.0, 0.0, 1.0]), spacing_h=0.0)
    with pytest.raises(MetricError):
        surface_covariance(np.array([0.0, 0.0, 1.0]), spacing_h=-0.05)
    with pytest.raises(MetricError):
        surface_covariance(np.array([0.0, 0.0, 1.0]), spacing_h=0.05, normal_ratio=1.5)


def test_quaternion_identity():
    identity = np.eye(3)
    quat = quaternion_from_rotation(identity)
    assert np.allclose(quat, [1.0, 0.0, 0.0, 0.0], atol=1e-6)
    assert np.linalg.norm(quat) == pytest.approx(1.0)


def test_quaternion_90_degree_z():
    angle = np.pi / 2
    rotation = np.array([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])
    quat = quaternion_from_rotation(rotation)
    assert np.allclose(quat, [np.cos(angle / 2), 0.0, 0.0, np.sin(angle / 2)], atol=1e-6)


def test_inverse_sigmoid_roundtrip():
    for value in (0.1, 0.5, 0.9):
        logit = inverse_sigmoid(np.array(value))
        recovered = 1.0 / (1.0 + np.exp(-logit))
        assert recovered == pytest.approx(value, abs=1e-6)


def test_builder_caps_sample_count():
    vertices, triangles = _grid_plane(side=32)
    colours = np.full((len(vertices), 3), 0.5)
    result = build_surface_gaussians(
        vertices, triangles, colours, spacing=0.1, max_samples=500, seed=1729
    )
    assert len(result.gaussians) <= 500


def test_builder_interpolates_vertex_colours():
    vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    triangles = np.array([[0, 1, 2]], dtype=np.int64)
    colours = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    result = build_surface_gaussians(vertices, triangles, colours, spacing=10.0, seed=1729)
    # A single sample inside the triangle blends the three pure corner colours,
    # so none of the RGB channels can be exactly 1.0 or 0.0 together.
    rgb = rgb_from_sh0(result.gaussians.sh0)
    assert (rgb < 1.0).all()
    assert (rgb > -1e-6).all()


def test_builder_skips_degenerate_triangle():
    vertices = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=np.float64)
    triangles = np.array([[0, 1, 2]], dtype=np.int64)
    colours = np.zeros((3, 3))
    with pytest.raises(MetricError):
        build_surface_gaussians(vertices, triangles, colours, spacing=0.1)


def test_export_ply_then_read_header(tmp_path):
    silhouette = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    triangoli = np.array([[0, 1, 2]])
    colours = np.full((3, 3), 0.5)
    result = build_surface_gaussians(silhouette, triangoli, colours, spacing=10.0)
    out = tmp_path / "surface.ply"
    result.gaussians.export_ply(out)
    raw = out.read_bytes()
    assert raw.startswith(b"ply\n")
    assert f"element vertex {len(result.gaussians)}".encode() in raw


def test_export_rejects_nonfinite():
    gauss = SurfaceGaussians(
        positions=np.array([[np.nan, 0.0, 0.0]]),
        frames=np.eye(3)[None],
        scales=np.array([[0.01, 0.01, 0.01]]),
        opacities=np.array([0.5]),
        sh0=np.array([[0.0, 0.0, 0.0]]),
    )
    with pytest.raises(MetricError):
        gauss.export_ply(__import__("pathlib").Path("unused.ply"))


def _single_triangle(colours: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vertices = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64
    )
    triangles = np.array([[0, 1, 2]], dtype=np.int64)
    return vertices, triangles, np.asarray(colours, dtype=np.float64)


def test_c01_uniform_red_triangle_reproduces_red():
    vertices, triangles, colours = _single_triangle(np.full((3, 3), [1.0, 0.0, 0.0]))
    result = build_surface_gaussians(vertices, triangles, colours, spacing=10.0, seed=1729)
    rgb = rgb_from_sh0(result.gaussians.sh0)
    assert np.allclose(rgb, [1.0, 0.0, 0.0], atol=1e-6)


def test_c01_identical_xyz_with_green_changes_output():
    vertices, triangles, red = _single_triangle(np.full((3, 3), [1.0, 0.0, 0.0]))
    green = np.full((3, 3), [0.0, 1.0, 0.0])
    red_result = build_surface_gaussians(vertices, triangles, red, spacing=10.0, seed=1729)
    green_result = build_surface_gaussians(vertices, triangles, green, spacing=10.0, seed=1729)
    red_rgb = rgb_from_sh0(red_result.gaussians.sh0)
    green_rgb = rgb_from_sh0(green_result.gaussians.sh0)
    assert not np.allclose(red_rgb, green_rgb)


def test_c02_translation_and_rotation_preserve_colours():
    vertices, triangles, colours = _single_triangle(
        np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    )
    original = build_surface_gaussians(vertices, triangles, colours, spacing=10.0, seed=1729)
    original_rgb = rgb_from_sh0(original.gaussians.sh0)

    translated = vertices + np.array([10.0, -4.0, 3.0])
    translated_result = build_surface_gaussians(translated, triangles, colours, spacing=10.0, seed=1729)
    assert np.allclose(rgb_from_sh0(translated_result.gaussians.sh0), original_rgb, atol=1e-6)

    angle = np.pi / 3
    rotation = np.array([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])
    rotated = vertices @ rotation.T
    rotated_result = build_surface_gaussians(rotated, triangles, colours, spacing=10.0, seed=1729)
    assert np.allclose(rgb_from_sh0(rotated_result.gaussians.sh0), original_rgb, atol=1e-6)


def test_c03_known_barycentric_weights_match_independent_calculation():
    colours = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    vertices, triangles, colours = _single_triangle(colours)
    result = build_surface_gaussians(vertices, triangles, colours, spacing=0.25, seed=7)
    decoded = rgb_from_sh0(result.gaussians.sh0)
    states = result.sample_states
    # Independent barycentric reconstruction from the exported positions alone:
    # w1 = ((y2-y3)(x-x3) + (x3-x2)(y-y3)) / denom, etc.
    v0, v1, v2 = vertices[0], vertices[1], vertices[2]
    denom = (v1[1] - v2[1]) * (v0[0] - v2[0]) + (v2[0] - v1[0]) * (v0[1] - v2[1])
    for point, state, expected_rgb_row in zip(
        result.gaussians.positions, states, decoded, strict=False
    ):
        assert state.supported
        weight_v0 = ((v1[1] - v2[1]) * (point[0] - v2[0]) + (v2[0] - v1[0]) * (point[1] - v2[1])) / denom
        weight_v1 = ((v2[1] - v0[1]) * (point[0] - v2[0]) + (v0[0] - v2[0]) * (point[1] - v2[1])) / denom
        weight_v2 = 1.0 - weight_v0 - weight_v1
        expected = weight_v0 * colours[0] + weight_v1 * colours[1] + weight_v2 * colours[2]
        assert np.allclose(expected_rgb_row, expected, atol=1e-6)


def test_c04_all_unseen_input_fails_explicitly():
    vertices, triangles, colours = _single_triangle(np.full((3, 3), [0.5, 0.5, 0.5]))
    seen = np.zeros(3, dtype=bool)
    with pytest.raises(MetricError, match="no-supported-output|supported"):
        build_surface_gaussians(
            vertices, triangles, colours, spacing=10.0, seen=seen, source_ids=["u"] * 3
        )


def test_c04_mixed_support_exports_only_observed_with_source_ids():
    vertices, triangles = _grid_plane(side=4, z=0.25)
    colours = np.full((len(vertices), 3), [0.2, 0.4, 0.6])
    seen = np.zeros(len(vertices), dtype=bool)
    source_ids = np.array([f"frame-{i:03d}" for i in range(len(vertices))], dtype=object)
    # Only the first triangle's three corners are seen.
    seen[triangles[0]] = True
    expected_sources = {source_ids[triangles[0, 0]], source_ids[triangles[0, 2]]}
    result = build_surface_gaussians(
        vertices, triangles, colours, spacing=1.0, seen=seen, source_ids=source_ids, seed=1729
    )
    decoded = rgb_from_sh0(result.gaussians.sh0)
    assert np.allclose(decoded, [0.2, 0.4, 0.6], atol=1e-6)
    states = result.sample_states
    assert all(state.supported for state in states)
    for state in states:
        assert expected_sources.issubset(set(state.source_ids))
        assert state.triangle == 0
    assert result.rejected_count == 0
    assert result.unsupported_count > 0


def test_builder_rejects_out_of_bounds_triangle_indices():
    vertices, triangles, colours = _single_triangle(np.zeros((3, 3)))
    triangles = np.array([[0, 1, 7]], dtype=np.int64)
    with pytest.raises(MetricError):
        build_surface_gaussians(vertices, triangles, colours, spacing=10.0)


def test_builder_rejects_colours_outside_unit_interval():
    vertices, triangles, colours = _single_triangle(np.ones((3, 3)))
    colours[0, 0] = 1.5
    with pytest.raises(MetricError):
        build_surface_gaussians(vertices, triangles, colours, spacing=10.0)


def test_builder_allocates_within_cap_before_expansion():
    vertices, triangles = _grid_plane(side=8)
    colours = np.full((len(vertices), 3), 0.5)
    tiny_spacing = 1e-4
    result = build_surface_gaussians(
        vertices, triangles, colours, spacing=tiny_spacing, max_samples=200, seed=1729
    )
    assert len(result.gaussians) == 200
    assert np.isfinite(result.gaussians.positions).all()


def test_sample_mesh_rejects_nonpositive_spacing():
    vertices, triangles, _ = _single_triangle(np.zeros((3, 3)))
    with pytest.raises(MetricError):
        sample_mesh(vertices, triangles, spacing=0.0)
    with pytest.raises(MetricError):
        sample_mesh(vertices, triangles, spacing=-0.1)
    with pytest.raises(MetricError):
        sample_mesh(vertices, triangles, spacing=np.nan)


def test_export_refuses_to_overwrite_existing_file(tmp_path):
    silhouette = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    triangoli = np.array([[0, 1, 2]])
    colours = np.full((3, 3), 0.5)
    result = build_surface_gaussians(silhouette, triangoli, colours, spacing=10.0)
    out = tmp_path / "surface.ply"
    result.gaussians.export_ply(out)
    with pytest.raises(MetricError, match="exists|overwrite"):
        result.gaussians.export_ply(out)
