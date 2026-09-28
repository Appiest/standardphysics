"""Draw generated rooms from above as one contact sheet, for judging variety and facing by eye.

Doors are orange bars, partitions and walls dark lines, wall-mounted things
(grab bars, dispensers, menu boards) outlines only, and every seat carries a
short red tick on its front edge so a chair facing away from its table shows.
"""

from __future__ import annotations

import argparse
import math
import pathlib

from PIL import Image, ImageDraw
from shop_generator import generate
from standardphysics_agents.checks import roles
from standardphysics_contracts import Scenario, SceneGraph, SceneNode
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import floor_polygon

TILE = 480
MARGIN = 24
COLOURS = {
    "floor": (236, 233, 226), "chair": (86, 132, 196), "stool": (86, 132, 196), "bench": (86, 132, 196),
    "sofa": (86, 132, 196), "table": (170, 120, 70), "counter": (60, 150, 110), "column": (110, 110, 110),
    "refrigerator": (150, 190, 210), "toilet": (240, 240, 255), "sink": (200, 220, 240),
}
OTHER = (190, 160, 200)
WALL = (40, 40, 44)
DOOR = (230, 120, 40)
OPENING = (60, 140, 220)
MOUNTED = (200, 40, 120)
TICK = (220, 30, 30)
ROUTE = (220, 60, 60)
MOUNTED_ABOVE = 0.3


def _extent(graph: SceneGraph) -> tuple[float, float, float, float]:
    points = [point for node in graph.nodes if node.kind in ("floor", "wall") for point in _shape(node)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _shape(node: SceneNode) -> list[tuple[float, float]]:
    return floor_polygon(node) if node.kind == "floor" else footprint(node)


def _is_mounted(node: SceneNode) -> bool:
    return node.kind == "object" and node.transform.m[11] - node.dimensions.z / 2 > MOUNTED_ABOVE


def _from_scan(graph: SceneGraph) -> bool:
    return any(node.kind == "wall" and min(node.dimensions.x, node.dimensions.y) < 0.01 for node in graph.nodes)


class Sheet:
    def __init__(self, graph: SceneGraph) -> None:
        self.tile = Image.new("RGB", (TILE, TILE), (250, 250, 248))
        self.pen = ImageDraw.Draw(self.tile)
        self.x0, self.y0, x1, y1 = _extent(graph)
        self.scale = (TILE - 2 * MARGIN) / max(x1 - self.x0, y1 - self.y0)

    def px(self, point: tuple[float, float]) -> tuple[float, float]:
        return MARGIN + (point[0] - self.x0) * self.scale, TILE - MARGIN - (point[1] - self.y0) * self.scale

    def outline(self, node: SceneNode, colour, width: int = 1) -> None:
        points = [self.px(p) for p in _shape(node)]
        self.pen.line([*points, points[0]], fill=colour, width=width)

    def shell(self, node: SceneNode) -> None:
        if node.kind == "floor":
            self.pen.polygon([self.px(p) for p in _shape(node)], fill=COLOURS["floor"])
        elif node.kind == "wall":
            self.pen.polygon([self.px(p) for p in _shape(node)], fill=WALL, outline=WALL)
        else:
            self.outline(node, DOOR if node.kind == "door" else OPENING, 4)

    def piece(self, node: SceneNode) -> None:
        if _is_mounted(node):
            self.outline(node, MOUNTED)
            return
        colour = COLOURS.get(node.raw_category, OTHER)
        self.pen.polygon([self.px(p) for p in footprint(node)], fill=colour, outline=(60, 60, 60))
        if roles.is_seating(node) or node.raw_category in ("sofa", "toilet"):
            self.front_tick(node)

    def front_tick(self, node: SceneNode) -> None:
        yaw = math.atan2(node.transform.m[4], node.transform.m[0])
        front = yaw - math.pi / 2
        x, y = node.transform.m[3], node.transform.m[7]
        edge = (x + node.dimensions.y / 2 * math.cos(front), y + node.dimensions.y / 2 * math.sin(front))
        tip = (edge[0] + 0.25 * math.cos(front), edge[1] + 0.25 * math.sin(front))
        self.pen.line([self.px(edge), self.px(tip)], fill=TICK, width=3)


def _draw_room(graph: SceneGraph, scenario: Scenario, title: str) -> Image.Image:
    sheet = Sheet(graph)
    order = ("floor", "wall", "window", "opening", "door")
    for node in sorted((n for n in graph.nodes if n.kind in order), key=lambda n: order.index(n.kind)):
        sheet.shell(node)
    for node in sorted((n for n in graph.nodes if n.kind not in order), key=_is_mounted):
        sheet.piece(node)
    stops = [sheet.px((s.position.x, s.position.y)) for s in scenario.stops]
    sheet.pen.line(stops, fill=ROUTE, width=2)
    for x, y in stops:
        sheet.pen.ellipse((x - 4, y - 4, x + 4, y + 4), fill=ROUTE)
    area = sum(n.dimensions.x * n.dimensions.y for n in graph.nodes if n.kind == "floor")
    pieces = sum(1 for n in graph.nodes if n.kind == "object")
    source = "scan" if _from_scan(graph) else "rect"
    sheet.pen.text((8, 6), f"{title} ({source})  {area:.0f} m2, {pieces} pieces", fill=(20, 20, 20))
    return sheet.tile


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
