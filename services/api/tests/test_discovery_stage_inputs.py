"""Discovery's inputs and outcome at the stage level: where crops go, and what survives a failed photo read."""

from types import SimpleNamespace

from standardphysics_contracts import Mat4, SurfaceHeight
from standardphysics_fixtures import build_graph
from standardphysics_pipeline.discovery import DiscoveryError

from standardphysics_api.stages import Stages


def test_lidar_heights_survive_a_failed_photo_discovery(tmp_path, monkeypatch):
    graph = build_graph().model_copy(update={"capture_to_room": Mat4.translation(0, 0, 0)})
    counter = next(node for node in graph.nodes if node.label == "Ordering counter")
    updated = counter.model_copy(update={"top_surface": SurfaceHeight(
        height_m=1.016, uncertainty_m=0.02, support_area_m2=0.3,
    )})
    mesh = tmp_path / "lidar-mesh"
    mesh.touch()
    pose = tmp_path / "poses"
    pose.touch()
    photo = tmp_path / "frame"
    photo.touch()
    monkeypatch.setattr("standardphysics_api.stages.room_faces", lambda *_: [])
    monkeypatch.setattr("standardphysics_api.stages.segment_surfaces", lambda *_: SimpleNamespace(
        nodes=[updated if node.id == counter.id else node for node in graph.nodes]
    ))

    def failed_discovery(_inputs):
        raise DiscoveryError("photo unavailable")

    result, outcome = Stages(discover=failed_discovery).discover_scan_with_report(
        graph, frame_paths=[photo], poses_path=pose, lidar_mesh_path=mesh,
    )
    assert outcome.failures
    assert result.by_id(counter.id).top_surface.height_m == 1.016


def test_discovery_inputs_point_crops_at_the_scan_crop_dir(tmp_path):
    import uuid as uuid_module

    from standardphysics_contracts import SceneGraph

    from standardphysics_api.stages import _discovery_inputs

    scan_root = tmp_path / "scans" / str(uuid_module.uuid4())
    artifacts = scan_root / "artifacts"
    artifacts.mkdir(parents=True)
    frame = artifacts / "frame-0007"
    poses = artifacts / "poses"
    lidar = artifacts / "lidar_mesh"
    for path, payload in ((frame, b"jpeg"), (poses, b"{}"), (lidar, b"mesh")):
        path.write_bytes(payload)

    graph = SceneGraph(scan_id=uuid_module.uuid4(), nodes=[])
    inputs = _discovery_inputs(graph, [frame], poses, lidar)
    assert inputs is not None
    assert inputs.crop_dir == scan_root / "crops"
    assert inputs.cache_dir == scan_root / "detections"

    missing = _discovery_inputs(graph, [frame], poses, artifacts / "nope_mesh")
    assert missing is None
