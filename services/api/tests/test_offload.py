import io
import tarfile
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from standardphysics_contracts import (
    Annotation,
    CameraPose,
    Locus,
    NodeTextureCoverage,
    TextureCoverage,
    Vec3,
)
from standardphysics_fixtures import build_graph
from standardphysics_pipeline import blender
from standardphysics_pipeline.textures import BakeInputs, BakeResult

from standardphysics_api.offload import Offload, _inside, _open, create_offload_app, offloaded
from standardphysics_api.settings import Settings
from standardphysics_api.stages import Stages

TOKEN = "s3cret"
COMMIT = "abc123"
BLENDER = "Blender 5.2.1"


def _unused(name):
    def fail(*args, **kwargs):
        raise AssertionError(f"{name} should not have been called")

    return fail


def _server_steps(**overrides):
    steps = {name: _unused(name) for name in ("bake_textures", "export_glb", "usdz_to_glb", "render_finding")}
    return SimpleNamespace(**{**steps, **overrides})


def _offload(steps, *, token=TOKEN, commit=COMMIT, blender_version=BLENDER, server_commit=COMMIT,
             server_blender=BLENDER, server_token=TOKEN):
    app = create_offload_app(server_token, server_commit, server_blender, steps)
    return Offload(
        url="http://testserver",
        token=token,
        commit=commit,
        timeout_seconds=30,
        blender_version=lambda: blender_version,
        client=lambda: TestClient(app),
    )


def _stages(offload, **local):
    fallbacks = {name: _unused(f"local {name}") for name in ("bake_textures", "export_glb", "usdz_to_glb", "render_finding")}
    return offloaded(Stages(**{**fallbacks, **local}), offload)


def _locus(graph):
    point = Vec3(x=1.0, y=0.0, z=1.0)
    return Locus(
        point=point,
        bbox_min=Vec3(x=0.0, y=0.0, z=0.0),
        bbox_max=Vec3(x=2.0, y=1.0, z=2.0),
        node_ids=[graph.nodes[0].id],
        annotation=Annotation(kind="dimension_line", points=[point, Vec3(x=2.0, y=0.0, z=1.0)], label="31 in"),
        camera=CameraPose(position=Vec3(x=3.0, y=2.0, z=3.0), target=point),
    )


def _coverage(graph):
    nodes = [NodeTextureCoverage(node_id=node.id, textured_fraction=0.5) for node in graph.nodes]
    return TextureCoverage(textured_fraction=0.5, nodes=nodes, needs_another_view=[])


def test_export_glb_comes_back_at_the_requested_path_with_the_lidar_mesh_content_and_never_runs_locally(tmp_path):
    graph = build_graph()
    lidar = tmp_path / "scan.obj"
    lidar.write_bytes(b"lidar-bytes")

    def remote_export(received_graph, out, lidar_mesh=None):
        assert received_graph == graph
        out.write_bytes(b"glb+" + lidar_mesh.read_bytes())
        return out

    stages = _stages(_offload(_server_steps(export_glb=remote_export)))
    wanted = tmp_path / "nested" / "model.glb"

    result = stages.export_glb(graph, wanted, lidar)

    assert result == wanted
    assert wanted.read_bytes() == b"glb+lidar-bytes"


def test_export_glb_without_a_lidar_mesh_sends_none(tmp_path):
    def remote_export(graph, out, lidar_mesh=None):
        assert lidar_mesh is None
        out.write_bytes(b"glb")
        return out

    stages = _stages(_offload(_server_steps(export_glb=remote_export)))

    assert stages.export_glb(build_graph(), tmp_path / "m.glb", None).read_bytes() == b"glb"


def test_usdz_conversion_keeps_file_names_and_returns_the_counts(tmp_path):
    usdz = tmp_path / "room.usdz"
    usdz.write_bytes(b"usdz-bytes")
    mapping = tmp_path / "room.metadata.json"
    mapping.write_text("{}")

    def remote_convert(received_usdz, out, received_mapping):
        assert received_usdz.name == "room.usdz"
        assert received_usdz.read_bytes() == b"usdz-bytes"
        assert received_mapping.name == "room.metadata.json"
        out.write_bytes(b"converted")
        return blender.ConversionResult(out, 9, 7, 3, 2, ["a", "b"])

    stages = _stages(_offload(_server_steps(usdz_to_glb=remote_convert)))
    wanted = tmp_path / "out" / "scene.glb"

    result = stages.usdz_to_glb(usdz, wanted, mapping)

    assert isinstance(result, blender.ConversionResult)
    assert result.glb_path == wanted
    assert wanted.read_bytes() == b"converted"
    assert (result.imported, result.meshes, result.renamed, result.unmapped_count) == (9, 7, 3, 2)
    assert result.unmapped_sample == ["a", "b"]


def test_render_finding_sends_the_locus_and_size_and_returns_the_still(tmp_path):
    graph = build_graph()
    locus = _locus(graph)

    def remote_render(received_graph, received_locus, out, size):
        assert received_graph == graph
        assert received_locus == locus
        assert size == (640, 480)
        out.write_bytes(b"png")
        return out

    stages = _stages(_offload(_server_steps(render_finding=remote_render)))
    wanted = tmp_path / "finding.png"

    assert stages.render_finding(graph, locus, wanted, (640, 480)) == wanted
    assert wanted.read_bytes() == b"png"


def test_render_finding_uses_the_default_size_when_none_is_given(tmp_path):
    graph = build_graph()

    def remote_render(received_graph, received_locus, out, size):
        assert size == (1200, 800)
        out.write_bytes(b"png")
        return out

    stages = _stages(_offload(_server_steps(render_finding=remote_render)))

    assert stages.render_finding(graph, _locus(graph), tmp_path / "f.png").read_bytes() == b"png"


def test_bake_sends_every_input_and_returns_outputs_in_the_callers_out_dir(tmp_path):
    graph = build_graph()
    frames = {}
    for name in ("frame-a", "frame-b"):
        frames[name] = tmp_path / f"{name}.jpg"
        frames[name].write_bytes(name.encode())
    poses = tmp_path / "poses.json"
    poses.write_text("[]")
    lidar = tmp_path / "scan.obj"
    lidar.write_bytes(b"mesh")
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "wall.png").write_bytes(b"wall-texture")
    out_dir = tmp_path / "caller-out"
    inputs = BakeInputs(graph, poses, frames, lidar, out_dir, materials)

    def remote_bake(received):
        assert received.bake_graph == graph
        assert received.poses_path.read_text() == "[]"
        assert {key: path.read_bytes() for key, path in received.frame_paths.items()} == {
            "frame-a": b"frame-a",
            "frame-b": b"frame-b",
        }
        assert received.lidar_mesh_path.read_bytes() == b"mesh"
        assert (received.materials_dir / "wall.png").read_bytes() == b"wall-texture"
        (received.out_dir / "scene.glb").write_bytes(b"baked")
        (received.out_dir / "coverage-0.png").write_bytes(b"mask")
        return BakeResult(
            received.out_dir / "scene.glb", [received.out_dir / "coverage-0.png"], _coverage(graph), 2, 1.5
        )

    stages = _stages(_offload(_server_steps(bake_textures=remote_bake)))

    result = stages.bake_textures(inputs)

    assert result.glb_path == out_dir / "scene.glb"
    assert result.glb_path.read_bytes() == b"baked"
    assert result.coverage_mask_paths == [out_dir / "coverage-0.png"]
    assert result.coverage_mask_paths[0].read_bytes() == b"mask"
    assert result.coverage == _coverage(graph)
    assert (result.frames_used, result.seconds) == (2, 1.5)


def test_bake_without_lidar_or_materials_sends_neither(tmp_path):
    graph = build_graph()
    poses = tmp_path / "poses.json"
    poses.write_text("[]")
    inputs = BakeInputs(graph, poses, {}, None, tmp_path / "out", tmp_path / "no-such-materials")

    def remote_bake(received):
        assert received.lidar_mesh_path is None
        assert received.materials_dir is None
        (received.out_dir / "scene.glb").write_bytes(b"baked")
        return BakeResult(received.out_dir / "scene.glb", [], _coverage(graph), 0, 0.1)

    stages = _stages(_offload(_server_steps(bake_textures=remote_bake)))

    assert stages.bake_textures(inputs).glb_path.read_bytes() == b"baked"


def test_a_blender_error_on_the_other_box_reaches_the_caller_without_running_locally(tmp_path):
    def remote_export(graph, out, lidar_mesh=None):
        raise blender.BlenderError("mesh has no faces")

    stages = _stages(_offload(_server_steps(export_glb=remote_export)))

    with pytest.raises(blender.BlenderError, match="mesh has no faces"):
        stages.export_glb(build_graph(), tmp_path / "m.glb", None)


def _local_export(graph, out, lidar_mesh=None):
    out.write_bytes(b"local glb")
    return out


def _remote_export_that_must_not_run(graph, out, lidar_mesh=None):
    raise AssertionError("the other box should have refused")


@pytest.mark.parametrize(
    "mismatch",
    [
        pytest.param({"token": "wrong"}, id="wrong-token"),
        pytest.param({"commit": "other"}, id="different-commit"),
        pytest.param({"blender_version": "Blender 4.0.0"}, id="different-blender"),
        pytest.param({"server_commit": "unknown"}, id="server-commit-unknown"),
        pytest.param({"commit": "unknown", "server_commit": "unknown"}, id="both-commits-unknown"),
    ],
)
def test_the_stage_runs_locally_when_the_other_box_refuses(tmp_path, mismatch):
    offload = _offload(_server_steps(export_glb=_remote_export_that_must_not_run), **mismatch)
    stages = _stages(offload, export_glb=_local_export)
    wanted = tmp_path / "m.glb"

    result = stages.export_glb(build_graph(), wanted, None)

    assert result == wanted
    assert wanted.read_bytes() == b"local glb"


def test_the_stage_runs_locally_when_the_other_box_cannot_be_reached(tmp_path):
    offload = Offload(url="http://127.0.0.1:9", token=TOKEN, commit=COMMIT, timeout_seconds=5,
                      blender_version=lambda: BLENDER)
    stages = _stages(offload, export_glb=_local_export)

    assert stages.export_glb(build_graph(), tmp_path / "m.glb", None).read_bytes() == b"local glb"


def test_the_stage_runs_locally_when_the_client_cannot_connect(tmp_path):
    def refuse():
        raise httpx.ConnectError("no route to host")

    offload = Offload(url="http://testserver", token=TOKEN, commit=COMMIT, timeout_seconds=5,
                      blender_version=lambda: BLENDER, client=refuse)
    stages = _stages(offload, export_glb=_local_export)

    assert stages.export_glb(build_graph(), tmp_path / "m.glb", None).read_bytes() == b"local glb"


def test_a_bake_falls_back_to_the_local_baker_with_the_original_inputs(tmp_path):
    graph = build_graph()
    poses = tmp_path / "poses.json"
    poses.write_text("[]")
    inputs = BakeInputs(graph, poses, {}, None, tmp_path / "out", None)
    expected = BakeResult(tmp_path / "local.glb", [], _coverage(graph), 0, 0.0)
    seen = []

    def local_bake(received):
        seen.append(received)
        return expected

    stages = _stages(_offload(_server_steps(), token="wrong"), bake_textures=local_bake)

    assert stages.bake_textures(inputs) is expected
    assert seen == [inputs]


def test_the_server_only_answers_health_without_credentials():
    app = create_offload_app(TOKEN, COMMIT, BLENDER, _server_steps())

    assert TestClient(app).get("/v1/health").json() == {"commit": COMMIT, "blender": BLENDER}


def test_an_unknown_operation_is_refused_with_not_found():
    app = create_offload_app(TOKEN, COMMIT, BLENDER, _server_steps())
    headers = {"Authorization": f"Bearer {TOKEN}", "X-SP-Commit": COMMIT, "X-SP-Blender": BLENDER}

    assert TestClient(app).post("/v1/stages/rm_rf", content=b"", headers=headers).status_code == 404


def test_offload_is_off_when_the_url_is_not_set():
    assert Offload.from_settings(_settings(offload_url=None, offload_token="t")) is None


def test_offload_is_off_when_the_token_is_not_set():
    assert Offload.from_settings(_settings(offload_url="http://box:8790", offload_token=None)) is None


def test_offload_from_settings_trims_the_trailing_slash_and_copies_the_settings():
    offload = Offload.from_settings(_settings(offload_url="http://box:8790/", offload_token="t", git_sha="deadbeef"))

    assert (offload.url, offload.token, offload.commit) == ("http://box:8790", "t", "deadbeef")
    assert offload.timeout_seconds == Settings().bake_timeout_seconds


def _settings(**changes):
    import dataclasses

    return dataclasses.replace(Settings(), **changes)


def test_stages_are_returned_unchanged_without_an_offload():
    stages = Stages()

    assert offloaded(stages, None) is stages


def test_offloaded_stages_keep_every_other_field():
    stages = Stages()
    wrapped = offloaded(stages, _offload(_server_steps()))

    assert wrapped is not stages
    assert wrapped.label is stages.label
    assert wrapped.ledger_factory is stages.ledger_factory
    assert wrapped.bake_textures is not stages.bake_textures


def test_a_parcel_member_cannot_escape_the_extraction_directory(tmp_path):
    with pytest.raises(ValueError, match="outside the parcel"):
        _inside(tmp_path, "../x")


def test_a_parcel_member_inside_the_directory_is_resolved(tmp_path):
    assert _inside(tmp_path, "frames/0/a.jpg") == (tmp_path / "frames/0/a.jpg").resolve()
    assert _inside(tmp_path, None) is None



def _parcel_with(tmp_path, member):
    parcel = tmp_path / "parcel.tar"
    with tarfile.open(parcel, "w") as tar:
        document = tarfile.TarInfo("document.json")
        document.size = 2
        tar.addfile(document, io.BytesIO(b"{}"))
        tar.addfile(member)
    return parcel


def test_a_parcel_carrying_a_link_is_refused(tmp_path):
    link = tarfile.TarInfo("out/scene.glb")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    with pytest.raises(ValueError, match="not a plain file"):
        _open(_parcel_with(tmp_path, link), tmp_path / "into")


def test_a_parcel_member_named_outside_the_directory_is_refused(tmp_path):
    escape = tarfile.TarInfo("../escaped")
    with pytest.raises(ValueError, match="outside the parcel"):
        _open(_parcel_with(tmp_path, escape), tmp_path / "into")
    assert not (tmp_path / "escaped").exists()
