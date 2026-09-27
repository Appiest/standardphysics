"""Generated materials for whatever part of a room no photo reached.

A surface no camera saw used to keep one flat colour, so a room read as photo
patches floating on blank sheet. Here every unphotographed point that belongs
to a wall, a floor or a labelled object takes a repeating material for what it
is, sampled at its real position so the pattern keeps its physical size, and
tinted to the median colour the photos did record on that same thing so the
fill meets the photo without a step in brightness.

Materials are keyed by what a surface is: "wall", "floor", or an object's label
in lower case ("chair", "television", "outlet"). The generic set ships with the
package; a room may carry its own set, generated from its own photos, which
overrides the generic one key by key.

Display only. Coverage is never touched: a filled point is a picture of what
the surface probably looks like, and coverage keeps reporting what a camera saw.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, stands_upright

from .project import to_linear, to_srgb
from .regions import VertexIndex
from .scan_colour import ColouredScan, vertex_normals

MATERIALS_DIR = pathlib.Path(__file__).parent / "materials"
MANIFEST = "materials.json"

SURFACE_REACH = 0.10
"""How far off a measured sheet, in metres, a scanned vertex may sit and still belong to it."""
OBJECT_REACH = 0.03
"""How far outside a labelled object's box a scanned vertex may sit and still belong to it."""
FACING_THE_SHEET = 0.8
"""How nearly a vertex must face along the sheet's normal to belong to it."""
MIN_SEEN_FOR_TINT = 50
"""Photographed points a surface needs before its own colour sets the tint."""


@dataclass(frozen=True)
class SurfaceMaterial:
    tile: np.ndarray
    """Linear RGB, float32, height x width x 3, repeating seamlessly in both directions."""
    metres_across: float
    """The real width one repetition of the tile covers."""
    tint: bool = True
    """Whether to move the tile onto the photographed colour of what it fills.

    A generic tile is a neutral grey and must be tinted. A room's own tile was
    generated from its own photo and already has the right colour; tinting it
    to a median dragged dark by a reflection or a poster only makes it wrong.
    """

    @property
    def median(self) -> np.ndarray:
        return np.median(self.tile.reshape(-1, 3), axis=0)


def material_key(node: SceneNode) -> str:
    """What a node is, as far as choosing a material goes.

    Discovery names things like "Outlet (electrical outlet)" or "Candidate outlet
    (power outlet)"; both are an outlet, and look like the one RoomPlan calls
    "Outlet", so the gloss in brackets and the word "candidate" are dropped.
    """
    if _is_room_sheet(node):
        return "wall" if stands_upright(node) else "floor"
    name = node.label.split("(")[0].strip().lower()
    return name.removeprefix("candidate ").strip()


def _manifest(directory: pathlib.Path) -> dict:
    path = directory / MANIFEST
    return json.loads(path.read_text()) if path.is_file() else {}


def _material_from(directory: pathlib.Path, entry: dict) -> SurfaceMaterial:
    with Image.open(directory / entry["image"]) as opened:
        srgb = np.asarray(opened.convert("RGB"), dtype=np.float32) / 255.0
    return SurfaceMaterial(to_linear(srgb).astype(np.float32), float(entry["metres_across"]), bool(entry.get("tint", True)))


def load_materials(*directories: pathlib.Path | None) -> dict[str, SurfaceMaterial]:
    """Every material in the given folders; a later folder overrides an earlier one key by key.

    With no folders given, the generic set that ships with the package. A folder
    without a manifest contributes nothing rather than failing.
    """
    materials: dict[str, SurfaceMaterial] = {}
    for directory in directories or (MATERIALS_DIR,):
        if directory is None:
            continue
        for key, entry in _manifest(directory).items():
            materials[key] = _material_from(directory, entry)
    return materials


def room_materials(room_directory: pathlib.Path | None) -> dict[str, SurfaceMaterial]:
    """The generic set with a room's own materials laid over it."""
    return load_materials(MATERIALS_DIR, room_directory)


def materials_digest(directory: pathlib.Path | None) -> str:
    """A fingerprint of a room's own materials, so a build made before they changed is not reused."""
    if directory is None or not (directory / MANIFEST).is_file():
        return ""
    digest = hashlib.sha256((directory / MANIFEST).read_bytes())
    for entry in sorted(_manifest(directory).values(), key=lambda entry: entry["image"]):
        digest.update((directory / entry["image"]).read_bytes())
    return digest.hexdigest()


def _is_room_sheet(node: SceneNode) -> bool:
    """A wall or floor; a door or window is cut into one and is not its own surface."""
    return bounds_the_room(node) and node.parent_id is None


def _node_frame(node: SceneNode) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    return matrix[:3, :3], matrix[:3, 3], half


def _inside(node: SceneNode, vertices: np.ndarray, reach: float) -> np.ndarray:
    rotation, centre, half = _node_frame(node)
    local = (vertices - centre) @ rotation
    return np.all(np.abs(local) <= half + reach, axis=1)


def _on_sheet(node: SceneNode, vertices: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Vertices inside the sheet's padded box that face the room from it.

    A wall is seen from either side, so either facing counts. A floor is only
    ever its top: a vertex facing down inside a floor's box is the underside of
    something standing on it.
    """
    within = _inside(node, vertices, SURFACE_REACH)
    if stands_upright(node):
        rotation, _, half = _node_frame(node)
        sheet_normal = rotation[:, int(np.argmin(half))]
        return within & (np.abs(normals @ sheet_normal) >= FACING_THE_SHEET)
    return within & (normals[:, 2] >= FACING_THE_SHEET)


def room_owners(
    vertices: np.ndarray, normals: np.ndarray, graph: SceneGraph, patches: np.ndarray | None = None,
) -> np.ndarray:
    """Index of the graph node each scanned vertex lies on, or -1 when it lies on none.

    The graph and the scan share the room frame, Z up. Objects claim first, so a
    television or whiteboard on a wall is itself and not the wall behind it. A
    vertex belongs to an object when it sits inside the object's box, and to a
    sheet when it sits inside the sheet's box, padded because a wall is measured
    as a plane of no thickness, and faces along the sheet's normal, so the side
    of a cabinet pressed against a wall is not taken for the wall. Room graphs
    carry no ceiling, so a ceiling belongs to nothing. `patches` marks vertices
    that patch a hole in a wall or floor; they are that sheet by construction, so
    the floor patched under a chair never takes the chair's fabric. Each node
    tests only the vertices around its own box, since testing a merged floor's
    millions of vertices against every one of its hundreds of nodes took minutes.
    """
    owners = np.full(len(vertices), -1, dtype=np.int32)
    claimable = ~patches if patches is not None else np.ones(len(vertices), dtype=bool)
    objects = [(index, node) for index, node in enumerate(graph.nodes) if not _is_room_sheet(node)]
    sheets = [(index, node) for index, node in enumerate(graph.nodes) if _is_room_sheet(node)]
    regions = VertexIndex(vertices)
    for index, node in objects:
        near = regions.near_box(node, OBJECT_REACH)
        owners[near[_inside(node, vertices[near], OBJECT_REACH) & (owners[near] < 0) & claimable[near]]] = index
    for index, node in sheets:
        near = regions.near_box(node, SURFACE_REACH)
        owners[near[_on_sheet(node, vertices[near], normals[near]) & (owners[near] < 0)]] = index
    return owners


def _wrapped_bilinear(tile: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    height, width = tile.shape[:2]
    x, y = np.mod(u, width), np.mod(v, height)
    # A hair below zero wraps to exactly `width` in float32, so the pixel index wraps too.
    x0, y0 = np.floor(x).astype(np.int64) % width, np.floor(y).astype(np.int64) % height
    x1, y1 = (x0 + 1) % width, (y0 + 1) % height
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = tile[y0, x0] * (1 - fx) + tile[y0, x1] * fx
    bottom = tile[y1, x0] * (1 - fx) + tile[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def planar_sample(material: SurfaceMaterial, positions: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """The tile laid flat across whichever room axis each point faces, in linear RGB.

    Walls take the tile across their horizontal run and up their height, and
    floors across X and Y, so a floorboard is the same size wherever it lands.
    """
    facing_axis = np.argmax(np.abs(normals), axis=1)
    across = np.where(facing_axis == 0, positions[:, 1], positions[:, 0])
    along = np.where(facing_axis == 2, positions[:, 1], positions[:, 2])
    pixels_per_metre = material.tile.shape[1] / material.metres_across
    return _wrapped_bilinear(material.tile, across * pixels_per_metre, -along * pixels_per_metre)


def tinted_to(pattern: np.ndarray, target: np.ndarray, pattern_median: np.ndarray) -> np.ndarray:
    """The pattern with its median moved onto the target colour, keeping its texture."""
    return np.clip(pattern * (target / np.maximum(pattern_median, 1e-4)), 0.0, 1.0)


def _median_of(colours: np.ndarray) -> np.ndarray | None:
    return np.median(colours, axis=0) if len(colours) >= MIN_SEEN_FOR_TINT else None


@dataclass(frozen=True)
class MaterialFill:
    """Which material each owner takes, and the materials themselves."""

    owner_keys: list[str]
    """The material key of each owner, indexed the way the owners array indexes them."""
    materials: dict[str, SurfaceMaterial]

    @classmethod
    def for_graph(cls, graph: SceneGraph, materials: dict[str, SurfaceMaterial]) -> MaterialFill:
        return cls([material_key(node) for node in graph.nodes], materials)

    def _tint_targets(self, colours: np.ndarray, photographed: np.ndarray, owners: np.ndarray) -> dict[int, np.ndarray]:
        """Each owner's photographed colour, falling back to the colour of everything sharing its key."""
        by_key: dict[str, np.ndarray] = {}
        for key in set(self.owner_keys):
            same_key = np.isin(owners, [i for i, k in enumerate(self.owner_keys) if k == key])
            colour = _median_of(colours[same_key & photographed])
            if colour is not None:
                by_key[key] = colour
        targets = {}
        for owner in np.unique(owners[owners >= 0]):
            own = _median_of(colours[photographed & (owners == owner)])
            chosen = own if own is not None else by_key.get(self.owner_keys[owner])
            if chosen is not None:
                targets[int(owner)] = chosen
        return targets

    def apply(
        self, colours: np.ndarray, photographed: np.ndarray,
        positions: np.ndarray, normals: np.ndarray, owners: np.ndarray,
    ) -> np.ndarray:
        """Linear colours with every unphotographed point that has a material given it."""
        if not self.materials or photographed.all():
            return colours
        filled = colours.copy()
        targets = self._tint_targets(colours, photographed, owners)
        unphotographed = ~photographed & (owners >= 0)
        for owner in np.unique(owners[unphotographed]):
            material = self.materials.get(self.owner_keys[owner])
            if material is None:
                continue
            here = np.flatnonzero(unphotographed & (owners == owner))
            pattern = planar_sample(material, positions[here], normals[here])
            target = targets.get(int(owner), material.median) if material.tint else material.median
            filled[here] = tinted_to(pattern, target, material.median)
        return filled


def unseen_surfaces_filled(
    scan: ColouredScan, graph: SceneGraph, materials: dict[str, SurfaceMaterial]
) -> ColouredScan:
    """The scan with every unphotographed vertex on a wall, floor or labelled object given its material."""
    if not materials or scan.seen.all():
        return scan
    normals = vertex_normals(scan.vertices, scan.triangles)
    owners = room_owners(scan.vertices, normals, graph, patches=scan.sheet_patches)
    linear = to_linear(scan.colours).astype(np.float32)
    filled = MaterialFill.for_graph(graph, materials).apply(linear, scan.seen, scan.vertices, normals, owners)
    return replace(scan, colours=to_srgb(filled).astype(np.float32))
