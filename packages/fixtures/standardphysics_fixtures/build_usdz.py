"""Builds shop.usdz from the fixture graph. Run with Blender, not python.

    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python packages/fixtures/standardphysics_fixtures/build_usdz.py

USD prim names must be alphanumeric-plus-underscore and cannot start with a
digit, so a UUID cannot be one: the hyphens get stripped and the names collide.
Prims are named `n_<uuid hex>` and `shop.node_map.json` maps each prim name
back to its canonical node ID.

This is the same problem RoomPlan solves with the metadataURL mapping file, and
the reason Lane A must capture it.
"""

from __future__ import annotations

import json
import pathlib

import bpy

HERE = pathlib.Path(__file__).parent
DATA = HERE / "data"


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def add_box(name: str, center: tuple[float, float, float],
            dims: tuple[float, float, float]) -> None:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.name = name
    obj.scale = dims


def prim_name(node_id: str) -> str:
    return "n_" + node_id.replace("-", "")


def main() -> None:
    graph = json.loads((DATA / "shop.scene_graph.json").read_text())
    clear_scene()
    node_map = {}
    for node in graph["nodes"]:
        transform = node["transform"]["m"]
        center = (transform[3], transform[7], transform[11])
        dims = (node["dimensions"]["x"], node["dimensions"]["y"],
                node["dimensions"]["z"])
        name = prim_name(node["id"])
        node_map[name] = node["id"]
        add_box(name, center, dims)

    out = DATA / "shop.usdz"
    bpy.ops.wm.usd_export(filepath=str(out))
    (DATA / "shop.node_map.json").write_text(json.dumps(node_map, indent=2) + "\n")
    print(f"USDZ_WRITTEN {out} objects={len(graph['nodes'])}")


if __name__ == "__main__":
    main()
