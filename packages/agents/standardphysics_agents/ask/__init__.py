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
from .compose import compose
from .dimensions import measure
from .directions import DIRECTIONS, Direction, shop_axes
from .inventory import count
from .layout import rearrange
from .locus import subject_locus
from .places import where
from .query import (
    KINDS,
    Query,
    QueryKind,
    all_of,
    names_something,
    parse_query,
    query_schema,
    register_kind,
)
from .resolve import KeywordResolver, ModelResolver, catalogue, resolver
from .shapes import Arrangement, arrangement, describe
from .space import FitAnswer, FitRequest, fits, space
from .spans import distance
from .standards import check
from .verify import verified

Executor = Callable[[Query, AskContext], Answer]

def _says(field: str, reason: str):
    """A question has to carry this argument before it can be acted on."""
    return lambda query: None if getattr(query, field) else reason


EXECUTORS: dict[QueryKind, Executor] = {}


def answers(name: str, executor: Executor, *needs) -> None:
    """Register an executor under the kind of question it answers.

    The kind, what a question of it must carry, and the code that answers it are
    one entry, so the list of questions this layer can take is the list of
    executors that exist rather than a type written somewhere else.
    """
    EXECUTORS[name] = executor
    register_kind(name, all_of(*needs) if needs else None)


answers("COUNT", count, names_something)
answers("MEASURE", measure, names_something,
        _says("dimension", "question_does_not_say_which_dimension"))
answers("DISTANCE", distance, _says("other_labels", "question_names_only_one_end"))
answers("WHERE", where, names_something)
answers("DESCRIBE", describe, names_something)
answers("SPACE", space, _says("length_inches", "question_does_not_say_how_big"))
answers("REARRANGE", rearrange, names_something,
        _says("direction", "question_does_not_say_which_way"))
answers("CHECK", check)


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
    return verified(_answered(text, graph, scenario, context, with_resolver), graph, text)


def _answered(text, graph, scenario, context, with_resolver) -> Answer:
    """A plan the model wrote, or the older executors when it could not write one.

    Composition leads because a question nobody anticipated has nowhere else to
    go. The executors stay underneath until every question they serve can be
    written as steps, and they answer while a model is unreachable.
    """
    planned = compose(text, graph)
    if planned is not None:
        return Answer(
            text=planned.text,
            subjects=planned.regions,
            data={"figures": planned.figures},
        )
    query = (with_resolver or resolver()).resolve(text, graph, scenario)
    if isinstance(query, Rejected):
        return Answer(text=ask_reply(query.reason), rejected=query.reason)
    return EXECUTORS[query.kind](query, context)


__all__ = [
    "DIRECTIONS", "EXECUTORS", "KINDS", "Answer", "Arrangement", "AskContext",
    "Direction", "Executor", "FitAnswer", "FitRequest", "KeywordResolver",
    "ModelResolver", "Query", "QueryKind", "answers", "arrangement", "ask", "catalogue",
    "check", "count", "describe", "distance", "fits", "measure", "parse_query",
    "compose", "query_schema", "rearrange", "resolver", "shop_axes", "space",
    "verified",
    "subject_locus", "where",
]
