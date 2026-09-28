"""ADA 2010 904.4.1, the clear floor space 305.3 it refers to, and the register.

Two numbers on the counter itself: how high it is, and whether there is room
to pull up alongside it. A third when a lowered section already exists: whether
people actually pay there. 904.4.1 is met by providing the portion. The
complaint in the pitch is that the point of sale still sat on the high part.
"""

from __future__ import annotations

from standardphysics_contracts import SceneNode, to_inches, to_meters
from standardphysics_pipeline import footprint, gap_between, region_locus
from standardphysics_pipeline.locus import height_locus

from ..rules import RuleSpec
from ..tracing import traced
from . import roles
from .clear_floor import fits_rectangle
from .context import CheckContext
from .observation import Observation
from .rectangles import facing, intruders, rectangle
from .vertical import mounted_locus

HEIGHT_RULE = "service_counter_height"
APPROACH_RULE = "service_counter_approach"
POS_RULE = "point_of_sale_height"

ADJACENT_METERS = 0.05
"""Five centimetres. Two counter sections that share an edge read as touching."""


def _portion_for(graph, counter, rule: RuleSpec) -> SceneNode | None:
    """The 36 by 36 inch section 904.4.1 asks for, if one stands next to this counter."""
    max_height = rule.threshold
    min_length = rule.parameter("accessible_length_min_inches")
    counter_foot = footprint(counter)
    for node in roles.lowered_sections(graph):
        height = _upper_height(node)
        length = to_inches(max(node.dimensions.x, node.dimensions.y))
        if height > max_height or length < min_length:
            continue
        if gap_between(footprint(node), counter_foot) > ADJACENT_METERS:
            continue
        return node
    return None


def _upper_height(node: SceneNode) -> float:
    if node.top_surface is None:
        return to_inches(node.dimensions.z)
    surface = node.top_surface
    if surface.height_m is None or surface.uncertainty_m is None:
        return float("inf")
    return to_inches(surface.height_m + surface.uncertainty_m)


def _section_under(item: SceneNode, surfaces: list[SceneNode]) -> SceneNode | None:
    """Which counter section the item stands at, by floor position."""
    item_foot = footprint(item)
    for surface in surfaces:
        if gap_between(item_foot, footprint(surface)) == 0.0:
            return surface
    return None


@traced("checks.service_counter_height")
def service_counter_height(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(HEIGHT_RULE)
    observations = []
    for counter in roles.service_counters(ctx.graph):
        portion = _portion_for(ctx.graph, counter, rule)
        target = portion or counter
        result = ctx.measure.counter_height(ctx.graph, target.id)
        relied_on = (counter.id,) if portion is None else (counter.id, target.id)
        observations.append(
            Observation(
                rule_id=HEIGHT_RULE,
                satisfied=_height_satisfied(rule, result),
                measured_inches=None if result.needs_measurement else result.inches,
                required_inches=rule.threshold,
                relied_on=relied_on,
                locus=mounted_locus(target) if result.needs_measurement else height_locus(target, result),
                facts={
                    "counter": counter.label,
                    "portion": None if portion is None else portion.label,
                    "accessible_length_inches": rule.parameter(
                        "accessible_length_min_inches"
                    ),
                    "uncertainty_inches": result.uncertainty_inches,
                },
                dedupe_key=(HEIGHT_RULE, str(counter.id)),
                reason="measured" if not result.needs_measurement else "unmeasured_mesh_top",
                asks_for="measurement" if result.needs_measurement or _height_ambiguous(rule, result) else None,
            )
        )
    return observations


def _height_satisfied(rule: RuleSpec, result) -> bool:
    return not result.needs_measurement and rule.satisfied_by(result.inches + (result.uncertainty_inches or 0.0))


def _height_ambiguous(rule: RuleSpec, result) -> bool:
    if result.needs_measurement or result.uncertainty_inches is None:
        return False
    return result.inches - result.uncertainty_inches <= rule.threshold < result.inches + result.uncertainty_inches


@traced("checks.service_counter_approach")
def service_counter_approach(ctx: CheckContext) -> list[Observation]:
    """The clear floor space for a parallel approach, beside the part of the
    counter 904.4.1 asks for.

    904.4.1 places the space "adjacent to the 36 inch minimum length of
    counter", so where a lowered section stands beside a counter the space is
    measured in front of that section. Measuring in front of the middle of the
    whole counter tested floor by the high part, where nobody in a wheelchair
    is served.

    Nothing asks for the space to be centred. It may sit anywhere along the
    face as long as it runs alongside 36 inches of that counter, so a sign at
    one end of a long counter does not fail it while the middle is clear. The
    forward approach of 904.4.2 is not searched: it needs knee and toe space
    under the counter, which a scan does not show.
    """
    rule = ctx.rule(APPROACH_RULE)
    height_rule = ctx.rule(HEIGHT_RULE)
    observations = []
    for counter in roles.service_counters(ctx.graph):
        at = _portion_for(ctx.graph, counter, height_rule) or counter
        slide = _slide_meters(at, rule, height_rule)
        result = ctx.measure.counter_approach(ctx.graph, at.id, slide_meters=slide)
        observations.append(_approach(ctx, rule, counter, at, result))
    return observations


def _slide_meters(at: SceneNode, rule: RuleSpec, height_rule: RuleSpec) -> float:
    """How far off the face centre the space may sit and still run alongside
    the 36 inch portion.

    The space's long side lies along the face, so it overlaps the face by at
    least the portion's length while its centre stays within half the face plus
    half the space, less that length, of the face centre. A face shorter than
    the portion has to be covered whole.
    """
    face = at.dimensions.x
    portion = min(to_meters(height_rule.parameter("accessible_length_min_inches")), face)
    space = to_meters(rule.parameter("clear_width_min_inches"))
    return max(0.0, (face + space) / 2 - portion)


def _approach(ctx: CheckContext, rule: RuleSpec, counter, at, result) -> Observation:
    required_wide = rule.parameter("clear_width_min_inches")
    required_deep = rule.parameter("clear_depth_min_inches")
    satisfied = fits_rectangle(result, required_wide, required_deep)
    beside = frozenset({counter.id, at.id})
    rotation = facing(ctx.graph, at)
    return Observation(
        rule_id=APPROACH_RULE,
        satisfied=satisfied,
        measured_inches=min(result.inches_wide, result.inches_deep),
        required_inches=rule.threshold,
        relied_on=(counter.id,) if at is counter else (counter.id, at.id),
        locus=region_locus(result, [at.id, *(
            intruders(ctx.graph, rectangle(
                result.center, to_meters(required_wide), to_meters(required_deep), rotation,
            ), ignoring=beside) if not satisfied else []
        )], rotation=rotation),
        facts={
            "counter": counter.label,
            "measured_wide": result.inches_wide,
            "measured_deep": result.inches_deep,
            "required_wide": required_wide,
            "required_deep": required_deep,
        },
        dedupe_key=(APPROACH_RULE, str(counter.id)),
        reason="measured" if satisfied else "too_small",
    )


def _portions_beside(graph, counters, height_rule: RuleSpec) -> list[SceneNode]:
    found = []
    for counter in counters:
        portion = _portion_for(graph, counter, height_rule)
        if portion is not None:
            found.append(portion)
    return found


@traced("checks.point_of_sale_height")
def point_of_sale_height(ctx: CheckContext) -> list[Observation]:
    """Whether people pay at the accessible section, when one exists."""
    height_rule = ctx.rule(HEIGHT_RULE)
    rule = ctx.rule(POS_RULE)
    counters = roles.service_counters(ctx.graph)
    portions = _portions_beside(ctx.graph, counters, height_rule)
    if not portions:
        return []

    surfaces = [*counters, *portions]
    observations = []
    for reader in roles.point_of_sale(ctx.graph):
        surface = _section_under(reader, surfaces)
        if surface is None:
            continue
        result = ctx.measure.counter_height(ctx.graph, surface.id)
        observations.append(
            Observation(
                rule_id=POS_RULE,
                satisfied=_height_satisfied(rule, result),
                measured_inches=None if result.needs_measurement else result.inches,
                required_inches=rule.threshold,
                relied_on=(reader.id, surface.id),
                locus=mounted_locus(reader),
                facts={
                    "reader": reader.label,
                    "counter": surface.label,
                    "portion": portions[0].label,
                    "uncertainty_inches": result.uncertainty_inches,
                },
                dedupe_key=(POS_RULE, str(reader.id)),
                reason="measured" if not result.needs_measurement else "unmeasured_mesh_top",
                asks_for="measurement" if result.needs_measurement or _height_ambiguous(rule, result) else None,
            )
        )
    return observations
