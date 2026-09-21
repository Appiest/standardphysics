"""What the owner asked, as one of a closed set of questions.

A model reads the sentence and picks a kind and its arguments. It never
supplies a number about the shop: every measurement in an answer is read off
the model of the room or taken by the measurement provider. That split is the
whole design. Working out that "how tall is my table" is a question about
height is judgment, and a model is good at it. Knowing the table is 29.5 inches
tall is measurement, and a model is not.

The parse is fail-closed, the same as a router action. A question naming
something that is not in the shop, a kind outside the set, or a request to move
something built in comes back as `Rejected` and reaches no executor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel
from standardphysics_contracts import SceneGraph

from ..router.decision import Rejected
from ..strict_schema import strict_schema
from .directions import DIRECTIONS, Direction

QueryKind = str
"""A kind of question some executor has said it can answer.

It was a closed list of eight, written here and restated in the table of
executors, so the list of things a person could ask lived in a type rather than
in the code that answers them. An executor now registers its own kind, with what
it needs from the question, and a new one plugs in without this file changing.
"""

Needs = Callable[["Query"], "str | None"]


@dataclass(frozen=True)
class Kind:
    name: str
    needs: Needs
    """Why a question of this kind cannot be acted on, or nothing if it can."""


KINDS: dict[str, Kind] = {}
"""Every kind of question an executor has registered, by name."""


def register_kind(name: str, needs: Needs | None = None) -> None:
    KINDS[name] = Kind(name=name, needs=needs or (lambda query: None))


Dimension = str
"""A measurement some reader knows how to take.

Filled from the readers themselves, so the dimensions a question may ask about
are exactly the ones something can measure."""

DIMENSIONS: set[str] = set()


def register_dimensions(names) -> None:
    DIMENSIONS.update(names)

MAX_DISTANCE_INCHES = 600.0
"""Fifty feet. Longer than any shop we screen, so a model that says 10000 is
caught before it drives a search."""

MAX_RESTATEMENT = 200



class Query(BaseModel):
    """One question, in terms the executors can act on."""

    model_config = {"extra": "forbid"}

    kind: QueryKind
    subject_labels: list[str] = []
    """What they asked about, in their words: "chairs", "the counter"."""

    subject_node_ids: list[UUID] = []
    """Or pinned exactly, when the model could tell which piece they meant."""

    other_labels: list[str] = []
    """The far end of a DISTANCE question."""

    dimension: Dimension | None = None
    thing: str | None = None
    """What they are thinking of buying, for SPACE."""

    length_inches: float | None = None
    depth_inches: float | None = None
    height_inches: float | None = None
    direction: Direction | None = None
    distance_inches: float | None = None
    locked_node_ids: list[UUID] = []
    restated: str
    """One sentence back to them, so they can see what was understood."""


def query_schema() -> dict:
    """The shape a question has to arrive in, generated from `Query`.

    Strict, because the call that uses it asks the endpoint to enforce the
    schema rather than hoping. `parse_query` still rejects anything that gets
    through: a kind outside the set, a label naming nothing in the shop, a
    measurement past what a shop can be.
    """
    schema = strict_schema(Query)
    properties = schema.get("properties", {})
    _choose_from(properties.get("kind"), sorted(KINDS))
    _choose_from(properties.get("dimension"), sorted(DIMENSIONS))
    return schema


def _choose_from(field: dict | None, names: list[str]) -> None:
    """Tell the model which values exist, read from what has registered.

    Without the closed types the generated schema says only "a string", and a
    model given no list invents kinds nobody answers.
    """
    if not field or not names:
        return
    variants = field.get("anyOf")
    target = next((v for v in variants if v.get("type") == "string"), None) if variants else field
    if target is not None:
        target["enum"] = names


def _shape(raw: object) -> Query | Rejected:
    if not isinstance(raw, dict):
        return Rejected("question_not_an_object")
    try:
        return Query.model_validate(raw)
    except Exception:
        return Rejected("question_does_not_fit_the_shape")


def _closed_sets(query: Query, graph: SceneGraph) -> str | None:
    if query.kind not in KINDS:
        return "unknown_question"
    if query.dimension is not None and query.dimension not in DIMENSIONS:
        return "unknown_dimension"
    if query.direction is not None and query.direction not in DIRECTIONS:
        return "unknown_direction"
    return None


def _arguments(query: Query, graph: SceneGraph) -> str | None:
    """Whatever the kind's own executor said it needs."""
    return KINDS[query.kind].needs(query)


def names_something(query: Query) -> str | None:
    if query.subject_labels or query.subject_node_ids:
        return None
    return "question_names_nothing"


def all_of(*needs: Needs) -> Needs:
    """Several requirements, reporting the first one a question misses."""
    return lambda query: next((why for need in needs if (why := need(query))), None)


def _numbers(query: Query, graph: SceneGraph) -> str | None:
    sizes = (query.length_inches, query.depth_inches, query.height_inches)
    for value in (*sizes, query.distance_inches):
        if value is not None and not (0 < value <= MAX_DISTANCE_INCHES):
            return "measurement_out_of_range"
    return None


def _pinned(query: Query, graph: SceneGraph) -> str | None:
    for node_id in query.subject_node_ids:
        try:
            graph.by_id(node_id)
        except KeyError:
            return "asked_about_something_that_is_not_here"
    if set(query.subject_node_ids) & set(query.locked_node_ids):
        return "asked_to_move_something_it_also_locked"
    return None


def _movable(query: Query, graph: SceneGraph) -> str | None:
    if query.direction is None:
        return None
    for node_id in query.subject_node_ids:
        if not graph.by_id(node_id).movable:
            return "asked_to_move_something_fixed"
    return None


CHECKS = (_closed_sets, _arguments, _numbers, _pinned, _movable)


def parse_query(raw: object, graph: SceneGraph) -> Query | Rejected:
    query = _shape(raw)
    if isinstance(query, Rejected):
        return query
    if not query.restated.strip():
        return Rejected("question_was_not_restated")
    if len(query.restated) > MAX_RESTATEMENT:
        return Rejected("restatement_too_long")
    for check in CHECKS:
        reason = check(query, graph)
        if reason:
            return Rejected(reason)
    return query
