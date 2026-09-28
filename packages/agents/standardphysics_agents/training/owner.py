"""The owner in the loop: they see each change before it is kept, and say yes, no, or what to keep.

A proposal the checker accepts still goes to the owner. When they turn it
down they usually say why ("keep the chairs at that table"), and what they say
becomes a stated wish that every later option is held to. `WishBook` keeps
each wish with the layout it was read from or said about, since a wish like
"stays at the table" means "as it was in that layout".

`SimulatedOwner` stands in for a real owner in evaluation and training. Their
wishes are the relations in their own layout before any scramble
(`infer_wishes(owner_layout)`), which the model never sees directly. They
object only to a change that newly breaks one of those wishes, so a room the
scramble already disturbed is not held against the model. `InteractiveOwner`
asks a person at the terminal instead.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from standardphysics_contracts import MeasurementProvider, OwnerWish, SceneGraph

from ..fix import CandidateRejection
from .wishes import Wish, infer_wishes, kept, stays_near, stays_put

INCH = 0.0254


@dataclass
class WishBook:
    """Wishes, each with the layout it refers to."""

    @classmethod
    def read_from(cls, layout: SceneGraph, measure: MeasurementProvider) -> WishBook:
        """The wishes a layout shows its owner chose, each referring to that layout."""
        book = cls()
        for wish in infer_wishes(layout, measure):
            book.add(wish, layout)
        return book

    entries: list[tuple[Wish, SceneGraph]] = field(default_factory=list)

    def add(self, wish: Wish, reference: SceneGraph) -> None:
        if all(existing != wish for existing, _ in self.entries):
            self.entries.append((wish, reference))

    @property
    def wishes(self) -> list[Wish]:
        return [wish for wish, _ in self.entries]

    @property
    def stated(self) -> list[Wish]:
        return [wish for wish in self.wishes if wish.hard]

    def broken(self, after: SceneGraph, measure: MeasurementProvider, hard_only: bool = False) -> list[Wish]:
        return [wish for wish, reference in self.entries
                if (wish.hard or not hard_only) and not kept(wish, reference, after, measure)]

    def rejection(self, measure: MeasurementProvider) -> CandidateRejection | None:
        """The stated wishes as a veto, in the same shape as a room's ADA directives; None when there are none."""
        if not self.stated:
            return None

        def refuse(_before: SceneGraph, after: SceneGraph) -> str | None:
            broken_now = self.broken(after, measure, hard_only=True)
            return f"owner_wish: {broken_now[0].text}" if broken_now else None

        return refuse

    def kept_share(self, start: SceneGraph, end: SceneGraph, measure: MeasurementProvider) -> float:
        """Of the wishes `start` keeps, the share `end` still keeps; 1.0 when `start` keeps none."""
        held = [(wish, reference) for wish, reference in self.entries if kept(wish, reference, start, measure)]
        if not held:
            return 1.0
        return sum(kept(wish, reference, end, measure) for wish, reference in held) / len(held)

    def newly_broken(self, before: SceneGraph, after: SceneGraph, measure: MeasurementProvider) -> list[Wish]:
        """Wishes `before` kept and `after` breaks."""
        return [wish for wish, reference in self.entries
                if kept(wish, reference, before, measure) and not kept(wish, reference, after, measure)]


@dataclass(frozen=True)
class Review:
    accepted: bool
    said: str = ""
    stated: tuple[Wish, ...] = ()
    """Wishes the owner stated while turning the change down, now hard for every later option."""
    about: SceneGraph | None = None
    """The layout those wishes refer to."""


def objection(wish: Wish) -> str:
    return f"Please don't do that. I want this kept: {wish.text}."


@dataclass
class SimulatedOwner:
    owner_layout: SceneGraph
    measure: MeasurementProvider
    hidden: WishBook = field(default_factory=WishBook)

    def __post_init__(self) -> None:
        if not self.hidden.entries:
            self.hidden = WishBook.read_from(self.owner_layout, self.measure)

    def review(self, before: SceneGraph, after: SceneGraph, explanation: str = "") -> Review:
        """Yes, or no naming the first wish the change newly breaks, which becomes a stated wish."""
        newly = self.hidden.newly_broken(before, after, self.measure)
        if not newly:
            return Review(accepted=True)
        return Review(accepted=False, said=objection(newly[0]), stated=(replace(newly[0], source="stated"),),
                      about=self.owner_layout)

    def kept_share(self, start: SceneGraph, end: SceneGraph) -> float:
        """Of the hidden wishes the starting room kept, the share the final room still keeps."""
        return self.hidden.kept_share(start, end, self.measure)


ANSWER_HELP = (
    "Keep this change? Answer y, or n and optionally a reason. To state a wish: "
    "'lock <id>' keeps a piece where it is; 'near <id> <anchor id> <inches>' keeps it close to another piece."
)
LOCK = re.compile(r"lock\s+([0-9a-f]{4,})", re.IGNORECASE)
NEAR = re.compile(r"near\s+([0-9a-f]{4,})\s+([0-9a-f]{4,})\s+(\d+(?:\.\d+)?)", re.IGNORECASE)


def _by_prefix(graph: SceneGraph, prefix: str):
    return next((node for node in graph.nodes if str(node.id).startswith(prefix.lower())), None)


def stated_from(answer: str, room: SceneGraph) -> list[Wish]:
    """The wishes a typed answer states, reading pieces by the start of their id."""
    found = []
    for match in LOCK.finditer(answer):
        node = _by_prefix(room, match.group(1))
        if node is not None:
            found.append(stays_put(node))
    for match in NEAR.finditer(answer):
        node, anchor = _by_prefix(room, match.group(1)), _by_prefix(room, match.group(2))
        if node is not None and anchor is not None:
            found.append(stays_near(node, anchor, float(match.group(3)) * INCH))
    return found


@dataclass
class InteractiveOwner:
    """A person at the terminal plays the owner."""

    ask: Callable[[str], str] = input
    show: Callable[[str], None] = print

    def review(self, before: SceneGraph, after: SceneGraph, explanation: str = "") -> Review:
        self.show(explanation)
        answer = self.ask(f"{ANSWER_HELP}\n> ").strip()
        if answer.lower() in ("y", "yes", ""):
            return Review(accepted=True)
        return Review(accepted=False, said=answer, stated=tuple(stated_from(answer, before)), about=before)


TABLE_SLACK_INCHES = 12.0
"""How far a seat the owner wants kept at its table may still drift from it."""


def _node(graph: SceneGraph, node_id):
    return next((node for node in graph.nodes if node.id == node_id), None)


def _as_wish(saved: OwnerWish, graph: SceneGraph) -> Wish | None:
    node = _node(graph, saved.node_id)
    if node is None:
        return None
    if saved.kind == "stays_put":
        return stays_put(node)
    anchor = _node(graph, saved.anchor_id)
    return None if anchor is None else stays_near(node, anchor, saved.inches * INCH)


def stated_book(graph: SceneGraph, saved: list[OwnerWish]) -> WishBook:
    """The owner's saved wishes as stated wishes about `graph`; a wish naming a piece no longer there is skipped."""
    book = WishBook()
    for wish in (_as_wish(item, graph) for item in saved):
        if wish is not None:
            book.add(wish, graph)
    return book


def _distance_inches(a, b) -> float:
    return math.dist((a.transform.position.x, a.transform.position.y),
                     (b.transform.position.x, b.transform.position.y)) / INCH


def keep_request(wish: Wish, graph: SceneGraph) -> OwnerWish | None:
    """The saved wish that would keep an inferred one, read against the layout it was inferred from.

    A seat kept at its table stays within a foot of its distance now; a piece
    kept against its wall stays where it is. The counter's view depends on
    every piece at once, so there is no single wish to save for it.
    """
    nodes = [_node(graph, node_id) for node_id in wish.subjects]
    if wish.kind == "with_table" and None not in nodes:
        seat, table = nodes
        return OwnerWish(kind="stays_near", node_id=seat.id, anchor_id=table.id,
                         inches=round(_distance_inches(seat, table) + TABLE_SLACK_INCHES, 1),
                         text=f"Keep the {seat.label.lower()} at the {table.label.lower()}")
    if wish.kind == "against_wall" and None not in nodes:
        return OwnerWish(kind="stays_put", node_id=nodes[0].id, text=f"Keep the {nodes[0].label.lower()} where it is")
    return None
