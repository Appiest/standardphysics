"""Asking whether something passes.

"Is my doorway wide enough" is a question the checks already answer, so this
finds the finding rather than measuring again. The sentence the owner gets is
the one the report would give them, which means the ask box and the list never
disagree.

Not everything a check is about is a piece of furniture. The path to the
counter is the subject of ADA 2010 403.5.1 and there is no node called "path",
so subjects are matched by topic as well as by id.
"""

from __future__ import annotations

from standardphysics_contracts import Finding

from ..tracing import traced
from . import subjects
from .answer import Answer, AskContext
from .locus import subject_locus
from .query import Query

WHAT_WE_CHECK = "the paths, the doorways and the counter"

TOPICS: tuple[tuple[str, frozenset[str]], ...] = (
    ("path", frozenset({"route_clear_width", "passing_space", "exit_path"})),
    ("route", frozenset({"route_clear_width", "passing_space"})),
    ("aisle", frozenset({"route_clear_width", "passing_space"})),
    ("walkway", frozenset({"route_clear_width", "passing_space"})),
    ("gap", frozenset({"route_clear_width"})),
    ("exit", frozenset({"exit_path"})),
    ("egress", frozenset({"exit_path"})),
    ("means", frozenset({"exit_path"})),
    ("door", frozenset({
        "door_clear_width", "entrance_threshold", "door_hardware",
        "door_opening_force",
    })),
    ("doorway", frozenset({"door_clear_width", "entrance_threshold"})),
    ("entrance", frozenset({"door_clear_width", "entrance_threshold"})),
    ("handle", frozenset({"door_hardware"})),
    ("counter", frozenset({
        "service_counter_height", "service_counter_approach",
    })),
    ("register", frozenset({"point_of_sale_height"})),
    ("card reader", frozenset({"point_of_sale_height"})),
    ("bar", frozenset({"service_counter_height", "service_counter_approach"})),
    ("turn", frozenset({"turning_space", "turn_clear_width"})),
    ("floor", frozenset({"floor_surface"})),
    ("mat", frozenset({"floor_surface"})),
    ("protruding", frozenset({"protruding_objects"})),
    ("projection", frozenset({"protruding_objects"})),
    ("reach", frozenset({"reach_range"})),
    ("operable", frozenset({"reach_range"})),
    ("table", frozenset({"dining_surface_height"})),
    ("dining", frozenset({"dining_surface_height"})),
    ("restroom", frozenset({"restroom_turning_space"})),
    ("toilet", frozenset({"restroom_turning_space"})),
    ("bathroom", frozenset({"restroom_turning_space"})),
    ("ramp", frozenset({
        "ramp_running_slope", "ramp_rise", "ramp_clear_width", "ramp_landing_length", "ramp_handrails",
    })),
    ("handrail", frozenset({"ramp_handrails", "handrail_height"})),
    ("railing", frozenset({"ramp_handrails", "handrail_height"})),
    ("kiosk", frozenset({"kiosk_reach", "kiosk_clear_floor"})),
    ("touchscreen", frozenset({"kiosk_reach", "kiosk_clear_floor"})),
    ("condiment", frozenset({"self_service_reach"})),
    ("straw", frozenset({"self_service_reach"})),
    ("napkin", frozenset({"self_service_reach"})),
    ("self-serve", frozenset({"self_service_reach"})),
    ("pickup", frozenset({"service_counter_height", "service_counter_approach"})),
)

OUTCOME_ORDER = {"problem": 0, "passes": 1, "question": 2}
"""What to lead with.

Somebody asking whether their door is wide enough wants the width, not the
photograph we also want of its handle. A problem outranks both.
"""


def topics_in(words: str) -> frozenset[str]:
    """The checks a question is about, from the words it used.

    The earliest topic word wins. "Is the path to the counter wide enough" is a
    question about the path, and the counter is where the path goes, which is
    the order English puts them in.
    """
    lowered = words.casefold()
    hits = [
        (lowered.find(word), checks) for word, checks in TOPICS if word in lowered
    ]
    if not hits:
        return frozenset()
    first = min(position for position, _ in hits)
    return frozenset().union(
        *[checks for position, checks in hits if position == first]
    )


def _touches(finding: Finding, node_ids: set) -> bool:
    if finding.locus is None:
        return False
    return bool(set(finding.locus.node_ids) & node_ids)


def _relevant(result, node_ids: set, checks: frozenset[str]) -> list[Finding]:
    """A named topic settles it; otherwise, whatever touches the pieces named.

    A topic is the stronger signal. Somebody asking about the path has
    mentioned the counter only to say where the path goes, and answering about
    the counter would be answering a question they did not ask.
    """
    if checks:
        return [f for f in result.findings if f.check_id in checks]
    if not node_ids:
        return list(result.findings)
    return [f for f in result.findings if _touches(f, node_ids)]


def _leading(relevant: list[Finding]) -> Finding:
    return min(relevant, key=lambda finding: OUTCOME_ORDER[finding.outcome])


def _sentence(finding: Finding) -> str:
    parts = [finding.title + ".", finding.detail]
    if finding.fix:
        parts.append(finding.fix)
    return " ".join(parts)


SCOPED_TO_A_PLACE = frozenset({"turning_space", "turn_clear_width"})
"""Checks the standard only asks for somewhere in particular.

ADA 2010 304.3 wants a turning space where a room's own section requires one,
such as a restroom. A shop floor has none of those, so the check reports
nothing — and somebody standing at the counter wondering whether they could
turn round there has asked a real question anyway. So it gets measured, with
what turning needs said plainly, and no claim that anything failed.
"""


def _measured_on_request(
    query: Query, checks: frozenset[str], context: AskContext
) -> Answer | None:
    from ..checks.clear_floor import square_side
    from ..numbers import inches

    if not checks <= SCOPED_TO_A_PLACE:
        return None
    found = subjects.resolve(
        context.graph, query.subject_node_ids, query.subject_labels
    )
    if not found:
        return None

    rule = context.rules.by_id("turning_space")
    needed = rule.parameter("circle_diameter_inches")
    at = _in_front_of(found[0])
    space = context.measure.turning_space(context.graph, at)
    side = square_side(space)
    return Answer(
        text=f"There is {inches(side)} of clear floor beside the "
        f"{found[0].label.casefold()}. Turning a wheelchair round needs "
        f"{inches(needed)}.",
        kind="CHECK",
        query=query,
        subjects=(found[0].id,),
        locus=subject_locus(found, inches(side)),
        data={
            "measured_inches": round(side, 2),
            "required_inches": needed,
            "citation": rule.citation.display(),
            "checks": sorted(checks),
        },
    )


def _in_front_of(node):
    """A clear floor space out from the node's shallow face."""
    from standardphysics_contracts import Vec3, to_meters

    from ..checks.turning_space import APPROACH_SETBACK_INCHES

    centre = node.transform.position
    setback = to_meters(APPROACH_SETBACK_INCHES)
    if node.dimensions.x >= node.dimensions.y:
        return Vec3(x=centre.x, y=centre.y - node.dimensions.y / 2 - setback, z=0.0)
    return Vec3(x=centre.x - node.dimensions.x / 2 - setback, y=centre.y, z=0.0)


@traced("ask.check")
def check(query: Query, context: AskContext) -> Answer:
    found = subjects.resolve(
        context.graph, query.subject_node_ids, query.subject_labels
    )
    checks = topics_in(query.restated)
    named_something = bool(query.subject_labels or query.subject_node_ids)
    if named_something and not found and not checks:
        return _nothing_covers_it(query)

    result = context.baseline()
    relevant = _relevant(result, {node.id for node in found}, checks)
    if not relevant:
        return _measured_on_request(query, checks, context) or _nothing_covers_it(
            query
        )

    leading = _leading(relevant)
    return Answer(
        text=_sentence(leading),
        kind="CHECK",
        query=query,
        subjects=tuple(node.id for node in found),
        locus=leading.locus,
        data={
            "outcome": leading.outcome,
            "citation": leading.citation.display(),
            "measured_inches": leading.measured_inches,
            "required_inches": leading.required_inches,
            "checks": sorted({f.check_id for f in relevant}),
        },
        findings=tuple(relevant),
    )


def _nothing_covers_it(query: Query) -> Answer:
    asked = query.subject_labels[0].casefold() if query.subject_labels else "that"
    return Answer(
        text=f"We measure {WHAT_WE_CHECK}. Ask about one of those and we will "
        "tell you where it stands.",
        kind="CHECK",
        query=query,
        data={"asked_about": asked},
    )
