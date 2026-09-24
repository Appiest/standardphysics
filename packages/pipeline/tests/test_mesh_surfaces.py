"""Measured surfaces must not acquire height from RoomPlan or display patches."""

import uuid

import numpy as np
import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.discovery.mesh_surfaces import segment_surfaces
from standardphysics_pipeline.measure import PipelineMeasurements


def _node(label, centre, size):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, label), kind="object", label=label,
        raw_category=label, dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*centre),
    )


def _plane(low, high, height, cells=12):
    x, y = np.meshgrid(np.linspace(low, high, cells), np.linspace(low, high, cells))
    points = np.stack([x, y, np.full_like(x, height)], axis=2)
    corners = []
    for row in range(cells - 1):
        for column in range(cells - 1):
            square = points[row:row + 2, column:column + 2]
            corners.extend((np.stack([square[0, 0], square[0, 1], square[1, 1]]),
                            np.stack([square[0, 0], square[1, 1], square[1, 0]])))
    return np.asarray(corners)


def _graph():
    floor = _node("Floor", (0, 0, 0), (4, 4, 0.02)).model_copy(update={"kind": "floor"})
    counter = _node("Ordering counter", (0, 0, 0.46), (0.8, 0.8, 0.92))
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, counter])


def test_scan_top_overrides_low_box_and_carries_uncertainty():
    graph = _graph()
    scanned = np.concatenate([_plane(-1.5, 1.5, 0), _plane(-0.38, 0.38, 1.016)])
    result = segment_surfaces(scanned, graph)
    counter = result.nodes[1]
    height = PipelineMeasurements().counter_height(graph.model_copy(update={"nodes": result.nodes}), counter.id)
    assert height.inches == pytest.approx(40, abs=0.15)
    assert 0 < height.uncertainty_inches < 4
    assert not height.needs_measurement
    assert result.labelled_area_m2 >= result.before_area_m2
    assert any(piece.orientation == "horizontal" and piece.owner_id == counter.id for piece in result.pieces)


def test_missing_top_is_explicitly_unmeasured():
    graph = _graph()
    result = segment_surfaces(_plane(-1.5, 1.5, 0), graph)
    counter = result.nodes[1]
    assert counter.top_surface.height_m is None
    assert PipelineMeasurements().counter_height(graph.model_copy(update={"nodes": result.nodes}), counter.id).needs_measurement


def test_unlabelled_sloped_and_vertical_faces_remain_unassigned():
    graph = _graph()
    slope = np.asarray([[[1, 1, 0.2], [2, 1, 1.4], [1, 2, 0.2]],
                        [[1, 1, 0.2], [1, 1, 1.2], [1, 2, 0.2]]])
    result = segment_surfaces(np.concatenate([_plane(-1.5, 1.5, 0), slope]), graph)
    assert any(piece.orientation == "sloped" and piece.label is None for piece in result.pieces)
    assert any(piece.orientation == "vertical" and piece.label is None for piece in result.pieces)
    assert result.labelled_area_m2 < result.total_area_m2


def test_disconnected_unknown_tops_become_reviewable_candidates_without_inflating_coverage():
    graph = _graph()
    unknown = np.concatenate([_plane(1.0, 1.5, 0.75), _plane(-1.5, -1.0, 0.75)])
    result = segment_surfaces(np.concatenate([_plane(-1.5, 1.5, 0), unknown]), graph)
    candidates = [node for node in result.nodes if node.raw_category == "lidar_candidate"]
    assert len(candidates) == 2
    assert all(node.quality == "needs_another_look" and not node.movable for node in candidates)
    assert all(node.dimensions.x > 0.3 and node.dimensions.y > 0.3 for node in candidates)
    assert {piece.owner_id for piece in result.pieces if piece.owner_id in {node.id for node in candidates}} == {
        node.id for node in candidates
    }
    assert result.labelled_area_m2 < result.total_area_m2
    repeated = segment_surfaces(np.concatenate([_plane(-1.5, 1.5, 0), unknown]),
                                graph.model_copy(update={"nodes": result.nodes}))
    assert {node.id for node in repeated.nodes if node.raw_category == "lidar_candidate"} == {
        node.id for node in candidates
    }


def test_high_table_is_not_inferred_as_room_ceiling():
    graph = _graph()
    wall = _node("Wall", (2, 0, 1.2), (0.05, 4, 2.4)).model_copy(update={"kind": "wall"})
    graph = graph.model_copy(update={"nodes": [*graph.nodes, wall]})
    result = segment_surfaces(np.concatenate([_plane(-1.5, 1.5, 0), _plane(-0.4, 0.4, 2.35)]), graph)
    assert not any(node.kind == "ceiling" for node in result.nodes)


def test_sloped_lidar_patch_needs_a_photo_before_it_is_called_a_ramp():
    graph = _graph()
    slope = np.asarray([[[1, 0.8, 0.2], [1.8, 0.8, 1.4], [1.8, 1.6, 1.4]],
                        [[1, 0.8, 0.2], [1.8, 1.6, 1.4], [1, 1.6, 0.2]]])
    result = segment_surfaces(np.concatenate([_plane(-1.5, 1.5, 0), slope]), graph)
    assert any(piece.orientation == "sloped" and piece.label is None for piece in result.pieces)
    assert any(node.raw_category == "lidar_candidate" and node.quality == "needs_another_look"
               for node in result.nodes)
    assert not any(node.label == "Ramp" for node in result.nodes)
