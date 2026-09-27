"""Runs inside Blender. Sets several RoomPlan exports on one floor and writes them as one usdz.

    blender --background --python scripts/merge_room_usdz.py -- \
        --usdz a.usdz --placement "16 floats" --usdz b.usdz --placement "16 floats" --out floor.usdz

A placement is row-major and in Blender's frame, which the importer reaches by
turning the export's Y up into Z up.
"""

import argparse
import sys

import bpy
from mathutils import Matrix


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--usdz", action="append", required=True)
    parser.add_argument("--placement", action="append", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if len(args.usdz) != len(args.placement):
        parser.error("every usdz needs exactly one placement")
    return args


def placed(path: str, placement: str) -> None:
    values = [float(value) for value in placement.split()]
    matrix = Matrix([values[0:4], values[4:8], values[8:12], values[12:16]])
    before = set(bpy.data.objects)
    bpy.ops.wm.usd_import(filepath=path)
    arrived = [obj for obj in bpy.data.objects if obj not in before]
    for obj in arrived:
        if obj.parent is None or obj.parent not in arrived:
            obj.matrix_world = matrix @ obj.matrix_world


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for path, placement in zip(args.usdz, args.placement):
        placed(path, placement)
    bpy.ops.wm.usd_export(filepath=args.out)
    print("FLOOR_USDZ_WRITTEN")


main()
