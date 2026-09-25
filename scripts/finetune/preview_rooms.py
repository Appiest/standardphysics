"""Draw generated rooms from above as one contact sheet, for judging variety by eye."""

from __future__ import annotations

import argparse
import math
import pathlib

from PIL import Image, ImageDraw
from shop_generator import generate
from standardphysics_contracts import Scenario, SceneGraph
from standardphysics_pipeline import footprint

TILE = 420
MARGIN = 24
COLOURS = {
    "floor": (236, 233, 226), "wall": (40, 40, 44), "door": (230, 120, 40), "chair": (86, 132, 196),
    "stool": (86, 132, 196), "bench": (86, 132, 196), "sofa": (86, 132, 196), "table": (170, 120, 70),
    "counter": (60, 150, 110), "column": (110, 110, 110), "refrigerator": (150, 190, 210),
}
OTHER = (190, 160, 200)
ROUTE = (220, 60, 60)


def _extent(graph: SceneGraph) -> tuple[float, float, float, float]:
    points = [point for node in graph.nodes if node.kind in ("floor", "wall") for point in footprint(node)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _draw_room(graph: SceneGraph, scenario: Scenario, title: str) -> Image.Image:
    tile = Image.new("RGB", (TILE, TILE), (250, 250, 248))
    pen = ImageDraw.Draw(tile)
    x0, y0, x1, y1 = _extent(graph)
    scale = (TILE - 2 * MARGIN) / max(x1 - x0, y1 - y0)

    def to_px(point: tuple[float, float]) -> tuple[float, float]:
        return MARGIN + (point[0] - x0) * scale, TILE - MARGIN - (point[1] - y0) * scale

    layers = sorted(graph.nodes, key=lambda n: ("floor", "wall", "door", "object").index(n.kind)
                    if n.kind in ("floor", "wall", "door", "object") else 9)
    for node in layers:
        colour = COLOURS.get(node.kind) or COLOURS.get(node.raw_category, OTHER)
        pen.polygon([to_px(p) for p in footprint(node)], fill=colour, outline=(60, 60, 60) if
                    node.kind == "object" else None)
    stops = [to_px((s.position.x, s.position.y)) for s in scenario.stops]
    pen.line(stops, fill=ROUTE, width=2)
    for x, y in stops:
        pen.ellipse((x - 4, y - 4, x + 4, y + 4), fill=ROUTE)
    area = sum(n.dimensions.x * n.dimensions.y for n in graph.nodes if n.kind == "floor")
    pieces = sum(1 for n in graph.nodes if n.kind == "object")
    pen.text((8, 6), f"{title}  {area:.0f} m2, {pieces} pieces", fill=(20, 20, 20))
    return tile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("runs/finetune/shop_generator_preview.png"))
    args = parser.parse_args()
    columns = 6
    rows = math.ceil(args.count / columns)
    sheet = Image.new("RGB", (columns * TILE, rows * TILE), (255, 255, 255))
    for offset in range(args.count):
        index = args.start + offset
        graph, scenario, shop = generate(index)
        sheet.paste(_draw_room(graph, scenario, f"#{index} {shop.name}"),
                    ((offset % columns) * TILE, (offset // columns) * TILE))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.out)
    print(args.out)


if __name__ == "__main__":
    main()
