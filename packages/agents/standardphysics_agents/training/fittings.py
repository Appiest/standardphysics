"""Fitting edits: changing how high something is, swapping a piece for a catalog one, cutting in a lowered counter section.

No rearrangement makes a 47 inch counter meet ADA 2010 904.4.1, or a room of
bar-height tables meet the 5 percent of 226.1. These edits change what a piece
is rather than where it stands, and like wall shifts and fixture moves they are
construction: the checker remeasures the result exactly as it measures a scan.

A lowered section is cut from one end of a counter. The counter gives up that
length and the section takes its place at 36 inches, so the pair keeps the
counter's footprint and needs no new floor. Whatever stood on the cut part
drops onto the section with it, and `carry` sets a register or card reader from
elsewhere on the counter down on the section too, clear of the high part, since
904.4 wants people to pay where they can reach.
"""

from __future__ import annotations

import uuid
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import contains_point, footprint, gap_between
from standardphysics_pipeline.footprints import rotation_about_z

from ..checks import roles
from ..checks.walls import is_room_shell
from ..fix.moves import RESTING_GAP, floor_height, surface_under, top_of, underside
from .catalog import CATALOG, LOWERED_COUNTER_SECTION, CatalogItem

SECTION_NAMESPACE = uuid.UUID("7b3c1f04-5e2a-4c6b-9d18-000000000031")

MIN_TOP_INCHES = 15.0
MAX_TOP_INCHES = 84.0
COUNTER_TOP_INCHES = (28.0, 48.0)
TABLE_TOP_INCHES = (26.0, 44.0)
MOUNTED_TOP_INCHES = (MIN_TOP_INCHES, MAX_TOP_INCHES)
"""The tops a height change may set, by what the piece is used for.

A counter or a table is rebuilt within the heights furniture is sold at; a
mounted item may go anywhere between the low side reach of ADA 2010 308 and
the top of an ordinary wall.
"""

MIN_SECTION_INCHES = LOWERED_COUNTER_SECTION.length_inches
MAX_SECTION_INCHES = 72.0
MIN_COUNTER_LEFT_METERS = to_meters(12.0)
"""A section may not take the whole counter; lowering all of it is a height change."""
RESTING_TOLERANCE = 0.02
CARRY_MARGIN = 0.05
"""How far a carried item stays inside the section, so it never reads as standing on the high part."""
CARRY_STEP = 0.02
ITEM_GAP = 0.01

End = Literal["start", "end"]


class HeightChange(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    node_id: UUID
    top_inches: float = Field(ge=MIN_TOP_INCHES, le=MAX_TOP_INCHES)


class Replacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: UUID
    catalog_item: str

    @field_validator("catalog_item")
    @classmethod
    def from_the_catalog(cls, name: str) -> str:
        if name not in CATALOG:
            raise ValueError(f"{name} is not in the catalog")
        return name


class LoweredSection(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    counter_id: UUID
    end: End
    length_inches: float = Field(default=MIN_SECTION_INCHES, ge=MIN_SECTION_INCHES, le=MAX_SECTION_INCHES)
    carry: list[UUID] = Field(default_factory=list, max_length=4)


def _replace_nodes(graph: SceneGraph, changed: dict[UUID, SceneNode], added: list[SceneNode] = ()) -> SceneGraph:
    nodes = [changed.get(node.id, node) for node in graph.nodes] + list(added)
    return graph.model_copy(update={"nodes": nodes, "revision": graph.revision + 1, "base_hash": None})


def _raised(node: SceneNode, dz: float) -> SceneNode:
    m = list(node.transform.m)
    m[11] += dz
    return node.model_copy(update={"transform": Mat4(m=m)})


def rests_on(item: SceneNode, surface: SceneNode) -> bool:
    centre = (item.transform.position.x, item.transform.position.y)
    return (item.id != surface.id and not is_room_shell(item)
            and abs(underside(item) - top_of(surface)) <= RESTING_TOLERANCE
            and contains_point(footprint(surface), centre))


def _riders(graph: SceneGraph, surface: SceneNode, dz: float) -> dict[UUID, SceneNode]:
    """What stood on the surface, carried up or down with its top."""
    return {node.id: _raised(node, dz) for node in graph.nodes if rests_on(node, surface)}


def _lowered_section_ids(graph: SceneGraph) -> set[UUID]:
    return {node.id for node in roles.lowered_sections(graph)}


def use_of(graph: SceneGraph, node: SceneNode) -> roles.UsedFromTheFloor | None:
    """Whether a person pulls up to this piece to sit at it or to be served at it."""
    if node.id in _lowered_section_ids(graph):
        return "counter"
    return roles.used_from_the_floor(node)


def _stands_on_the_floor(graph: SceneGraph, node: SceneNode) -> bool:
    return underside(node) <= floor_height(graph) + RESTING_GAP


def _stands_on_a_piece(graph: SceneGraph, node: SceneNode) -> bool:
    """A register on a counter goes where the counter's top goes; it is not hung on its own."""
    floor_z = floor_height(graph)
    return surface_under(graph, node, floor_z) > floor_z


def height_range(graph: SceneGraph, node: SceneNode) -> tuple[float, float] | None:
    """The tops a height change may give this piece, or None when its height is not ours to change."""
    use = use_of(graph, node)
    if use == "counter":
        return COUNTER_TOP_INCHES
    if use == "surface":
        return TABLE_TOP_INCHES
    if is_room_shell(node) or _stands_on_the_floor(graph, node) or _stands_on_a_piece(graph, node):
        return None
    return MOUNTED_TOP_INCHES


def _with_top(graph: SceneGraph, node: SceneNode, top: float) -> SceneNode:
    """Floor-standing pieces are rebuilt taller or shorter; mounted ones are rehung."""
    if not _stands_on_the_floor(graph, node):
        return _raised(node, top - top_of(node))
    bottom = underside(node)
    size = node.dimensions
    height = top - bottom
    m = list(node.transform.m)
    m[11] = bottom + height / 2
    return node.model_copy(update={"dimensions": Vec3(x=size.x, y=size.y, z=height), "transform": Mat4(m=m)})


def _node(graph: SceneGraph, node_id: UUID, action: str) -> SceneNode:
    try:
        return graph.by_id(node_id)
    except KeyError:
        raise ValueError(f"a {action} names an object that is not in the room") from None


def change_height(graph: SceneGraph, change: HeightChange) -> SceneGraph:
    node = _node(graph, change.node_id, "height change")
    allowed = height_range(graph, node)
    if allowed is None or not allowed[0] <= change.top_inches <= allowed[1]:
        raise ValueError(f"{node.label} cannot be set to a {change.top_inches:g} in top")
    changed = _with_top(graph, node, to_meters(change.top_inches))
    riders = _riders(graph, node, top_of(changed) - top_of(node))
    return _replace_nodes(graph, {**riders, node.id: changed})


def _accepts(use: str | None, item: CatalogItem) -> bool:
    serves_counters = item.knee_clearance_inches is None
    return use == ("counter" if serves_counters else "surface")


def _catalog_size(node: SceneNode, item: CatalogItem, height: float) -> Vec3:
    length = to_meters(item.length_inches)
    depth = min(node.dimensions.x, node.dimensions.y) if item.depth_inches is None else to_meters(item.depth_inches)
    if node.dimensions.x >= node.dimensions.y:
        return Vec3(x=length, y=depth, z=height)
    return Vec3(x=depth, y=length, z=height)


def replace(graph: SceneGraph, replacement: Replacement) -> SceneGraph:
    """The piece swapped for the catalog item, in the same place and facing the same way."""
    node = _node(graph, replacement.node_id, "replacement")
    item = CATALOG[replacement.catalog_item]
    if not _accepts(use_of(graph, node), item):
        raise ValueError(f"{node.label} cannot be replaced by a {item.name}")
    bottom = underside(node)
    height = item.top_meters - bottom
    m = list(node.transform.m)
    m[11] = bottom + height / 2
    swapped = node.model_copy(update={"label": item.label, "dimensions": _catalog_size(node, item, height),
                                      "transform": Mat4(m=m)})
    riders = _riders(graph, node, top_of(swapped) - top_of(node))
    return _replace_nodes(graph, {**riders, node.id: swapped})


class _Frame:
    """A counter's own axes: `along` its length from the start end to the end end, `across` its depth."""

    def __init__(self, counter: SceneNode):
        cos_t, sin_t = rotation_about_z(counter)
        self.long_is_x = counter.dimensions.x >= counter.dimensions.y
        self.along = (cos_t, sin_t) if self.long_is_x else (-sin_t, cos_t)
        self.across = (-self.along[1], self.along[0])
        self.centre = (counter.transform.position.x, counter.transform.position.y)
        self.length = max(counter.dimensions.x, counter.dimensions.y)

    def coordinates(self, node: SceneNode) -> tuple[float, float]:
        dx, dy = node.transform.position.x - self.centre[0], node.transform.position.y - self.centre[1]
        return (dx * self.along[0] + dy * self.along[1], dx * self.across[0] + dy * self.across[1])

    def point(self, along: float, across: float) -> tuple[float, float]:
        return (self.centre[0] + self.along[0] * along + self.across[0] * across,
                self.centre[1] + self.along[1] * along + self.across[1] * across)

    def resized(self, node: SceneNode, length: float, along: float, bottom: float, height: float) -> tuple[Vec3, Mat4]:
        size = node.dimensions
        dims = Vec3(x=length, y=size.y, z=height) if self.long_is_x else Vec3(x=size.x, y=length, z=height)
        x, y = self.point(along, 0.0)
        m = list(node.transform.m)
        m[3], m[7], m[11] = x, y, bottom + height / 2
        return dims, Mat4(m=m)


def _placed_at(node: SceneNode, x: float, y: float, z: float) -> SceneNode:
    m = list(node.transform.m)
    m[3], m[7], m[11] = x, y, z
    return node.model_copy(update={"transform": Mat4(m=m)})


def _section_nodes(counter: SceneNode, section: LoweredSection) -> tuple[SceneNode, SceneNode, float]:
    """The shortened counter, the new section, and where the cut falls along the counter."""
    frame, length = _Frame(counter), to_meters(section.length_inches)
    if frame.length - length < MIN_COUNTER_LEFT_METERS:
        raise ValueError(f"{counter.label} is too short for a {section.length_inches:g} in section")
    sign = -1.0 if section.end == "start" else 1.0
    cut = sign * (frame.length / 2 - length)
    bottom = underside(counter)
    kept_dims, kept_at = frame.resized(counter, frame.length - length, -sign * length / 2, bottom,
                                       counter.dimensions.z)
    new_dims, new_at = frame.resized(counter, length, sign * (frame.length - length) / 2, bottom,
                                     LOWERED_COUNTER_SECTION.top_meters - bottom)
    shortened = counter.model_copy(update={"dimensions": kept_dims, "transform": kept_at})
    lowered = counter.model_copy(update={
        "id": uuid.uuid5(SECTION_NAMESPACE, f"{counter.id}:{section.end}"), "label": LOWERED_COUNTER_SECTION.label,
        "dimensions": new_dims, "transform": new_at, "quality": "measured", "parent_id": None, "relation": None,
        "measured_position": None,
    })
    return shortened, lowered, cut


def _on_the_cut_part(frame: _Frame, node: SceneNode, cut: float, sign: float) -> bool:
    return sign * (frame.coordinates(node)[0] - cut) > 0


def _slots(frame: _Frame, item: SceneNode, cut: float, sign: float) -> list[float]:
    """Positions along the counter for the item's centre, from the far end of the section toward the cut."""
    half = max(item.dimensions.x, item.dimensions.y) / 2
    far, near = sign * (frame.length / 2 - half - CARRY_MARGIN), cut + sign * (half + CARRY_MARGIN)
    count = int(abs(far - near) / CARRY_STEP) + 1 if sign * (far - near) >= 0 else 0
    return [far - sign * step * CARRY_STEP for step in range(count)]


def _set_down(frame: _Frame, item: SceneNode, cut: float, sign: float, top: float, taken: list[SceneNode]) -> SceneNode:
    across = frame.coordinates(item)[1]
    for along in _slots(frame, item, cut, sign):
        placed = _placed_at(item, *frame.point(along, across), top + item.dimensions.z / 2)
        if all(gap_between(footprint(placed), footprint(other)) > ITEM_GAP for other in taken):
            return placed
    raise ValueError(f"there is no room on the new section for the {item.label}")


def _carried(graph: SceneGraph, counter: SceneNode, section: LoweredSection) -> list[SceneNode]:
    sellers = {node.id for node in roles.point_of_sale(graph)}
    items = [_node(graph, node_id, "carry") for node_id in dict.fromkeys(section.carry)]
    for item in items:
        if item.id not in sellers or not rests_on(item, counter):
            raise ValueError(f"only a register or card reader on {counter.label} can be carried to its new section")
    return items


def add_lowered_section(graph: SceneGraph, section: LoweredSection) -> SceneGraph:
    counter = _node(graph, section.counter_id, "lowered section")
    if counter.id not in {node.id for node in roles.service_counters(graph)}:
        raise ValueError(f"{counter.label} is not a service counter")
    carried = _carried(graph, counter, section)
    shortened, lowered, cut = _section_nodes(counter, section)
    frame, sign = _Frame(counter), -1.0 if section.end == "start" else 1.0
    drop = top_of(lowered) - top_of(counter)
    carried_ids = {item.id for item in carried}
    dropped = {node.id: _raised(node, drop) for node in graph.nodes
               if node.id not in carried_ids and rests_on(node, counter) and _on_the_cut_part(frame, node, cut, sign)}
    taken = list(dropped.values())
    for item in carried:
        taken.append(_set_down(frame, item, cut, sign, top_of(lowered), taken))
    changed = {**dropped, **{node.id: node for node in taken}, counter.id: shortened}
    return _replace_nodes(graph, changed, [lowered])


def fit(graph: SceneGraph, heights: list[HeightChange], replacements: list[Replacement],
        sections: list[LoweredSection]) -> SceneGraph:
    """The room after its fitting edits: replacements, then height changes, then new counter sections."""
    for replacement in replacements:
        graph = replace(graph, replacement)
    for change in heights:
        graph = change_height(graph, change)
    for section in sections:
        graph = add_lowered_section(graph, section)
    return graph


def fitted_ids(before: SceneGraph, after: SceneGraph) -> set[UUID]:
    """Pieces that fitting or relocation added or changed, which the hard constraints must check."""
    original = {node.id: node for node in before.nodes}
    return {node.id for node in after.nodes if not is_room_shell(node) and (
        node.id not in original or node.transform != original[node.id].transform
        or node.dimensions != original[node.id].dimensions)}
