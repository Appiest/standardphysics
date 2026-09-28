"""Per-room evidence for every rule and directive in the configured corpus.

Legal sign-off and a room measurement are independent. A measured result cannot
turn a preview rule or an unsigned directive into a verified requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Literal

from standardphysics_contracts import MeasurementProvider, Scenario, SceneGraph, to_inches
from standardphysics_contracts.findings import Asks
from standardphysics_contracts.precedents import PrecedentDirective, PrecedentQuerySpec, SpaceTypology
from standardphysics_pipeline import footprint, gap_between

from .checks import REGISTRY, CheckContext, Observation, roles
from .checks.dining import required_count, surface_height_inches, within_range
from .checks.door_clearance import latch_sides_clear
from .checks.result import as_result
from .checks.route_geometry import stops_needing_turning_space
from .precedents import PrecedentCompiler, check_precedent_constraints
from .precedents.verification import PrecedentLedger, load_precedent_ledger, load_precedents
from .rules import AgentRulePack, RuleSpec, VerificationLedger, load_ledger, load_pack

Outcome = Literal["pass", "fail", "unknown", "unverified"]
Source = Literal["rule", "query", "constraint"]

# A rule_id in the corpus is only a hint. Each query needs a metric-specific
# adapter; adding a rule mapping alone must not silently increase coverage.
QUERY_IMPLEMENTATIONS = {
    "service_counter_height": "service_counter_height",
    "service_counter_clear_length": "counter_section_geometry",
    "route_clear_width": "route_clear_width",
    "dining_surface_height_max": "dining_surface_geometry",
    "dining_surface_height_min": "dining_surface_geometry",
    "door_clear_width": "door_clear_width",
    "service_counter_approach_width": "service_counter_approach",
    "self_service_dispenser_reach": "self_service_reach",
    "kiosk_operable_part_height": "kiosk_reach",
    "kiosk_clear_floor_width": "kiosk_clear_floor",
    "restroom_turning_circle": "restroom_turning_space",
    "door_pull_side_latch_clearance": "door_latch_side_strips",
}

COMPARED_OBSERVATIONS = frozenset({"service_counter_height", "route_clear_width", "door_clear_width"})
"""Adapters that compare a check's measurement against the query's own threshold."""

JUDGED_OBSERVATIONS = frozenset(
    {"service_counter_approach", "self_service_reach", "kiosk_reach", "kiosk_clear_floor", "restroom_turning_space"}
)
"""Adapters that take the check's own verdict, because its measurement is a tested size, not a free reading."""


@dataclass(frozen=True)
class QueryAsk:
    """The one thing a person can send that settles a query the scan cannot."""

    asks: Asks
    request: str


QUERY_ASKS: dict[str, QueryAsk] = {
    "service_counter_height": QueryAsk(
        "measurement", "Measure the height of the lowest part of the ordering counter; it needs to be 36 inches or lower."),
    "service_counter_clear_length": QueryAsk(
        "measurement", "Measure how much of the low counter is left clear of registers and displays; it needs 36 inches."),
    "service_counter_approach_width": QueryAsk(
        "photo", "Send a photo of the floor in front of the low part of the counter, showing a 30 by 48 inch space "
        "to pull up alongside it."),
    "service_counter_alcove_width": QueryAsk(
        "measurement", "If displays or baskets in front of the counter make a nook more than 15 inches deep, measure "
        "how wide it is; it needs 60 inches."),
    "self_service_dispenser_reach": QueryAsk(
        "measurement", "Measure from the floor to the highest straw, lid or napkin a customer reaches for; it needs to "
        "be 48 inches or lower."),
    "route_clear_width": QueryAsk(
        "another_look", "Walk the phone along the path again so its narrowest point can be measured; it needs 36 inches."),
    "dining_surface_height_max": QueryAsk(
        "measurement", "Measure the height of the accessible table top; it needs to be 34 inches or lower."),
    "dining_surface_height_min": QueryAsk(
        "measurement", "Measure the height of the accessible table top; it needs to be 28 inches or higher."),
    "dining_knee_clearance_height": QueryAsk(
        "measurement", "Measure from the floor to the underside of the accessible table where knees go; it needs "
        "27 inches."),
    "dining_knee_clearance_width": QueryAsk(
        "measurement", "Measure the clear width under the accessible table between its legs or base; it needs "
        "30 inches."),
    "door_clear_width": QueryAsk(
        "measurement", "Open the door all the way and measure from the face of the door to the frame; it needs "
        "32 inches."),
    "door_pull_side_latch_clearance": QueryAsk(
        "photo", "Send a photo of the pull side of the door showing the floor beside the handle; it needs 18 inches "
        "clear past the latch edge."),
    "door_threshold_height": QueryAsk(
        "photo", "Send a low photo of the doorway threshold with a tape measure standing beside it; it can be half an "
        "inch high at most."),
    "restroom_turning_circle": QueryAsk(
        "photo", "Send a photo of the restroom from the doorway with the whole floor in view, to check for a 60 inch "
        "circle to turn in."),
    "kiosk_operable_part_height": QueryAsk(
        "measurement", "Measure from the floor to the kiosk's highest button, card slot or top of its touch screen; "
        "it needs to be 48 inches or lower."),
    "kiosk_clear_floor_width": QueryAsk(
        "photo", "Send a photo of the floor in front of the kiosk, showing a clear 30 by 48 inch space to pull up "
        "to it."),
    "wheelchair_space_width": QueryAsk(
        "measurement", "Measure the width of each wheelchair space in the seating; each needs 36 inches."),
    "wheelchair_space_depth": QueryAsk(
        "measurement", "Measure the depth of each wheelchair space in the seating; each needs 48 inches entered from "
        "the front or back, and 60 inches from the side."),
}
"""What settles each corpus query when the scan cannot: a measured query whose target was not seen, or one the
scan never carries, such as the knee space under a table or a threshold's height."""


@dataclass(frozen=True)
class RequirementEvidence:
    target: str
    measured_value: float | None
    detail: str
    reliable: bool = True
    measured: Outcome | None = None
    """This one measurement's result, where the entry measures more than one target."""


@dataclass(frozen=True)
class RequirementEntry:
    id: str
    source: Source
    citation: str
    target: str
    threshold: float | None
    comparison: str | None
    unit: str | None
    applicable: bool
    outcome: Outcome | None
    reason: str
    implementation: str | None = None
    evidence: tuple[RequirementEvidence, ...] = ()
    measured: Outcome | None = None
    """What the room measurement says with legal sign-off set aside; never "unverified"."""

    ask: QueryAsk | None = None
    """For a query whose measurement is unknown, the one thing a person can send that settles it."""


@dataclass(frozen=True)
class ComplianceResult:
    entries: tuple[RequirementEntry, ...]
    all_verified_measurable_passed: bool
    accept_for_final_layout: bool
    passed: tuple[str, ...] = field(default_factory=tuple)
    failed: tuple[str, ...] = field(default_factory=tuple)
    unknown: tuple[str, ...] = field(default_factory=tuple)
    unverified: tuple[str, ...] = field(default_factory=tuple)
    measured_failed: tuple[str, ...] = field(default_factory=tuple)
    """Applicable entries whose measurement fails, whether or not a person has signed the rule off."""
    measured_unknown: tuple[str, ...] = field(default_factory=tuple)


def _applicable_rule(rule: RuleSpec, graph: SceneGraph, scenario: Scenario, typology: SpaceTypology | None) -> bool:
    operable_names = ("operable", "dispenser", "kiosk", "terminal", "touchscreen", "card reader", "register")
    targets = {
        "route": True,
        "route_leg": len(scenario.stops) > 1,
        "route_turn": len(scenario.stops) > 2,
        "turning_room": bool(stops_needing_turning_space(scenario.stops)),
        "door": bool(roles.doors(graph)),
        "entrance": roles.entrance(graph) is not None,
        "service_counter": bool(roles.service_counters(graph)),
        "point_of_sale": bool(roles.point_of_sale(graph)),
        "dining_surface": bool(roles.dining_surfaces(graph)),
        "floor": bool(roles.floors(graph)),
        "restroom": typology == SpaceTypology.RESTROOM_FACILITY,
        "operable_part": any(any(name in f"{node.label} {node.raw_category}".lower() for name in operable_names)
                             for node in graph.nodes),
        "wall_mounted": any(node.attachment is not None for node in graph.nodes),
        "ramp": bool(roles.ramps(graph)),
        "handrail": bool(roles.ramps(graph)),
        "kiosk": bool(roles.kiosks(graph)),
        "self_service": bool(roles.self_service(graph)),
        "post_mounted": any("post" in node.label.lower() for node in graph.nodes),
    }
    return any(targets.get(target, True) for target in rule.applies_to)


def _observations(ctx: CheckContext) -> tuple[dict[str, list[Observation]], dict[str, str]]:
    measured: dict[str, list[Observation]] = {}
    gaps: dict[str, str] = {}
    active = {rule.id for rule in ctx.rules.rules}
    for rule_ids, check in REGISTRY:
        if not rule_ids & active:
            continue
        try:
            result = as_result(check(ctx))
        except (AttributeError, NotImplementedError) as exc:
            for rule_id in rule_ids & active:
                gaps[rule_id] = f"measurement provider could not answer: {exc}"
            continue
        for observation in result.observations:
            if observation.rule_id in active:
                measured.setdefault(observation.rule_id, []).append(observation)
        for gap in result.unevaluated:
            gaps[gap.rule_id] = gap.waiting_on
    return measured, gaps


def _observation_outcome(observation: Observation, graph: SceneGraph) -> Outcome:
    if observation.asks_for or roles.needs_another_look(graph, observation.relied_on):
        return "unknown"
    if observation.reason == "unreachable":
        return "fail"
    if observation.measured_inches is None and observation.reason != "clear":
        return "unknown"
    return "pass" if observation.satisfied else "fail"


def _combined(outcomes: list[Outcome]) -> Outcome:
    if "fail" in outcomes:
        return "fail"
    if "unknown" in outcomes or not outcomes:
        return "unknown"
    return "pass"


def _observation_target(observation: Observation, fallback: str) -> str:
    origin = observation.facts.get("origin")
    destination = observation.facts.get("destination")
    if origin and destination:
        return f"{origin} to {destination}"
    if destination:
        return f"route to {destination}"
    if observation.facts.get("stop"):
        return str(observation.facts["stop"])
    if observation.facts.get("exit"):
        return f"route to {observation.facts['exit']}"
    return ", ".join(str(node_id) for node_id in observation.relied_on) or fallback


def _rule_entry(rule: RuleSpec, ctx: CheckContext, observations: list[Observation], gap: str | None,
                applicable: bool) -> RequirementEntry:
    if rule.id == "turn_clear_width" and not observations and not gap:
        applicable = False
    if observations and all(item.facts.get("applies") is False for item in observations):
        applicable = False
    verified = ctx.ledger.personally_verified(rule)
    values = tuple(RequirementEvidence(
        target=_observation_target(observation, "/".join(rule.applies_to)),
        measured_value=observation.measured_inches,
        detail=observation.reason,
        measured=_observation_outcome(observation, ctx.graph),
    ) for observation in observations)
    outcome: Outcome | None = _combined([_observation_outcome(item, ctx.graph) for item in observations])
    if gap:
        outcome = "unknown"
    measured = outcome if applicable else None
    if not verified:
        outcome = "unverified"
    if not applicable:
        outcome = None
    reason = "rule condition or target does not apply in this room" if not applicable else gap or (
        "human legal review is missing or preview-only" if not verified else
        "no room measurement was produced" if not observations else "checker evidence recorded"
    )
    implementation = next((check.__name__ for ids, check in REGISTRY if rule.id in ids), None)
    return RequirementEntry(
        id=f"rule:{rule.id}", source="rule", citation=f"{rule.citation.authority}_{rule.citation.section}",
        target="; ".join(dict.fromkeys(value.target for value in values)) or ", ".join(rule.applies_to),
        threshold=rule.threshold, comparison=rule.comparison,
        unit=rule.unit, applicable=applicable, outcome=outcome, reason=reason,
        implementation=implementation, evidence=values, measured=measured,
    )


def _compared_values(ctx: CheckContext, query: PrecedentQuerySpec,
                     observations: dict[str, list[Observation]]) -> tuple[RequirementEvidence, ...]:
    return tuple(RequirementEvidence(
        _observation_target(item, query.target_role),
        item.measured_inches, item.reason, _observation_outcome(item, ctx.graph) != "unknown",
    ) for item in observations.get(query.rule_id or "", []))


def _judged_values(ctx: CheckContext, rule_id: str,
                   observations: dict[str, list[Observation]]) -> tuple[RequirementEvidence, ...]:
    return tuple(RequirementEvidence(
        _observation_target(item, rule_id), item.measured_inches, item.reason,
        _observation_outcome(item, ctx.graph) != "unknown", measured=_observation_outcome(item, ctx.graph),
    ) for item in observations.get(rule_id, []))


def _dining_values(ctx: CheckContext) -> tuple[RequirementEvidence, ...]:
    return tuple(RequirementEvidence(str(node.id), surface_height_inches(node),
                                     "surface top from scan geometry", node.quality != "needs_another_look")
                 for node in roles.dining_surfaces(ctx.graph))


def _latch_side_values(ctx: CheckContext) -> tuple[RequirementEvidence, ...]:
    """18 inches past the latch on the pull side. The scan knows neither the latch side nor the pull side, so
    only floor clear on both sides of the opening settles it; anything else is a question."""
    rule = ctx.rule("door_maneuvering_clearance")
    latch = rule.parameter("front_approach_pull_latch_side_inches")
    values = []
    for door in roles.doors(ctx.graph):
        both = all(latch_sides_clear(ctx, rule, door))
        values.append(RequirementEvidence(
            str(door.id), latch if both else None,
            "floor clear beside both edges of the opening" if both else "latch side and swing are unknown",
            both and door.quality != "needs_another_look", measured="pass" if both else "unknown",
        ))
    return tuple(values)


GeometryAdapter = Callable[[CheckContext], tuple[RequirementEvidence, ...]]

GEOMETRY_ADAPTERS: dict[str, GeometryAdapter] = {
    "counter_section_geometry": lambda ctx: _counter_length_values(ctx.graph),
    "dining_surface_geometry": _dining_values,
    "door_latch_side_strips": _latch_side_values,
}


def _query_values(query: PrecedentQuerySpec, ctx: CheckContext,
                  observations: dict[str, list[Observation]]) -> tuple[RequirementEvidence, ...]:
    implementation = QUERY_IMPLEMENTATIONS.get(query.query_id)
    if implementation in GEOMETRY_ADAPTERS:
        return GEOMETRY_ADAPTERS[implementation](ctx)
    if implementation in COMPARED_OBSERVATIONS:
        return _compared_values(ctx, query, observations)
    if implementation in JUDGED_OBSERVATIONS:
        return _judged_values(ctx, implementation, observations)
    return ()


def _counter_length_values(graph: SceneGraph) -> tuple[RequirementEvidence, ...]:
    values = []
    for counter in roles.service_counters(graph):
        adjacent = [section for section in roles.lowered_sections(graph)
                    if gap_between(footprint(section), footprint(counter)) <= 0.05]
        section = adjacent[0] if adjacent else counter
        values.append(RequirementEvidence(
            str(counter.id), to_inches(max(section.dimensions.x, section.dimensions.y)),
            f"length of section {section.id} beside counter {counter.id} from scan geometry",
            counter.quality != "needs_another_look" and section.quality != "needs_another_look",
        ))
    return tuple(values)


def _value_outcome(value: RequirementEvidence, query: PrecedentQuerySpec) -> Outcome:
    if value.measured is not None:
        return value.measured
    if value.measured_value is None or not value.reliable:
        return "unknown"
    within = (value.measured_value <= query.threshold if query.comparison == "at_most"
              else value.measured_value >= query.threshold)
    return "pass" if within else "fail"


def _query_reason(verified: bool, implementation: str | None, values: tuple, outcomes: list[Outcome],
                  ask: QueryAsk | None) -> str:
    if not verified:
        return "human directive or linked rule review is missing"
    if values and "unknown" not in outcomes:
        return "metric-specific evidence recorded"
    if ask is not None:
        return f"needs a {ask.asks.replace('_', ' ')}: {ask.request}"
    if implementation is None:
        return "no metric-specific checker implementation"
    return "required measurement is missing"


def _query_entry(directive: PrecedentDirective, query: PrecedentQuerySpec, ctx: CheckContext,
                 observations: dict[str, list[Observation]], verified: bool) -> RequirementEntry:
    implementation = QUERY_IMPLEMENTATIONS.get(query.query_id)
    values = _query_values(query, ctx, observations)
    outcomes = [_value_outcome(value, query) for value in values]
    measured = _combined(outcomes)
    ask = QUERY_ASKS.get(query.query_id) if measured == "unknown" else None
    values = tuple(replace(value, measured=result) for value, result in zip(values, outcomes))
    return RequirementEntry(
        id=f"query:{directive.directive_id}:{query.query_id}", source="query", citation=query.citation,
        target=query.target_role, threshold=query.threshold, comparison=query.comparison, unit="in",
        applicable=True, outcome=measured if verified else "unverified",
        reason=_query_reason(verified, implementation, values, outcomes, ask),
        implementation=implementation, evidence=values, measured=measured, ask=ask,
    )


def _query_target_present(query: PrecedentQuerySpec, graph: SceneGraph) -> bool:
    if query.target_role == "route_leg":
        return True
    if query.target_role == "service_counter":
        return bool(roles.service_counters(graph))
    if query.target_role == "dining_surface":
        return bool(roles.dining_surfaces(graph))
    if query.target_role == "door":
        return bool(roles.doors(graph))
    if query.target_role == "straw_dispenser":
        return bool(roles.self_service(graph)) or any(
            "straw" in f"{node.label} {node.raw_category}".lower() for node in graph.nodes)
    if query.target_role == "service_counter_alcove":
        return bool(roles.service_counters(graph))
    return True


def _constraint_specs(directive: PrecedentDirective) -> list[tuple[str, str, str]]:
    specs: list[tuple[str, str, str]] = [
        (f"fixed_role:{role}", directive.authority[0], role) for role in directive.constraints.fixed_roles
    ]
    if directive.constraints.requires_accessible_dining:
        specs.append(("accessible_dining_share", "ADA_2010_226.1", "dining_surface"))
    if directive.constraints.dispersed:
        specs.append(("dining_dispersion", "ADA_2010_226.2", "dining_surface"))
    return specs


def _constraint_entry(directive: PrecedentDirective, spec: tuple[str, str, str], base: SceneGraph | None,
                      candidate: SceneGraph, verified: bool, violations, pack: AgentRulePack) -> RequirementEntry:
    name, citation, target = spec
    baseline = base or candidate
    fixed_ids = {str(node.id) for node in roles.service_counters(baseline)} if target == "service_counter" else (
        {str(node.id) for node in roles.point_of_sale(baseline)} if target == "point_of_sale" else set()
    )
    applicable = not name.startswith("fixed_role") or bool(fixed_ids)
    relevant = [v for v in violations if v.directive_id == directive.directive_id and (
        (name.startswith("fixed_role") and v.rule_broken == "moved_fixed_role" and v.target_node_id in fixed_ids) or
        (name == "accessible_dining_share" and v.rule_broken == "too_few_accessible_dining_surfaces") or
        (name == "dining_dispersion" and v.rule_broken == "accessible_dining_set_apart")
    )]
    measurable = base is not None or not name.startswith("fixed_role")
    outcome: Outcome | None = "unknown" if not measurable or name in {"dining_dispersion", "accessible_dining_share"} else (
        "fail" if relevant else "pass"
    )
    if relevant:
        outcome = "fail"
    measured = outcome if applicable else None
    if not verified:
        outcome = "unverified"
    if not applicable:
        outcome = None
    evidence: tuple[RequirementEvidence, ...] = tuple(
        RequirementEvidence(v.target_node_id or target, None, v.detail, measured="fail") for v in relevant
    )
    threshold = None
    if name == "accessible_dining_share":
        surfaces = roles.dining_surfaces(candidate)
        rule = pack.by_id("dining_surface_height")
        accessible = sum(within_range(surface_height_inches(node), rule) for node in surfaces)
        threshold = float(required_count(len(surfaces), rule))
        evidence += (RequirementEvidence(target, float(accessible), "height-qualified surfaces; knee space unmeasured"),)
    return RequirementEntry(
        id=f"constraint:{directive.directive_id}:{name}", source="constraint", citation=citation,
        target=target, threshold=threshold, comparison="at_least" if threshold is not None else None,
        unit="surfaces" if threshold is not None else None, applicable=applicable, outcome=outcome,
        reason=("fixed role is absent from the room" if not applicable else
                "human directive review is missing" if not verified else
                "baseline room is required to check moved fixtures" if not measurable else
                "knee clearance is unmeasured" if name == "accessible_dining_share" and not relevant else
                "five-metre dispersion screen is not a legal pass criterion" if name == "dining_dispersion" and not relevant else
                "directive geometry check recorded"),
        implementation="check_precedent_constraints", evidence=evidence, measured=measured,
    )


def _inapplicable(directive: PrecedentDirective, reason: str) -> list[RequirementEntry]:
    entries = [RequirementEntry(
        id=f"query:{directive.directive_id}:{query.query_id}", source="query", citation=query.citation,
        target=query.target_role, threshold=query.threshold, comparison=query.comparison, unit="in",
        applicable=False, outcome=None, reason=reason, implementation=QUERY_IMPLEMENTATIONS.get(query.query_id),
    ) for query in directive.inspection_queries]
    entries.extend(RequirementEntry(
        id=f"constraint:{directive.directive_id}:{name}", source="constraint", citation=citation,
        target=target, threshold=None, comparison=None, unit=None, applicable=False, outcome=None,
        reason=reason, implementation="check_precedent_constraints",
    ) for name, citation, target in _constraint_specs(directive))
    return entries


def _inapplicable_query(directive: PrecedentDirective, query: PrecedentQuerySpec) -> RequirementEntry:
    return RequirementEntry(
        id=f"query:{directive.directive_id}:{query.query_id}", source="query", citation=query.citation,
        target=query.target_role, threshold=query.threshold, comparison=query.comparison, unit="in",
        applicable=False, outcome=None, reason="target role is absent from the room scan",
        implementation=QUERY_IMPLEMENTATIONS.get(query.query_id),
    )


def evaluate_candidate_room(
    candidate: SceneGraph, scenario: Scenario, measure: MeasurementProvider, typology: SpaceTypology,
    *, base: SceneGraph | None = None, rules: AgentRulePack | None = None,
    rule_ledger: VerificationLedger | None = None, directives: list[PrecedentDirective] | None = None,
    directive_ledger: PrecedentLedger | None = None,
) -> ComplianceResult:
    """Evaluate the full corpus for one candidate without asserting legal sign-off.

    `base` is the layout before rearrangement. Without it, fixed-role movement
    constraints remain unknown. This result contains no usability or removal cost.
    """
    pack = rules or load_pack()
    reviewed_rules = rule_ledger if rule_ledger is not None else load_ledger()
    corpus = directives if directives is not None else load_precedents(allow_unverified=True)
    reviewed_directives = directive_ledger if directive_ledger is not None else load_precedent_ledger()
    ctx = CheckContext(candidate, scenario, measure, pack, reviewed_rules)
    observations, gaps = _observations(ctx)
    entries = [_rule_entry(rule, ctx, observations.get(rule.id, []), gaps.get(rule.id),
                           _applicable_rule(rule, candidate, scenario, typology))
               for rule in pack.rules]
    matched = PrecedentCompiler(corpus).match(typology, candidate.nodes)
    matched_ids = {directive.directive_id for directive in matched}
    violations = check_precedent_constraints(base or candidate, candidate, matched)
    for directive in corpus:
        if directive.directive_id not in matched_ids:
            entries.extend(_inapplicable(directive, "room typology or required entity does not match"))
            continue
        verified = reviewed_directives.is_verified(directive.directive_id)
        for query in directive.inspection_queries:
            if not _query_target_present(query, candidate):
                entries.append(_inapplicable_query(directive, query))
                continue
            linked = query.rule_id is None or reviewed_rules.personally_verified(pack.by_id(query.rule_id))
            entries.append(_query_entry(directive, query, ctx, observations, verified and linked))
        entries.extend(_constraint_entry(directive, spec, base, candidate, verified, violations, pack)
                       for spec in _constraint_specs(directive))
    applicable = [entry for entry in entries if entry.applicable]
    buckets = {outcome: tuple(entry.id for entry in applicable if entry.outcome == outcome)
               for outcome in ("pass", "fail", "unknown", "unverified")}
    verified_measurable = [entry for entry in applicable if entry.outcome != "unverified"]
    return ComplianceResult(
        entries=tuple(entries),
        all_verified_measurable_passed=bool(verified_measurable) and all(
            entry.outcome == "pass" for entry in verified_measurable),
        accept_for_final_layout=bool(applicable) and all(entry.outcome == "pass" for entry in applicable),
        passed=buckets["pass"], failed=buckets["fail"], unknown=buckets["unknown"],
        unverified=buckets["unverified"],
        measured_failed=tuple(entry.id for entry in applicable if entry.measured == "fail"),
        measured_unknown=tuple(entry.id for entry in applicable if entry.measured == "unknown"),
    )
