"""The ask box: any question about the shop, answered from the measured model.

One entry point, a closed set of question kinds, and one executor each. The
model reads the sentence and picks the kind. Every number in the answer is read
off the room or taken by the measurement provider, so an answer can be wrong
about what was asked and never wrong about what is there.

Each answer carries a locus, so asking about the tables sends the camera to the
tables. That is the difference between a room you can talk to and a list of
measurements.
"""

from __future__ import annotations

from typing import Callable

from standardphysics_contracts import MeasurementProvider, Scenario, SceneGraph
from standardphysics_contracts.rules import Tier

from ..copy import ask_reply
from ..router.decision import Rejected
from ..rules import AgentRulePack, VerificationLedger, load_ledger, load_pack
from ..tracing import traced
from .answer import Answer, AskContext
from .dimensions import measure
from .directions import DIRECTIONS, Direction, shop_axes
from .inventory import count
from .layout import rearrange
from .locus import subject_locus
from .places import where
from .query import KINDS, Query, QueryKind, parse_query, query_schema
from .resolve import KeywordResolver, ModelResolver, catalogue, resolver
from .shapes import Arrangement, arrangement, describe
from .space import FitAnswer, FitRequest, fits, space
from .spans import distance
from .standards import check

Executor = Callable[[Query, AskContext], Answer]

EXECUTORS: dict[QueryKind, Executor] = {
    "COUNT": count,
    "MEASURE": measure,
    "DISTANCE": distance,
    "WHERE": where,
    "DESCRIBE": describe,
    "SPACE": space,
    "REARRANGE": rearrange,
    "CHECK": check,
}


@traced("ask")
def ask(
    text: str,
    graph: SceneGraph,
    scenario: Scenario,
    measurements: MeasurementProvider,
    *,
    rules: AgentRulePack | None = None,
    ledger: VerificationLedger | None = None,
    max_tier: Tier = 1,
    with_resolver=None,
) -> Answer:
    """A question in, an answer the owner can read and a place to look."""
    context = AskContext(
        graph=graph,
        scenario=scenario,
        measure=measurements,
        rules=rules or load_pack(),
        ledger=ledger if ledger is not None else load_ledger(),
        max_tier=max_tier,
    )
    query = (with_resolver or resolver()).resolve(text, graph, scenario)
    if isinstance(query, Rejected):
        return Answer(text=ask_reply(query.reason), rejected=query.reason)
    return EXECUTORS[query.kind](query, context)


__all__ = [
    "DIRECTIONS", "EXECUTORS", "KINDS", "Answer", "Arrangement", "AskContext",
    "Direction", "Executor", "FitAnswer", "FitRequest", "KeywordResolver",
    "ModelResolver", "Query", "QueryKind", "arrangement", "ask", "catalogue",
    "check", "count", "describe", "distance", "fits", "measure", "parse_query",
    "query_schema", "rearrange", "resolver", "shop_axes", "space",
    "subject_locus", "where",
]
