"""The measured model. Only ingest writes dimensions; no agent ever does."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .geometry import Mat4, Vec3

NodeKind = str
"""What a region is called, which is a word somebody coined and not a category.

It was a closed set of six, and a scan of anywhere those six do not cover had
nowhere to put what it found. A name is for showing a person; anything that needs
to know what a region is measures it, which is what `SceneGraph.bounds_the_room`
and `SceneGraph.upright_extent` are for.
"""

Quality = Literal["measured", "needs_another_look", "confirmed"]
"""measured: LiDAR at high confidence.
needs_another_look: thin coverage or low confidence; becomes a request, not a finding.
confirmed: a number a person entered by hand.
"""

LabelSource = str
"""Who coined the name. A new way of finding things should not need this file
edited before it can say where a name came from."""
"""discovery: found in the LiDAR and named from the photos, because RoomPlan boxes no such category."""

Relation = str
"""How one region stands to another, in whatever word was coined for it.

Four were listed, and somewhere with a fifth arrangement could not be described.
A relation carries the geometric predicate that justified it, so anything needing
the truth re-runs that rather than trusting the word.
"""
"""How a node is attached to its parent.

rests_on   its underside meets the parent's top face: a blanket on a bed.
inside     its volume lies within the parent's: a pen in a cup.
mounted_on it hangs off a vertical face: a poster on a wall.
cut_into   it is an opening through the parent: a door in a wall.

`parent_id` carried all four meanings at once before this existed, so nothing
reading the graph could tell an object standing on a table from a door cut
through a wall.
"""


class DisplayAppearance(BaseModel):
    """An inferred finish for rendering; never physical or compliance evidence."""

    model_config = ConfigDict(extra="forbid")
    base_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    material: Literal["paint", "wood", "fabric", "metal", "stone", "glass", "neutral"]
    source: Literal["astra"] = "astra"


class DisplayPart(BaseModel):
    """A completed visual part inside the measured object's normalized bounds."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=60)
    primitive: Literal["box", "cylinder", "ellipsoid"]
    center: list[float] = Field(min_length=3, max_length=3)
    size: list[float] = Field(min_length=3, max_length=3)
    axis: Literal["x", "y", "z"] = "z"
    bevel: float = Field(default=0.02, ge=0, le=0.2)
    base_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    material: Literal["paint", "wood", "fabric", "metal", "stone", "glass", "neutral"]

    @model_validator(mode="after")
    def within_measured_bounds(self) -> DisplayPart:
        if any(size <= 0 or abs(center) + size / 2 > 0.50001 for center, size in zip(self.center, self.size)):
            raise ValueError("display parts must stay inside the measured object bounds")
        return self


class SurfaceText(BaseModel):
    """Words read off a surface, and the frames they were read from.

    A whiteboard, a sign, a label on a box. The text is evidence about what the
    room says, never about what it measures.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_frame_ids: list[str] = Field(min_length=1, max_length=8)
    face: Literal["top", "front", "back", "left", "right", "bottom"] | None = None


class DisplayReconstruction(BaseModel):
    """Photo-informed completion. Never a recovered measurement or verified clearance."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source: Literal["astra"] = "astra"
    summary: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)
    evidence_frame_ids: list[str] = Field(min_length=1, max_length=6)
    parts: list[DisplayPart] = Field(min_length=1, max_length=32)


class SceneNode(BaseModel):
    id: UUID
    kind: NodeKind
    label: str
    raw_category: str
    dimensions: Vec3
    transform: Mat4
    quality: Quality = "measured"
    movable: bool = False
    labeled_by: LabelSource = "roomplan"
    parent_id: UUID | None = None
    relation: Relation | None = Field(default=None, exclude_if=lambda value: value is None)
    texts: list[SurfaceText] = Field(default_factory=list, exclude_if=lambda value: not value)
    appearance: DisplayAppearance | None = Field(default=None, exclude_if=lambda value: value is None)
    reconstruction: DisplayReconstruction | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def name_the_edge_on_older_graphs(cls, data):
        """Fill in the relation a stored graph was written without.

        `parent_id` shipped before `relation` did, carrying both meanings at
        once, so graphs already in a database have a parent and no edge type.
        The child's kind settles which it was: only an opening is ever cut into
        its parent, and everything else that named a parent rested on it.
        """
        if not isinstance(data, dict) or data.get("parent_id") is None or data.get("relation"):
            return data
        opening = data.get("kind") in ("door", "window", "opening")
        return {**data, "relation": "cut_into" if opening else "rests_on"}

    @model_validator(mode="after")
    def parent_and_relation_travel_together(self) -> SceneNode:
        if (self.parent_id is None) != (self.relation is None):
            raise ValueError("a parent_id needs a relation, and a relation needs a parent_id")
        return self

    @property
    def touches_floor(self) -> bool:
        """Whether a node of this kind stands on the floor at all.

        Kind alone cannot settle it: a cup of this kind stands on a desk. Ask
        `SceneGraph.obstacles` for the question a route actually needs, since
        answering it means knowing what the node rests on.
        """
        return not lies_flat(self)


SHEET_THICKNESS = 0.2
"""How thin a region has to be to read as a sheet rather than a solid, in metres."""

SHEET_AREA = 1.0
"""And how broad, in square metres, so that a shelf board is not a wall."""


def bounds_the_room(node: SceneNode) -> bool:
    """Whether the region encloses the space rather than standing in it.

    A room is made of sheets and filled with solids. A wall, a floor, a ceiling,
    a door and a window are all broad and almost without thickness; a chair, a
    table and a bookcase are none of them thin. The difference is in the extents
    themselves, so it holds in a room with a four metre void over it as well as
    one with an ordinary ceiling, where asking whether a region covers most of
    the floor-to-ceiling span does not.

    It needs only the region, so anywhere that used to ask what kind of thing it
    was holding can ask this instead, whether or not it has the whole room.

    Where it is wrong: a rug and a wall poster are broad and thin and are not the
    room, so this reads them as part of it. Nothing in the scans on hand has one,
    so it has not been worth a second rule yet, and a scan that turns one up will
    show as a thing that cannot be counted or moved.
    """
    spans = sorted(node.dimensions.as_tuple())
    return spans[0] <= SHEET_THICKNESS and spans[1] * spans[2] >= SHEET_AREA


def measured_as(node: SceneNode) -> Vec3:
    """The region across, back and up: width, depth and height as a person reads them.

    A region records its extents in its own frame, and the scanner orients that
    frame however it likes. The extent listed third is a height for a bookcase
    and a floor's length for a floor, so anything reading it as a height is right
    about half the time, and a suite that did so told a model a bookcase was half
    a metre tall and two metres deep.

    The region's own turning says which of its three extents points up; that one
    is the height. The other two are its width and its depth, taken from the
    region itself rather than from a box drawn round it, because a bookcase set
    at an angle to the room is still as deep as a bookcase.
    """
    rotation = [row[:3] for row in _rows(node.transform)]
    extents = list(node.dimensions.as_tuple())
    upward = max(range(3), key=lambda index: abs(rotation[2][index]))
    height = 2 * sum(abs(rotation[2][index]) * extents[index] / 2 for index in range(3))
    flat = sorted((extents[index] for index in range(3) if index != upward), reverse=True)
    return Vec3(x=flat[0], y=flat[1], z=height)


def lies_flat(node: SceneNode) -> bool:
    """A sheet lying down: a floor, or a ceiling over one."""
    return bounds_the_room(node) and _upright_extent(node) <= SHEET_THICKNESS


def stands_upright(node: SceneNode) -> bool:
    """A sheet standing up: a wall, or something set into one."""
    return bounds_the_room(node) and _upright_extent(node) > SHEET_THICKNESS


def _upright_extent(node: SceneNode) -> float:
    return measured_as(node).z


def _rows(transform) -> list[list[float]]:
    values = transform.m
    return [values[0:4], values[4:8], values[8:12], values[12:16]]


def _refuse_cycles(parents: dict[UUID, UUID]) -> None:
    settled: set[UUID] = set()
    for start in parents:
        walked: list[UUID] = []
        seen: set[UUID] = set()
        current: UUID | None = start
        while current is not None and current not in settled:
            if current in seen:
                raise ValueError("the scene tree loops back on itself")
            seen.add(current)
            walked.append(current)
            current = parents.get(current)
        settled.update(walked)


class SceneGraph(BaseModel):
    scan_id: UUID
    revision: int = 0
    base_hash: str | None = None
    nodes: list[SceneNode]
    capture_to_room: Mat4 | None = Field(default=None, exclude_if=lambda value: value is None)
    """ARKit world to this graph's room frame: the Y-up to Z-up turn, then the floor drop.

    Photos and the raw scan are posed in ARKit world, so projecting a photo onto
    the model maps model points back through the inverse of this.
    """

    @model_validator(mode="after")
    def the_tree_holds(self) -> SceneGraph:
        """Every parent resolves, and no node is its own ancestor.

        A cycle here is not a cosmetic problem: walking a support chain is how
        the app answers where something is, and a loop hangs that walk.
        """
        known = {node.id for node in self.nodes}
        parents: dict[UUID, UUID] = {}
        for node in self.nodes:
            if node.parent_id is None:
                continue
            if node.parent_id not in known:
                raise ValueError(f"{node.label} names a parent that is not in the graph")
            if node.parent_id == node.id:
                raise ValueError(f"{node.label} is its own parent")
            parents[node.id] = node.parent_id
        _refuse_cycles(parents)
        return self

    def ground(self) -> SceneNode | None:
        """The sheet everything stands on: the lowest one lying down."""
        flat = [node for node in self.nodes if lies_flat(node)]
        return min(flat, key=lambda node: node.transform.position.z) if flat else None

    def upright_extent(self, node: SceneNode) -> float:
        """How far the region reaches up and down, in the room's own frame.

        A region's extents are recorded in its own frame, and the scanner does
        not orient every frame the same way: a floor's come back as two
        horizontal spans and a thickness of nothing, a wall's as a width and a
        storey. Reading `dimensions.z` and calling it height is right for some
        regions and nonsense for others, which is why so much of this code asks
        what kind of thing it is holding first. Turning the extents through the
        region's own rotation answers it for all of them without asking.
        """
        return _upright_extent(node)

    def bounds_the_room(self, node: SceneNode) -> bool:
        """Whether the region encloses the space rather than standing in it."""
        return bounds_the_room(node)

    def contents(self) -> list[SceneNode]:
        """Everything standing in the room rather than making it."""
        return [node for node in self.nodes if not bounds_the_room(node)]

    def by_id(self, node_id: UUID) -> SceneNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def movable(self) -> list[SceneNode]:
        return [n for n in self.nodes if n.movable]

    def obstacles(self) -> list[SceneNode]:
        """What a route has to get around.

        Only what stands on the floor blocks a route. A cup on a desk sits
        inside the desk's own footprint and stops nobody, so counting it would
        narrow every aisle beside that desk by the width of a cup.
        """
        return [
            n for n in self.nodes
            if stands_upright(n) or (n.touches_floor and self.stands_on_floor(n))
        ]

    def stands_on_floor(self, node: SceneNode) -> bool:
        """Whether the node reaches the ground rather than being carried."""
        if node.parent_id is None:
            return True
        parent = self.by_id(node.parent_id)
        if lies_flat(parent):
            return node.relation == "rests_on"
        return False

    def children_of(self, node_id: UUID, relation: Relation | None = None) -> list[SceneNode]:
        return [
            node
            for node in self.nodes
            if node.parent_id == node_id and (relation is None or node.relation == relation)
        ]

    def ancestors_of(self, node_id: UUID) -> list[SceneNode]:
        """From the immediate parent outward: the cup, then the desk, then the floor."""
        chain: list[SceneNode] = []
        seen = {node_id}
        current = self.by_id(node_id).parent_id
        while current is not None and current not in seen:
            seen.add(current)
            parent = self.by_id(current)
            chain.append(parent)
            current = parent.parent_id
        return chain

    def descendants_of(self, node_id: UUID) -> list[SceneNode]:
        """Everything the node carries, at any depth."""
        found: list[SceneNode] = []
        frontier = [node_id]
        while frontier:
            for child in self.children_of(frontier.pop()):
                found.append(child)
                frontier.append(child.id)
        return found

    def roots(self) -> list[SceneNode]:
        return [node for node in self.nodes if node.parent_id is None]


class Stop(BaseModel):
    """A destination on a customer's route."""

    name: str
    position: Vec3
    anchor_node_id: UUID | None = None
    """The fixture this stop is at, such as the counter a customer orders from.

    Route width ignores only this node near the stop. A stop with no anchor gets
    no exemption, so an obstruction beside it still counts.
    """


class Scenario(BaseModel):
    """The routine we screen. Legs run between consecutive stops."""

    name: str = "Order a drink"
    stops: list[Stop] = Field(min_length=2)
