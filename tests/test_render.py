"""Blender-backed output. Skipped where Blender is not installed."""

import json
from math import cos, pi, sin
from uuid import uuid4

import pytest
from standardphysics_contracts import DisplayPart, DisplayReconstruction, Mat4, SceneNode, Vec3
from standardphysics_fixtures import build_graph, build_scenario
from standardphysics_pipeline.blender import (
    MIN_DISPLAY_WALL_THICKNESS,
    display_graph,
    export_glb,
    glb_mesh_bounds,
    glb_node_names,
    glb_ray_hit,
    render_finding,
)
from standardphysics_pipeline.check_blender import blender_path
from standardphysics_pipeline.locus import width_locus
from standardphysics_pipeline.measure import PipelineMeasurements


def blender_missing() -> bool:
    try:
        blender_path()
    except FileNotFoundError:
        return True
    return False


needs_blender = pytest.mark.skipif(blender_missing(), reason="Blender not installed")


def test_display_graph_gives_a_zero_thickness_wall_a_visual_shell_only():
    graph = build_graph()
    wall = next(node for node in graph.nodes if node.kind == "wall")
    zero_wall = wall.model_copy(update={"dimensions": wall.dimensions.model_copy(update={"y": 0.0})})
    measured = graph.model_copy(update={"nodes": [zero_wall if node.id == wall.id else node for node in graph.nodes]})

    displayed = display_graph(measured)

    assert measured.by_id(wall.id).dimensions.y == 0.0
    assert displayed.by_id(wall.id).dimensions.y == MIN_DISPLAY_WALL_THICKNESS


def test_display_graph_gives_a_zero_thickness_floor_a_visual_shell_only():
    graph = build_graph()
    floor = next(node for node in graph.nodes if node.kind == "floor")
    zero_floor = floor.model_copy(update={"dimensions": floor.dimensions.model_copy(update={"y": 0.0, "z": 0.0})})
    measured = graph.model_copy(update={"nodes": [zero_floor if node.id == floor.id else node for node in graph.nodes]})

    displayed = display_graph(measured)

    assert measured.by_id(floor.id).dimensions.y == 0.0
    assert displayed.by_id(floor.id).dimensions.y == MIN_DISPLAY_WALL_THICKNESS
    assert measured.by_id(floor.id).dimensions.z == 0.0
    assert displayed.by_id(floor.id).dimensions.z == MIN_DISPLAY_WALL_THICKNESS


@pytest.fixture
def finding_locus():
    graph, scenario = build_graph(), build_scenario()
    result = PipelineMeasurements().route_clear_width(graph, scenario, 0)
    return graph, width_locus(graph, result)


@needs_blender
def test_a_finding_renders(tmp_path, finding_locus):
    graph, locus = finding_locus
    out = render_finding(graph, locus, tmp_path / "finding.png", size=(400, 300))
    assert out.exists()
    assert out.stat().st_size > 5_000


@needs_blender
def test_glb_names_every_node_by_id(tmp_path):
    graph = build_graph()
    names = set(glb_node_names(export_glb(graph, tmp_path / "scene.glb")))
    assert {str(node.id) for node in graph.nodes} <= names


def _node(kind: str, category: str, dimensions: tuple[float, float, float], matrix: Mat4, *, parent_id=None) -> SceneNode:
    return SceneNode(
        id=uuid4(), kind=kind, label=category.title(), raw_category=category,
        dimensions=Vec3(x=dimensions[0], y=dimensions[1], z=dimensions[2]),
        transform=matrix, parent_id=parent_id,
    )


def _rotated_z(x: float, y: float, z: float, angle: float) -> Mat4:
    return Mat4(m=[
        cos(angle), -sin(angle), 0, x,
        sin(angle), cos(angle), 0, y,
        0, 0, 1, z,
        0, 0, 0, 1,
    ])


@needs_blender
def test_exported_sofa_stays_inside_its_measured_envelope(tmp_path):
    graph = build_graph()
    sofa = _node("object", "sofa", (2.0, 1.0, 1.0), Mat4.translation(1.0, 2.0, 1.0))
    scene = graph.model_copy(update={"nodes": [sofa]})

    low, high = glb_mesh_bounds(export_glb(scene, tmp_path / "sofa.glb"), str(sofa.id))

    assert low == pytest.approx((0.0, 1.5, 0.5), abs=0.02)
    assert high == pytest.approx((2.0, 2.5, 1.5), abs=0.02)


@needs_blender
def test_reconstructed_parts_keep_one_selectable_node_and_their_own_finishes(tmp_path):
    reconstruction = DisplayReconstruction(
        summary="Rounded cabinet with metal legs and glass display.", confidence=0.8,
        evidence_frame_ids=["frame-0001"],
        parts=[
            DisplayPart(name="body", primitive="box", center=[0, 0, 0], size=[1, 1, 1], bevel=0.08, base_color="#7a5432", material="wood"),
            DisplayPart(name="legs", primitive="cylinder", axis="z", center=[0.3, 0.3, -0.35], size=[0.12, 0.12, 0.3], bevel=0.1, base_color="#353535", material="metal"),
            DisplayPart(name="display", primitive="ellipsoid", center=[0, -0.2, 0.15], size=[0.5, 0.08, 0.4], base_color="#a9d8e8", material="glass"),
        ],
    )
    cabinet = _node("object", "storage", (2.0, 1.0, 1.0), Mat4.translation(1.0, 2.0, 1.0)).model_copy(
        update={"reconstruction": reconstruction}
    )
    graph = build_graph().model_copy(update={"nodes": [cabinet]})
    glb = export_glb(graph, tmp_path / "cabinet.glb")
    low, high = glb_mesh_bounds(glb, str(cabinet.id))
    raw = glb.read_bytes()
    json_length = int.from_bytes(raw[12:16], "little")
    exported = json.loads(raw[20:20 + json_length].decode("utf-8"))
    materials = {material["name"] for material in exported["materials"]}
    mesh = exported["meshes"][exported["nodes"][0]["mesh"]]

    assert glb_node_names(glb).count(str(cabinet.id)) == 1
    assert low == pytest.approx((0.0, 1.5, 0.5), abs=0.02)
    assert high == pytest.approx((2.0, 2.5, 1.5), abs=0.02)
    assert {"finish:#7a5432:wood", "finish:#353535:metal", "finish:#a9d8e8:glass"} <= materials
    assert len(mesh["primitives"]) == 3
    assert {primitive["material"] for primitive in mesh["primitives"]} == {0, 1, 2}


@needs_blender
def test_rotated_wall_portals_cut_only_aligned_overlapping_openings(tmp_path):
    graph = build_graph()
    wall = _node("wall", "wall", (4.0, 0.1, 3.0), _rotated_z(0, 0, 1.5, pi / 2))
    aligned = _node("door", "door", (1.0, 0.1, 2.0), _rotated_z(0, 0, 1.0, pi / 2))
    overlapping = _node("window", "window", (1.0, 0.1, 2.0), _rotated_z(0, 0.25, 1.0, pi / 2))
    perpendicular = _node("opening", "opening", (1.0, 0.1, 2.0), Mat4.translation(0, 1.5, 1.0))
    scene = graph.model_copy(update={"nodes": [wall, aligned, overlapping, perpendicular]})
    glb = export_glb(scene, tmp_path / "portals.glb")

    # Two overlapping, wall-aligned portals make one open span through the wall.
    assert glb_ray_hit(glb, (-3, 0, 1), (1, 0, 0)) is None
    # The nearby portal is perpendicular to the wall and must not cut it.
    assert glb_ray_hit(glb, (-3, 1.5, 1), (1, 0, 0)) == str(wall.id)


def test_the_camera_stays_inside_the_room(finding_locus):
    """A low camera puts the near wall between the lens and the subject, and
    spends half the frame outside the building."""
    graph, locus = finding_locus
    floor = next(node for node in graph.nodes if node.kind == "floor")
    half_depth = floor.dimensions.y / 2
    assert abs(locus.camera.position.y) < half_depth


def test_the_camera_clears_the_walls(finding_locus):
    graph, locus = finding_locus
    tallest = max(
        node.transform.position.z + node.dimensions.z / 2
        for node in graph.nodes
        if node.kind == "wall"
    )
    assert locus.camera.position.z > tallest * 0.8


def test_framing_includes_the_objects_causing_the_pinch(finding_locus):
    """A tight crop on the gap alone is two coloured shapes and no shop."""
    _, locus = finding_locus
    width = locus.bbox_max.x - locus.bbox_min.x
    assert width > 1.5
