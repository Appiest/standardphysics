"""Working out which question was asked.

The model does this job because it is the one thing in the app that needs
judgment about language: "how would you describe my table arrangement" and
"what shape are my tables in" are the same question, and no amount of keyword
matching gets there. What the model returns is a kind and its arguments, never
a fact about the shop.

The keyword resolver underneath it is a fallback, labelled as one, so the
feature works in a demo with no key and nobody is misled about which answered.
"""

from __future__ import annotations

import re

from standardphysics_contracts import Scenario, SceneGraph, stands_upright

from ..models import OpenRouter
from ..router.decision import Rejected
from ..tracing import traced
from . import subjects
from .directions import Direction
from .query import Dimension, Query, QueryKind, parse_query, query_schema

INSTRUCTION = (
    "A shop owner has asked a question about their shop, which has been scanned "
    "and measured. Work out which kind of question it is and fill in its "
    "arguments. Do not answer it and do not supply any measurement: every "
    "number about the shop is read from the scan.\n"
    "COUNT: how many of something there is.\n"
    "MEASURE: how tall, long, wide or deep something is. Set dimension.\n"
    "DISTANCE: how far apart two things are. Put the far end in other_labels.\n"
    "WHERE: where something is.\n"
    "DESCRIBE: what shape or pattern a set of furniture makes.\n"
    "SPACE: whether something they might buy would fit. Set thing and whichever "
    "of length_inches, depth_inches and height_inches they gave, and leave the "
    "rest empty rather than guessing.\n"
    "REARRANGE: move furniture. Set direction, and distance_inches only if "
    "they said one.\n"
    "CHECK: whether something meets the accessibility standards.\n"
    "Put the pieces they meant in subject_node_ids, chosen from the ids you were "
    "given. Judge by the measurements and where each piece is, not by whether its "
    "label is the word they used: somebody asking about their desks means the "
    "pieces that are desk-shaped and desk-height, whatever the scan called them. "
    "Use subject_labels only when no piece in the list is what they meant. "
    "Restate the question in one short sentence so they can see it was read "
    "correctly."
)

KIND_WORDS: tuple[tuple[str, QueryKind], ...] = (
    ("how many", "COUNT"),
    ("how much of", "COUNT"),
    ("how far", "DISTANCE"),
    ("how tall", "MEASURE"),
    ("how high", "MEASURE"),
    ("how wide", "MEASURE"),
    ("how long", "MEASURE"),
    ("how deep", "MEASURE"),
    ("how big", "MEASURE"),
    ("what size", "MEASURE"),
    ("room for", "SPACE"),
    ("space for", "SPACE"),
    ("fit a", "SPACE"),
    ("fit the", "SPACE"),
    ("describe", "DESCRIBE"),
    ("arrangement", "DESCRIBE"),
    ("what shape", "DESCRIBE"),
    ("pattern", "DESCRIBE"),
    ("laid out", "DESCRIBE"),
    ("look like from above", "DESCRIBE"),
    ("where", "WHERE"),
    ("wide enough", "CHECK"),
    ("tall enough", "CHECK"),
    ("enough room", "CHECK"),
    ("turn around", "CHECK"),
    ("get past", "CHECK"),
    ("get through", "CHECK"),
    ("accessible", "CHECK"),
    ("pass", "CHECK"),
    ("compliant", "CHECK"),
    ("up to code", "CHECK"),
    ("move", "REARRANGE"),
    ("shift", "REARRANGE"),
    ("push", "REARRANGE"),
    ("slide", "REARRANGE"),
    ("spread", "REARRANGE"),
)

DIMENSION_WORDS: tuple[tuple[str, Dimension], ...] = (
    ("how tall", "height"),
    ("how high", "height"),
    ("how wide", "width"),
    ("how long", "length"),
    ("how deep", "depth"),
    ("how big", "footprint"),
    ("what size", "footprint"),
)

DIRECTION_WORDS: tuple[tuple[str, Direction], ...] = (
    ("back", "back"),
    ("rear", "back"),
    ("front", "front"),
    ("forward", "front"),
    ("door", "front"),
    ("left", "left"),
    ("right", "right"),
    ("apart", "apart"),
    ("spread", "apart"),
    ("wider", "apart"),
    ("together", "together"),
    ("closer", "together"),
    ("tighter", "together"),
)

NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def _first(text: str, table) -> object | None:
    lowered = text.casefold()
    hits = [(lowered.find(word), value) for word, value in table if word in lowered]
    return min(hits)[1] if hits else None


def _numbers(text: str) -> list[float]:
    return [float(match) for match in NUMBER.findall(text)]


ASKING_WORDS = frozenset(
    {
        "how", "many", "much", "what", "where", "which", "does", "have", "got",
        "there", "are", "is", "the", "my", "our", "size", "tall", "high", "wide",
        "long", "deep", "big", "far", "from", "about", "would", "could", "should",
        "describe", "arrangement", "shape", "pattern", "laid", "out", "look",
        "like", "space", "room", "fit", "inch", "inches", "feet", "foot", "and",
        "for", "with", "that", "this", "them", "they", "you", "your", "any",
    }
)


def _labels(text: str, graph: SceneGraph) -> list[str]:
    """Every distinct kind of thing in the shop whose name they used."""
    found: list[str] = []
    for node in graph.nodes:
        if node.label not in found and subjects.label_matches(node.label, text):
            found.append(node.label)
    if not found and subjects.is_about_the_room([text]):
        found.append("room")
    return found


def _unmatched_noun(text: str) -> list[str]:
    """The thing they named, when the shop has nothing by that name.

    "How many sofas do I have" is a question with an answer, and the answer is
    none. Dropping it because no sofa was scanned would leave somebody asking
    twice.
    """
    words = [
        word
        for word in re.findall(r"[a-zA-Z]+", text)
        if len(word) > 2 and word.casefold() not in ASKING_WORDS
    ]
    return [max(words, key=len)] if words else []


class KeywordResolver:
    """Matches phrasings, and says so. No judgment, and no pretending to any."""

    provider = "local_keywords"

    @traced("ask.keywords")
    def resolve(
        self, text: str, graph: SceneGraph, scenario: Scenario
    ) -> Query | Rejected:
        kind = _first(text, KIND_WORDS)
        if kind is None:
            return Rejected("could_not_tell_what_was_asked")
        payload = {
            "kind": kind,
            "subject_labels": self._subjects(text, graph, kind),
            "restated": text.strip(),
        }
        payload.update(self._arguments(text, graph, kind))
        return parse_query(payload, graph)

    def _subjects(self, text: str, graph: SceneGraph, kind: str) -> list[str]:
        labels = _labels(text, graph)
        if kind == "DISTANCE" and len(labels) >= 2:
            return labels[:1]
        return labels or _unmatched_noun(text)

    def _arguments(self, text: str, graph: SceneGraph, kind: str) -> dict:
        if kind == "MEASURE":
            return {"dimension": _first(text, DIMENSION_WORDS) or "height"}
        if kind == "DISTANCE":
            labels = _labels(text, graph)
            return {"other_labels": labels[1:2]}
        if kind == "SPACE":
            return self._space(text)
        if kind == "REARRANGE":
            numbers = _numbers(text)
            return {
                "direction": _first(text, DIRECTION_WORDS),
                "distance_inches": numbers[0] if numbers else None,
            }
        return {}

    @staticmethod
    def _space(text: str) -> dict:
        sizes = _numbers(text)
        words = [w for w in re.findall(r"[a-z]+", text.casefold()) if len(w) > 3]
        thing = words[-1] if words else "piece"
        return {
            "thing": thing,
            "length_inches": sizes[0] if sizes else None,
            "depth_inches": sizes[1] if len(sizes) > 1 else None,
            "height_inches": sizes[2] if len(sizes) > 2 else None,
        }


class ModelResolver:
    """One OpenRouter call, constrained to `Query`."""

    provider = "openrouter"

    def __init__(self, models: OpenRouter | None = None) -> None:
        self.models = models or OpenRouter()

    @property
    def configured(self) -> bool:
        return self.models.configured

    @traced("ask.resolve")
    def resolve(
        self, text: str, graph: SceneGraph, scenario: Scenario
    ) -> Query | Rejected:
        answer = self.models.structured(
            INSTRUCTION,
            {"asked": text, "pieces": catalogue(graph, scenario)},
            query_schema(),
            "shop_question",
        )
        if isinstance(answer, Rejected):
            return answer
        return parse_query(answer.payload, graph)


def catalogue(graph: SceneGraph, scenario: Scenario) -> list[dict]:
    """What is in the shop, described the way somebody standing in it would.

    Positions are given in the shop's own terms rather than as coordinates,
    because "the table near the front on the left" is what people say.

    Every piece carries its measurements, so that a piece can be picked out by
    what it is like rather than by what it is called. Without them the only way
    to answer "the two desks" is to match the word against a label, and a scan
    that called them tables has no desks in it.
    """
    from standardphysics_contracts import to_inches

    from .directions import shop_axes

    back, right = shop_axes(scenario, graph)
    return [
        {
            "id": str(node.id),
            "label": node.label,
            "movable": node.movable,
            "width_inches": round(to_inches(node.dimensions.x), 1),
            "depth_inches": round(to_inches(node.dimensions.y), 1),
            "height_inches": round(to_inches(node.dimensions.z), 1),
            "toward_back_inches": round(
                to_inches(
                    node.transform.position.x * back[0]
                    + node.transform.position.y * back[1]
                ),
                1,
            ),
            "toward_right_inches": round(
                to_inches(
                    node.transform.position.x * right[0]
                    + node.transform.position.y * right[1]
                ),
                1,
            ),
        }
        for node in graph.nodes
        if not stands_upright(node)
    ]


class FallbackResolver:
    """The model first, and the keywords whenever it does not come back with a query.

    The model reads phrasing the keywords never will, so it leads. But it can
    fail for reasons that have nothing to do with the question: no credit left,
    a rate limit, a timeout, a reply that would not parse. Treating those as
    "we did not follow that one" tells the owner their question was bad when
    the truth is that a server was busy, and it throws away an answer the
    keywords could have given from measurements already in hand.
    """

    provider = "openrouter"

    def __init__(self, models: OpenRouter | None = None) -> None:
        self.model = ModelResolver(models)
        self.keywords = KeywordResolver()

    @property
    def configured(self) -> bool:
        return self.model.configured

    def resolve(self, text: str, graph: SceneGraph, scenario: Scenario):
        answer = self.model.resolve(text, graph, scenario)
        if not isinstance(answer, Rejected):
            return answer
        return self.keywords.resolve(text, graph, scenario)


def resolver(models: OpenRouter | None = None):
    """The model backed by the keywords, or the keywords alone when it is not configured."""
    chain = FallbackResolver(models)
    return chain if chain.configured else chain.keywords
