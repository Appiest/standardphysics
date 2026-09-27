"""Runs inside Blender. Thins a painted scan and unwraps it, so photographs can be baked into an image.

    blender --background --python unwrap_scan.py -- --scan scan.npz --out unwrapped.npz

The input holds room-frame vertices and triangles. The output holds the thinned
mesh's vertices and triangles, a UV pair for every corner of every triangle,
since a vertex on a seam between two islands has a different UV on each side,
and which atlas each triangle is packed into.
"""

import argparse
import sys

import bmesh
import bpy
import numpy as np

MAX_TRIANGLES = 260_000
"""Above this the viewer stutters on a phone, so the mesh is thinned to fit."""
MARGIN_DIVISOR = 0.05
"""Space Blender leaves around each face, in its own relative units: two or three texels at 4096."""


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-triangles", type=int, default=MAX_TRIANGLES)
    parser.add_argument("--atlases", type=int, default=1)
    return parser.parse_args(argv)


def build(vertices: np.ndarray, triangles: np.ndarray):
    mesh = bpy.data.meshes.new("scan")
    mesh.from_pydata(vertices.tolist(), [], triangles.tolist())
    mesh.validate()
    obj = bpy.data.objects.new("scan", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


FLAT_EDGE_RADIANS = np.radians(10.0)
"""Two faces meeting at less than this are one flat stretch, where flipping their edge keeps the surface where it was."""
WELD_DISTANCE = 0.001
"""Vertices this close are one vertex: the shared corners of patch squares, and the seams between LiDAR anchors."""


def welded(obj) -> None:
    """Join coincident vertices, so the mesh thins as one surface rather than as loose pieces.

    Hole patches arrive as separate squares and the LiDAR as separate anchors.
    Thinned apart, each square collapsed on its own and a patched floor came out
    as a lattice with gaps; on one Moffitt walk that was 15 per cent of the surface.
    """
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    bmesh.ops.remove_doubles(mesh, verts=mesh.verts[:], dist=WELD_DISTANCE)
    mesh.to_mesh(obj.data)
    mesh.free()


def thin(obj, max_triangles: int) -> None:
    count = len(obj.data.polygons)
    if count <= max_triangles:
        return
    modifier = obj.modifiers.new("thin", "DECIMATE")
    modifier.ratio = max_triangles / count
    bpy.ops.object.modifier_apply(modifier="thin")


def triangulated(obj) -> None:
    """Triangles, with edges flipped wherever that makes them less thin.

    Thinning collapses a flat stretch, a patched floor most of all, into a fan
    of needles radiating from one vertex. A needle gets a strip of texture one
    texel wide, so the photograph smeared along it in streaks. Flipping the
    shared edge of two thin triangles into the other diagonal turns them into
    two fat ones over the same surface. Only edges between nearly coplanar
    faces are flipped, because flipping across a bend changes the shape.
    """
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    bmesh.ops.triangulate(mesh, faces=mesh.faces[:])
    flat = [edge for edge in mesh.edges if len(edge.link_faces) == 2 and edge.calc_face_angle(np.pi) < FLAT_EDGE_RADIANS]
    bmesh.ops.beautify_fill(mesh, faces=mesh.faces[:], edges=flat)
    mesh.to_mesh(obj.data)
    mesh.free()


def regions(obj, count: int) -> np.ndarray:
    """Which of `count` atlases each face goes to: the surface cut across its longer side, again and again, into stretches of equal faces.

    One atlas for a whole library floor left each face a texel or two, and the
    floor came out as flat-coloured shards. Neighbouring faces share an atlas,
    so each atlas holds one part of the floor at the density a single room gets.
    """
    centres = np.empty(len(obj.data.polygons) * 3, dtype=np.float64)
    obj.data.polygons.foreach_get("center", centres)
    labels = np.zeros(len(obj.data.polygons), dtype=np.int64)
    _split(centres.reshape(-1, 3)[:, :2], np.arange(len(labels)), 0, count, labels)
    return labels


def _split(centres: np.ndarray, faces: np.ndarray, first: int, count: int, labels: np.ndarray) -> None:
    if count == 1:
        labels[faces] = first
        return
    lower = count // 2
    axis = int(np.argmax(np.ptp(centres[faces], axis=0)))
    ordered = faces[np.argsort(centres[faces, axis], kind="stable")]
    cut = len(ordered) * lower // count
    _split(centres, ordered[:cut], first, lower, labels)
    _split(centres, ordered[cut:], first + lower, count - lower, labels)


def unwrap(obj, labels: np.ndarray) -> None:
    """Each atlas's faces packed on their own, at a size in proportion to their area.

    Charts that follow the surface are the usual choice, but a thinned LiDAR mesh
    is too noisy for them: it broke into so many tiny islands that the gaps
    between them took the atlas, and one texel covered eighteen centimetres.
    Packing faces one by one fills about a third of the atlas instead.
    """
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="FACE")
    for atlas in range(int(labels.max()) + 1):
        bpy.ops.mesh.select_all(action="DESELECT")
        mesh = bmesh.from_edit_mesh(obj.data)
        mesh.faces.ensure_lookup_table()
        for index in np.flatnonzero(labels == atlas):
            mesh.faces[index].select_set(True)
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.uv.lightmap_pack(PREF_CONTEXT="SEL_FACES", PREF_PACK_IN_ONE=True, PREF_MARGIN_DIV=MARGIN_DIVISOR)
    bpy.ops.object.mode_set(mode="OBJECT")


def corner_arrays(obj) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mesh = obj.data
    vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", vertices)
    corners = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", corners)
    uv = np.empty(len(mesh.loops) * 2, dtype=np.float64)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    return vertices.reshape(-1, 3), corners.reshape(-1, 3), uv.reshape(-1, 3, 2)


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    archive = np.load(args.scan)
    obj = build(archive["vertices"], archive["triangles"])
    welded(obj)
    thin(obj, args.max_triangles)
    triangulated(obj)
    atlases = regions(obj, args.atlases)
    unwrap(obj, atlases)
    vertices, triangles, uv = corner_arrays(obj)
    np.savez(args.out, vertices=vertices, triangles=triangles, uv=uv, atlases=atlases)
    print(f"SCAN_UNWRAPPED {len(triangles)}")


main()
