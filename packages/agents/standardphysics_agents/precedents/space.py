"""From a scan's space type to the directives that bind its layout proposals."""

from __future__ import annotations

from standardphysics_contracts import SceneGraph
from standardphysics_contracts.precedents import PrecedentDirective, SpaceTypology

from ..fix.search import CandidateRejection
from .checker import precedent_rejection_for
from .compiler import PrecedentCompiler
from .verification import load_precedents


def directives_for_space(
    typology: SpaceTypology | None,
    graph: SceneGraph,
    directives: list[PrecedentDirective] | None = None,
) -> list[PrecedentDirective]:
    """The directives that apply to this room, drawn from the verified set by default.

    No space type means the owner has not said what the room is, so nothing
    matches. Only directives a person has signed are considered unless a
    caller passes its own list.
    """
    if typology is None:
        return []
    available = load_precedents() if directives is None else directives
    return PrecedentCompiler(available).match(typology, graph.nodes)


def rejection_for_space(
    typology: SpaceTypology | None,
    graph: SceneGraph,
    directives: list[PrecedentDirective] | None = None,
) -> CandidateRejection | None:
    """The directive veto for this room, or None when no directive applies."""
    matched = directives_for_space(typology, graph, directives)
    return precedent_rejection_for(matched) if matched else None
