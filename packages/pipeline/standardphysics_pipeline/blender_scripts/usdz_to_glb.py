"""Runs inside Blender. Converts a scanned USDZ into a GLB the viewer can select.

    blender --background --python usdz_to_glb.py -- \
        --usdz room.usdz --map metadata.json --out scene.glb

The scanned mesh looks like the real room, which box geometry never will. The
catch is identity: USD prim names cannot hold a UUID, so RoomPlan ships a
mapping file alongside the export and every object has to be renamed through it
before the viewer can tie a mesh back to the object a check measured.

Anything the map does not cover keeps its imported name and is reported as
unmapped rather than quietly renamed to something plausible.
"""

import argparse
import json
import plistlib
import re
import sys
from typing import Any

import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--usdz", required=True)
    parser.add_argument("--map", required=False)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def load_map(path):
    """RoomPlan's mapping file, whichever way it was written.

    Lane A names it `.metadata.json`, but a real export is a **binary plist**:
    it starts with `bplist00` and json.loads throws on it. Accept either rather
    than depending on a file extension telling the truth.
    """
    if not path:
        return {}
    data = open(path, "rb").read()
    if data[:8] == b"bplist00":
        raw = plistlib.loads(data)
    else:
        raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("mapping file was not a dictionary of names to ids")
    return {str(key): str(value) for key, value in raw.items()}


BLENDER_SUFFIX = re.compile(r"\.\d{3}$")


def lookup(mapping, name):
    """Find a name's element, ignoring Blender's duplicate suffix.

    A real export carries each element twice — once parametric, once as mesh —
    so the second import of `Chair0` becomes `Chair0.001`. That suffix is
    Blender disambiguating, not part of the USD name, and the mapping file
    knows nothing about it. On a phone scan it cost half the identities.
    """
    if name in mapping:
        return mapping[name]
    return mapping.get(BLENDER_SUFFIX.sub("", name))


def rename_through(mapping):
    """Rename in two passes so a target name colliding with a not-yet-renamed
    object cannot make Blender append .001 and lose the identity."""
    staged: list[tuple[Any, str]] = []
    for obj in list(bpy.data.objects):
        target = lookup(mapping, obj.name)
        if target:
            obj.name = "__staged__%d" % len(staged)
            staged.append((obj, target))

    # Only geometry needs identity. A USD scene is full of grouping nodes
    # (Object_grp, Section_grp, the room itself) that import as empties and
    # carry nothing a check could ever reason about, so counting them as
    # unmapped makes a clean conversion look broken.
    unmapped = [
        o.name
        for o in bpy.data.objects
        if not o.name.startswith("__staged__") and o.type == "MESH"
    ]
    for obj, target in staged:
        obj.name = target
        if obj.data is not None:
            obj.data.name = target
    return len(staged), unmapped


def main():
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.usd_import(filepath=args.usdz)

    imported = len(bpy.data.objects)
    renamed, unmapped = rename_through(load_map(args.map))

    bpy.ops.export_scene.gltf(
        filepath=args.out,
        export_format="GLB",
        export_apply=True,
        export_extras=True,
        export_yup=True,
    )
    meshes = len([o for o in bpy.data.objects if o.type == "MESH"])
    print("USDZ_CONVERTED imported=%d meshes=%d renamed=%d unmapped=%d"
          % (imported, meshes, renamed, len(unmapped)))
    for name in unmapped[:10]:
        print("UNMAPPED %s" % name)


if __name__ == "__main__":
    main()
