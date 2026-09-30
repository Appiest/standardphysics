"""Deterministic unlit rasterizer for photo-mesh GLBs.

Renders a trimesh-loaded photo-mesh scene (per-camera textured geometries plus
neutral unphotographed geometry) at arbitrary camera projections in the GLTF
frame, with perspective-correct barycentric UV interpolation and bilinear
texture sampling, exactly as an unlit triangle renderer must. This is the
evaluation renderer for benchmark moffett-photo-mesh-500; it shares the
pipeline camera math and produces lossless PNG passes.

Why not Blender: headless Blender 4.1.1 on this host rendered non-axis-aligned
cameras at wrong locations (empirically demonstrated in
runs/moffett/photo-mesh-500/r001/stageB/harness-self-test; dot markers at known
world points landed at pixels contradicting the camera matrix read-back).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from numba import njit
from PIL import Image

NEUTRAL = np.array([158, 153, 148], dtype=np.uint8)


# The one place in this repository that keeps its branches. Numba compiles this
# whole function to machine code, and every helper split out of it would have to
# be @njit too, called across a boundary the compiler is no longer free to
# inline: a per-pixel inner loop is exactly where that costs. Splitting it is a
# performance change wearing a readability change, and it is not one this
# checkout can measure, because numba is not installed here.
@njit(cache=True)
def raster_frame(vertices, faces, uvs, texture_index, textures, palette,  # noqa: C901
                 rot, trans, fx, fy, cx, cy, width, height, mode,
                 vcolors=None, color_index=None):
    count = len(vertices)
    proj_u = np.empty(count, dtype=np.float32)
    proj_v = np.empty(count, dtype=np.float32)
    proj_d = np.empty(count, dtype=np.float32)
    for i in range(count):
        vx, vy, vz = vertices[i, 0], vertices[i, 1], vertices[i, 2]
        x = vx * rot[0, 0] + vy * rot[0, 1] + vz * rot[0, 2] + trans[0]
        y = vx * rot[1, 0] + vy * rot[1, 1] + vz * rot[1, 2] + trans[1]
        z = vx * rot[2, 0] + vy * rot[2, 1] + vz * rot[2, 2] + trans[2]
        if z > 0.15 and z != 0.0:
            proj_u[i] = fx * x / z + cx
            proj_v[i] = fy * y / z + cy
            proj_d[i] = z
        else:
            proj_u[i] = np.nan
            proj_v[i] = np.nan
            proj_d[i] = np.nan

    colour = np.zeros((height, width, 3), dtype=np.float32)
    depth = np.full((height, width), np.inf, dtype=np.float32)
    face_buffer = np.full((height, width), -1, dtype=np.int32)
    texi = np.zeros((height, width), dtype=np.int32)
    uv_buffer = np.zeros((height, width, 2), dtype=np.float32)

    face_count = len(faces)
    for f in range(face_count):
        a, b, c = faces[f]
        if not (np.isfinite(proj_d[a]) and np.isfinite(proj_d[b]) and np.isfinite(proj_d[c])):
            continue
        ua, ub, uc = proj_u[a], proj_u[b], proj_u[c]
        va, vb, vc = proj_v[a], proj_v[b], proj_v[c]
        denom = (vb - vc) * (ua - uc) + (uc - ub) * (va - vc)
        if abs(denom) < 1e-10:
            continue
        x0 = max(0, int(floor3(ua, ub, uc)))
        x1 = min(width - 1, int(ceil3(ua, ub, uc)))
        y0 = max(0, int(floor3(va, vb, vc)))
        y1 = min(height - 1, int(ceil3(va, vb, vc)))
        if x0 > x1 or y0 > y1:
            continue
        inv_denom = 1.0 / denom
        da, db, dc = proj_d[a], proj_d[b], proj_d[c]
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                n_a = (vb - vc) * (x - uc) + (uc - ub) * (y - vc)
                n_b = (vc - va) * (x - uc) + (ua - uc) * (y - vc)
                wa = n_a * inv_denom
                wb = n_b * inv_denom
                wc = 1.0 - wa - wb
                if wa < -1e-6 or wb < -1e-6 or wc < -1e-6:
                    continue
                zinv = wa / da + wb / db + wc / dc
                if zinv <= 0:
                    continue
                z = 1.0 / zinv
                if z >= depth[y, x]:
                    continue
                depth[y, x] = z
                ti = texture_index[f]
                ci = color_index[f] if color_index is not None else -1
                texi[y, x] = ti
                uvh = (wa * uvs[a, 0] / da + wb * uvs[b, 0] / db + wc * uvs[c, 0] / dc) / zinv
                uvv = (wa * uvs[a, 1] / da + wb * uvs[b, 1] / db + wc * uvs[c, 1] / dc) / zinv
                uv_buffer[y, x, 0] = uvh
                uv_buffer[y, x, 1] = uvv
                face_buffer[y, x] = f
                if mode == 0:
                    if ti >= 0:
                        value = sample_texture(textures, ti, uvh, uvv)
                        colour[y, x, 0] = value[0]
                        colour[y, x, 1] = value[1]
                        colour[y, x, 2] = value[2]
                    elif ci >= 0:
                        colours_a = vcolors[ci]
                        colour[y, x, 0] = wa * colours_a[a, 0] + wb * colours_a[b, 0] + wc * colours_a[c, 0]
                        colour[y, x, 1] = wa * colours_a[a, 1] + wb * colours_a[b, 1] + wc * colours_a[c, 1]
                        colour[y, x, 2] = wa * colours_a[a, 2] + wb * colours_a[b, 2] + wc * colours_a[c, 2]
                    else:
                        colour[y, x, 0] = 158.0
                        colour[y, x, 1] = 153.0
                        colour[y, x, 2] = 148.0
                elif mode == 2:
                    colour[y, x] = palette[ti % len(palette)]
                elif mode == 3:
                    grey = (ti >= 0) or (ci >= 0)
                    colour[y, x, 0] = 255.0 * grey
                    colour[y, x, 1] = 255.0 * grey
                    colour[y, x, 2] = 255.0 * grey
    for y in range(height):
        for x in range(width):
            if np.isfinite(depth[y, x]):
                if mode == 1:
                    colour[y, x] = 255.0
    return colour, depth, face_buffer, texi, uv_buffer


@njit(cache=True)
def floor3(a, b, c):
    m = a
    if b < m:
        m = b
    if c < m:
        m = c
    return m


@njit(cache=True)
def ceil3(a, b, c):
    m = a
    if b > m:
        m = b
    if c > m:
        m = c
    return m


@njit(cache=True)
def sample_texture(textures, ti, uvh, uvv):
    if ti < 0:
        return 158.0, 153.0, 148.0
    tex = textures[ti]
    th, tw = tex.shape[0], tex.shape[1]
    u = uvh * tw - 0.5
    v = uvv * th - 0.5
    if u < 0.0:
        u = 0.0
    if u > tw - 1.001:
        u = tw - 1.001
    if v < 0.0:
        v = 0.0
    if v > th - 1.001:
        v = th - 1.001
    x0 = int(u)
    y0 = int(v)
    fu = u - x0
    fv = v - y0
    x1 = x0 + 1 if x0 + 1 < tw else x0
    y1 = y0 + 1 if y0 + 1 < th else y0
    w00 = (1 - fu) * (1 - fv)
    w10 = fu * (1 - fv)
    w01 = (1 - fu) * fv
    w11 = fu * fv
    r = w00 * tex[y0, x0, 0] + w10 * tex[y0, x1, 0] + w01 * tex[y1, x0, 0] + w11 * tex[y1, x1, 0]
    g = w00 * tex[y0, x0, 1] + w10 * tex[y0, x1, 1] + w01 * tex[y1, x0, 1] + w11 * tex[y1, x1, 1]
    b = w00 * tex[y0, x0, 2] + w10 * tex[y0, x1, 2] + w01 * tex[y1, x0, 2] + w11 * tex[y1, x1, 2]
    return r, g, b


def scene_arrays(glb_path: Path):
    """Scene arrays straight from the GLB chunks, in document order.

    The higher-level mesh loader reshuffles these multi-mesh soup GLBs, so this
    parser reads POSITION/indices/TEXCOORD_0 and textures directly.
    """
    from glb_colors import glb_vertex_colors
    from glb_scene import parse_scene

    parsed = parse_scene(glb_path)
    glb_colour_pages = glb_vertex_colors(glb_path)
    vertex_parts, face_parts, uv_parts = [], [], []
    tex_index = []
    color_index = []
    textures = []
    vcolors = []
    names = []
    colour_cursor = 0
    running_vertices = 0
    for mesh_index, (vertices_own, faces_own, uv_own, image) in enumerate(parsed):
        if image is not None:
            index = len(textures)
            textures.append(image)
        else:
            index = -1
        colour_index_value = -1
        if image is None:
            if colour_cursor < len(glb_colour_pages) and \
                    glb_colour_pages[colour_cursor].shape[0] >= len(vertices_own):
                colour_index_value = len(vcolors)
                vcolors.append(np.ascontiguousarray(glb_colour_pages[colour_cursor][:len(vertices_own)] * 255.0, dtype=np.float32))
                colour_cursor += 1
        offset = running_vertices
        vertex_parts.append(vertices_own.astype(np.float32))
        running_vertices += len(vertices_own)
        face_parts.append(faces_own.astype(np.int32) + np.int32(offset))
        uv_parts.append(uv_own.astype(np.float32) if len(uv_own) == len(vertices_own) else np.zeros((len(vertices_own), 2), dtype=np.float32))
        tex_index.append(np.full(len(faces_own), index, dtype=np.int32))
        color_index.append(np.full(len(faces_own), colour_index_value, dtype=np.int32))
        names.append(f"geometry_{mesh_index}")
    vertices = np.concatenate(vertex_parts)
    faces = np.concatenate(face_parts)
    uvs = np.concatenate(uv_parts)
    texture_index = np.concatenate(tex_index)
    colours_index = np.concatenate(color_index)
    palette = (np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 0], [255, 0, 255],
                         [0, 255, 255], [128, 0, 255], [255, 128, 0]]) * 0.75).astype(np.float32)
    return vertices, faces, uvs, texture_index, textures, palette, names, vcolors, colours_index


def render_view(arrays, camera, width, height, modes, out_paths):
    vertices, faces, uvs, texture_index, textures, palette, names, vcolors, colours_index = arrays[:9]
    rot = camera.scene_to_camera[:3, :3].astype(np.float32)
    trans = camera.scene_to_camera[:3, 3].astype(np.float32)
    outputs = {}
    sentinel_textures = textures if textures else [np.zeros((1, 1, 3), dtype=np.uint8)]
    sentinel_vcolors = vcolors if vcolors else [np.zeros((1, 3), dtype=np.float32)]
    for mode, out_path in zip(modes, out_paths, strict=False):
        colour, depth, face_buffer, texi, uv_buffer = raster_frame(
            vertices, faces, uvs, texture_index, sentinel_textures, palette,
            rot, trans, np.float32(camera.fx), np.float32(camera.fy),
            np.float32(camera.cx), np.float32(camera.cy), width, height, mode,
            sentinel_vcolors, colours_index)
        Image.fromarray(colour.astype(np.uint8), "RGB").save(out_path)
        outputs[mode] = {"depth": depth, "face_buffer": face_buffer, "texi": texi, "uv": uv_buffer}
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.views.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    arrays = scene_arrays(args.glb)
    manifest = {"glb": str(args.glb), "views": []}
    from standardphysics_pipeline.render_efficiency.photo_mesh_500 import build_frozen_camera
    for view in spec["views"]:
        camera = build_frozen_camera(view)
        passes = ["rgb", "geometry", "source_id", "coverage"]
        paths = [args.out / f"{view['id']}-{name}.png" for name in passes]
        render_view(arrays, camera, view["width"], view["height"], [0, 1, 2, 3], paths)
        manifest["views"].append({"id": view["id"], "width": view["width"], "height": view["height"],
                                  "passes": {name: str(path) for name, path in zip(passes, paths, strict=True)}})
    (args.out / "raster-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("RASTER_DONE", len(spec["views"]))


if __name__ == "__main__":
    main()
