"""Numeric projection and an actual Blender/glTF raster test, with no Moffett data."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import standardphysics_pipeline
from PIL import Image
from standardphysics_pipeline.check_blender import blender_path
from standardphysics_pipeline.textures.blender_camera import blender_view
from standardphysics_pipeline.textures.camera import PhotoCamera


def installed_blender() -> Path:
    """The Blender the pipeline itself would run, or a skip where there is none."""
    try:
        return Path(blender_path())
    except FileNotFoundError:
        pytest.skip("actual raster verification requires Blender")


def example_camera():
    # Deliberately oblique and translated, with unequal focal lengths and off-centre K.
    x, z = .23, -.37
    rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    rotation = rz @ rx
    position = np.array([1.7, -2.1, .9])
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = -rotation @ position
    return PhotoCamera("oblique", transform, 410, 430, 341, 225, 640, 480, 0)


@pytest.mark.parametrize("crop,size", [((0, 0, 640, 480), (640, 480)),
                                       ((80, 0, 560, 480), (500, 500)),
                                       ((50, 30, 530, 400), (333, 217))])
def test_blender_projection_matches_original_photo_crop_resize(crop, size):
    camera = example_camera()
    view = blender_view(camera, crop, size)
    # Independent room points, not the adapter's inverse-projected verification probes.
    points = np.random.default_rng(7).uniform([-2, -3, 3], [3, 2, 8], (100, 3))
    u, v, _ = camera.project(points)
    expected = np.column_stack([((u-crop[0])+.5)*size[0]/(crop[2]-crop[0])-.5,
                                ((v-crop[1])+.5)*size[1]/(crop[3]-crop[1])-.5])
    local = np.column_stack([points, np.ones(len(points))]) @ np.linalg.inv(view["matrix_world"]).T
    aspect = view["pixel_aspect_y"]/view["pixel_aspect_x"]
    focal = view["lens_mm"]/36*size[0]
    actual = np.column_stack([focal*local[:, 0]/-local[:, 2]+size[0]/2-view["shift_x"]*size[0]-.5,
                              -focal/aspect*local[:, 1]/-local[:, 2]+size[1]/2
                              + view["shift_y"]*size[0]/aspect-.5])
    np.testing.assert_allclose(actual, expected, atol=1e-9)
    assert np.linalg.det(np.asarray(view["matrix_world"])[:3, :3]) == pytest.approx(1)


def test_pixel_axis_conversion_is_equivalent_to_original_camera_pose():
    c2r = np.eye(4)
    c2r[:3, 3] = [0, 0, 1.12]
    pose = np.linalg.inv(example_camera().room_to_camera)
    flip = np.diag([1., -1., -1., 1.])
    world_to_pixel = flip @ np.linalg.inv(pose) @ np.linalg.inv(c2r)
    np.testing.assert_allclose(np.linalg.inv(world_to_pixel) @ flip, c2r @ pose, atol=1e-12)


@pytest.mark.parametrize("crop,size", [((-1, 0, 10, 10), (500, 500)),
                                       ((0, 0, 641, 480), (500, 500)),
                                       ((0, 0, 100, 100), (0, 500))])
def test_invalid_image_extent_fails(crop, size):
    with pytest.raises(ValueError):
        blender_view(example_camera(), crop, size)


def test_reflected_camera_is_rejected():
    camera = example_camera()
    camera.room_to_camera[0] *= -1
    with pytest.raises(ValueError, match="proper rigid"):
        blender_view(camera, (0, 0, 640, 480), (500, 500))


def test_coverage_must_use_material_support_masks_never_rgb_appearance(tmp_path):
    """Lock in the lesson of the invalid 98% coverage claim.

    The photograph that covers a surface can itself be neutral grey, and an
    uncovered surface with the same grey looks identical in the beauty render.
    Coverage measured by colour thresholds on that render cannot separate the
    two, so a pass number computed that way is bogus in both directions. The
    dedicated material-support pass is unambiguous: textured faces are white,
    faces without a source photo are black.
    """
    blender = installed_blender()
    trimesh = pytest.importorskip("trimesh")
    camera = example_camera()
    view = blender_view(camera, (80, 0, 560, 480), (500, 500))
    transform = camera.room_to_camera
    position = -transform[:3, :3].T @ transform[:3, 3]
    authored_grey = (158, 153, 148)
    texture_grey = (207, 204, 201)  # what the unlit neutral factor actually renders as post-conversion

    def quad_at(u, v, depth, size=6):
        corners = []
        for dx, dy in ((-size, -size), (size, -size), (size, size), (-size, size)):
            pu = (u + dx + .5) * 480 / 500 - .5 + 80
            pv = (v + dy + .5) * 480 / 500 - .5
            ray = np.array([(pu - camera.cx) / camera.fx, (pv - camera.cy) / camera.fy, 1])
            corners.append(position + transform[:3, :3].T @ (depth * ray))
        return np.array(corners)

    scene = trimesh.Scene()
    photographed_vertices = quad_at(160, 110, .7)[:, [0, 2, 1]] * [1, 1, -1]
    scene.add_geometry(trimesh.Trimesh(
        photographed_vertices, [[0, 1, 2], [0, 2, 3]], process=False,
        visual=trimesh.visual.TextureVisuals(
            uv=[[0, 0], [1, 0], [1, 1], [0, 1]],
            material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.new("RGB", (2, 2), texture_grey),
                metallicFactor=0, roughnessFactor=1, doubleSided=True))))
    unphotographed_vertices = quad_at(340, 110, .7)[:, [0, 2, 1]] * [1, 1, -1]
    scene.add_geometry(trimesh.Trimesh(
        unphotographed_vertices, [[0, 1, 2], [0, 2, 3]], process=False,
        visual=trimesh.visual.TextureVisuals(
            uv=[[0, 0], [1, 0], [1, 1], [0, 1]],
            material=trimesh.visual.material.PBRMaterial(
                baseColorFactor=[*[c / 255 for c in authored_grey], 1.0], baseColorTexture=None,
                metallicFactor=0, roughnessFactor=1, doubleSided=True))))

    def unlit(tree):
        tree.setdefault("extensionsUsed", []).append("KHR_materials_unlit")
        for material in tree["materials"]:
            material.setdefault("extensions", {})["KHR_materials_unlit"] = {}

    glb = tmp_path / "coverage-fixture.glb"
    glb.write_bytes(trimesh.exchange.gltf.export_glb(scene, tree_postprocessor=unlit))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"glb": str(glb), "views": [view]}))
    script = Path(standardphysics_pipeline.__file__).parent / "blender_scripts/render_calibrated_mesh.py"
    result = subprocess.run([str(blender), "-b", "--factory-startup", "--python-exit-code", "1",
                             "-P", str(script), "--", str(plan)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-2500:] + result.stderr[-2500:]

    beauty = np.asarray(Image.open(tmp_path / "oblique_candidate.png").convert("RGB"))
    support = np.asarray(Image.open(tmp_path / "oblique_photo_support.png").convert("L")) > 128
    # The photographed face (texture) and the uncovered face (authored grey factor)
    # both end up at the same rendered grey, so colour thresholds give the same
    # verdict to faces that need opposite verdicts.
    photographed_colour = beauty[110, 160].astype(int)
    unphotographed_colour = beauty[110, 340].astype(int)
    np.testing.assert_allclose(photographed_colour, unphotographed_colour, atol=6)
    # The authored baseColorFactor does not survive colour conversion: measuring
    # coverage by chasing the authored constant in the rendered image is wrong.
    assert not np.all(np.abs(unphotographed_colour - authored_grey) < 12), \
        "the authored grey mistakenly appears verbatim in the rendered output; thresholds measured pre-conversion are invalid"
    # The material-support pass separates them exactly.
    assert support[110, 160], "photographed face must be marked supported even when it renders grey"
    assert not support[110, 340], "a same-coloured face without a source photo must not count"


def test_real_blender_import_and_render_place_markers_at_known_pixels(tmp_path):
    blender = installed_blender()
    trimesh = pytest.importorskip("trimesh")
    camera = example_camera()
    view = blender_view(camera, (80, 0, 560, 480), (500, 500))
    targets = [(80, 70), (395, 73), (110, 380), (399, 410), (250, 240), (350, 240)]
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), None]
    scene = trimesh.Scene()
    transform = camera.room_to_camera
    position = -transform[:3, :3].T @ transform[:3, 3]
    for (u, v), color, depth in zip(targets, colors, (.7, 2, 3, 5, 2, 2)):
        room_vertices = []
        for dx, dy in ((-4, -4), (4, -4), (4, 4), (-4, 4)):
            # Invert the documented image crop/resize directly in original sensor pixels.
            pu = (u+dx+.5)*480/500-.5+80
            pv = (v+dy+.5)*480/500-.5
            ray = np.array([(pu-camera.cx)/camera.fx, (pv-camera.cy)/camera.fy, 1])
            room_vertices.append(position + transform[:3, :3].T @ (depth*ray))
        room_vertices = np.array(room_vertices)
        gltf_vertices = room_vertices[:, [0, 2, 1]] * [1, 1, -1]
        material = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.new("RGB", (2, 2), color) if color else None,
                                                       metallicFactor=0, roughnessFactor=1, doubleSided=True)
        visual = trimesh.visual.TextureVisuals(uv=[[0, 0], [1, 0], [1, 1], [0, 1]], material=material)
        scene.add_geometry(trimesh.Trimesh(gltf_vertices, [[0, 1, 2], [0, 2, 3]], visual=visual, process=False))

    def unlit(tree):
        tree.setdefault("extensionsUsed", []).append("KHR_materials_unlit")
        for material in tree["materials"]:
            material.setdefault("extensions", {})["KHR_materials_unlit"] = {}

    glb = tmp_path/"fixture.glb"
    glb.write_bytes(trimesh.exchange.gltf.export_glb(scene, tree_postprocessor=unlit))
    plan = tmp_path/"plan.json"
    plan.write_text(json.dumps({"glb": str(glb), "views": [view]}))
    script = Path(standardphysics_pipeline.__file__).parent/"blender_scripts/render_calibrated_mesh.py"
    result = subprocess.run([str(blender), "-b", "--factory-startup", "--python-exit-code", "1",
                             "-P", str(script), "--", str(plan)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-2500:] + result.stderr[-2500:]
    assert "CALIBRATED_MESH_RENDER_COMPLETE" in result.stdout
    image = np.asarray(Image.open(tmp_path/"oblique_candidate.png").convert("RGB"))
    for (u, v), color in zip(targets, colors):
        if color is None:
            continue
        channels = np.asarray(color) > 0
        mask = np.all(np.where(channels, image > 220, image < 20), axis=-1)
        ys, xs = np.nonzero(mask)
        assert len(xs) > 20
        assert abs(xs.mean()-u) < .75
        assert abs(ys.mean()-v) < .75
    geometry = np.asarray(Image.open(tmp_path/"oblique_geometry.png").convert("L")) > 128
    photo_support = np.asarray(Image.open(tmp_path/"oblique_photo_support.png").convert("L")) > 128
    for (u, v), color in zip(targets, colors):
        assert geometry[v, u]
        assert photo_support[v, u] == (color is not None)
    assert not geometry[15, 15]
    assert not photo_support[15, 15]
    report = json.loads((tmp_path/"camera-verification.json").read_text())
    assert report["views"][0]["max_error_px"] < .01
