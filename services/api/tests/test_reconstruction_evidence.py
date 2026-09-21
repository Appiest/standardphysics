from standardphysics_contracts import Mat4
from standardphysics_fixtures import build_graph

import standardphysics_api.stages as stage_module


def test_rebuild_uses_captured_photo_placement_and_restores_current_layout(monkeypatch, tmp_path):
    capture = build_graph()
    current = capture.model_copy(deep=True)
    node = next(node for node in current.nodes if node.kind == "object")
    original = capture.by_id(node.id).transform
    node.transform = Mat4.translation(8, 9, 1)
    seen = []

    def infer(graph, **kwargs):
        seen.append((graph, kwargs))
        return graph

    monkeypatch.setattr(stage_module, "reconstruct", infer)
    stages = stage_module.Stages(label=infer)
    photos = [tmp_path / "frame-0000"]
    poses, mesh = tmp_path / "poses", tmp_path / "mesh"
    result = stages.label_scan(current, frame_paths=photos, poses_path=poses, lidar_mesh_path=mesh, capture_graph=capture)
    assert seen[0][0].by_id(node.id).transform == original
    assert seen[0][1] == {"frame_paths": photos, "poses_path": poses, "lidar_mesh_path": mesh}
    assert result.by_id(node.id).transform == node.transform
    assert current.by_id(node.id).transform == node.transform
    assert capture.by_id(node.id).transform == original
