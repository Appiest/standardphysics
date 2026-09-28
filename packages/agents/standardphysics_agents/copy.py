"""Every sentence a shop owner reads about a finding.

One place, so a wording change is one edit. Section 2 of the plan governs all of
it: short sentences, ordinary words, inches, no jargon, and nothing whose job is
to point at an absence. A title names what is wrong, the line under it gives the
measurement and what is needed, and the fix says what to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .checks.observation import Observation
from .numbers import by, by_size, inches, measured, plural, size, span, things
from .rules import RuleSpec


@dataclass(frozen=True)
class FindingCopy:
    title: str
    detail: str
    fix: str | None = None


STOP_PHRASES = {
    "counter": "the counter",
    "order": "the order counter",
    "pickup": "the pickup spot",
    "checkout": "the checkout",
    "check in": "the check-in desk",
    "seat": "the seats",
    "browse": "the shop floor",
    "wait": "the waiting area",
    "restroom": "the restroom",
    "exit": "the way out",
    "entrance": "the front door",
}


def stop_phrase(name: str | None) -> str:
    if not name:
        return "the next stop"
    return STOP_PHRASES.get(name.strip().casefold(), f"the {name.strip().casefold()}")


def _measured_inches(observation: Observation) -> float:
    if observation.measured_inches is None:
        raise ValueError(f"{observation.rule_id} reached its copy without a measurement")
    return observation.measured_inches


def _route_width(observation: Observation, rule: RuleSpec) -> FindingCopy:
    destination = stop_phrase(observation.facts.get("destination"))
    if observation.reason == "unreachable":
        return _route_blocked(observation, destination)
    needed = inches(rule.threshold)
    shown = measured(_measured_inches(observation), rule.threshold)
    if observation.satisfied:
        return FindingCopy(
            title=f"The path to {destination} fits",
            detail=f"It's {shown} at the tightest point. Wheelchairs need {needed}.",
        )
    return FindingCopy(
        title=f"The path to {destination} is too narrow",
        detail=f"It's {shown} at the tightest point. Wheelchairs need {needed}.",
        fix=_widen_fix(observation, rule),
    )


def _route_blocked(observation: Observation, destination: str) -> FindingCopy:
    blockers = things(observation.facts.get("blockers", []))
    subject = f"{blockers} sit" if blockers else "Something sits"
    return FindingCopy(
        title=f"There's no way through to {destination}",
        detail=f"{subject} across the path, wall to wall.",
        fix="Move enough of it aside to leave a gap, and we'll measure again.",
    )


def _widen_fix(observation: Observation, rule: RuleSpec) -> str:
    """A fix only ever asks for something the owner can actually do.

    Placement is adjustable and inventory is not, and a built-in counter is
    neither. Naming a fixed fixture in a fix would send the owner to shove a
    wall.
    """
    deficit = inches(rule.threshold - _measured_inches(observation))
    movable = things(observation.facts.get("movable_blockers", []))
    fixed = things(observation.facts.get("fixed_blockers", []))
    if movable and len(observation.facts.get("movable_blockers", [])) >= 2:
        return f"Move {movable} {deficit} apart."
    if movable and fixed:
        return f"Move {movable} {deficit} further from {fixed}."
    if movable:
        return f"Move {movable} {deficit} over."
    if fixed:
        return f"Ask a contractor about opening this gap to {inches(rule.threshold)}."
    return f"Clear a path {inches(rule.threshold)} wide."


def _door_width(observation: Observation, rule: RuleSpec) -> FindingCopy:
    door = observation.facts.get("door", "door").casefold()
    needed = inches(rule.threshold)
    shown = measured(_measured_inches(observation), rule.threshold)
    if observation.satisfied:
        return FindingCopy(
            title=f"The {door} is wide enough",
            detail=f"It's {shown} clear. Doorways need {needed}.",
        )
    return FindingCopy(
        title=f"The {door} is too narrow",
        detail=f"It's {shown} clear. Doorways need {needed}.",
        fix=f"Widen the {door} to a {size(rule.threshold)} clear opening.",
    )


def _counter_height(observation: Observation, rule: RuleSpec) -> FindingCopy:
    counter = observation.facts.get("counter", "counter").casefold()
    allowed = inches(rule.threshold)
    length = inches(observation.facts.get("accessible_length_inches", 36.0))
    shown = _height_with_uncertainty(observation, rule)
    portion = observation.facts.get("portion")
    if observation.satisfied and portion:
        return FindingCopy(
            title=f"The {counter} has a section you can order from",
            detail=f"The lowered section is {shown} high. Ordering from a wheelchair needs {allowed} or lower.",
        )
    section = f"Make it {length} long and {allowed} high."
    if observation.satisfied:
        return FindingCopy(
            title=f"The {counter} is a good height to order from",
            detail=f"It's {shown} high. Ordering from a wheelchair needs {allowed} or lower.",
        )
    return FindingCopy(
        title=f"The {counter} is too high to order from",
        detail=f"It's {shown} high. Ordering from a wheelchair needs {allowed} or lower.",
        fix=f"Add a lower section to the {counter}. {section}",
    )


def _point_of_sale(observation: Observation, rule: RuleSpec) -> FindingCopy:
    reader = observation.facts.get("reader", "card reader").casefold()
    portion = observation.facts.get("portion", "lowered section").casefold()
    shown = _height_with_uncertainty(observation, rule)
    allowed = inches(rule.threshold)
    if observation.satisfied:
        return FindingCopy(
            title="People pay at a counter they can reach",
            detail=f"The {reader} sits {shown} up. Ordering from a wheelchair needs {allowed} or lower.",
        )
    return FindingCopy(
        title="People pay at the high counter",
        detail=f"The {reader} sits {shown} up. Ordering from a wheelchair needs {allowed} or lower.",
        fix=f"Move the {reader} to the {portion}.",
    )


def _height_with_uncertainty(observation: Observation, rule: RuleSpec) -> str:
    shown = measured(observation.measured_inches, rule.threshold)
    uncertainty = observation.facts.get("uncertainty_inches")
    return f"{shown} (uncertainty ±{inches(uncertainty)})" if uncertainty is not None else shown


def _counter_approach(observation: Observation, rule: RuleSpec) -> FindingCopy:
    counter = observation.facts.get("counter", "counter").casefold()
    needed = by(
        observation.facts.get("required_wide", rule.threshold),
        observation.facts.get("required_deep", 30.0),
    )
    have = by(
        observation.facts.get("measured_wide", 0.0),
        observation.facts.get("measured_deep", 0.0),
    )
    detail = f"The clear floor beside it is {have}. Pulling up needs {needed}."
    if observation.satisfied:
        return FindingCopy(
            title=f"There's room to pull up to the {counter}", detail=detail
        )
    return FindingCopy(
        title=f"There's not enough room to pull up to the {counter}",
        detail=detail,
        fix=f"Clear a space {needed} beside the {counter}.",
    )


def _passing_space(observation: Observation, rule: RuleSpec) -> FindingCopy:
    needed = inches(rule.threshold)
    if not observation.facts.get("applies", True):
        shown = measured(_measured_inches(observation))
        return FindingCopy(
            title="Two people can pass anywhere on the path",
            detail=f"It's {shown} wide at the tightest point, and passing needs {needed}.",
        )
    route = inches(observation.facts.get("route_width", 0.0))
    spot = inches(observation.measured_inches or 0.0)
    if observation.satisfied:
        return FindingCopy(
            title="There's a spot to step aside on the path",
            detail=f"The path narrows to {route}, and the widest spot to wait is {spot}.",
        )
    return FindingCopy(
        title="There's nowhere to pass another customer",
        detail=f"The path narrows to {route}, and the widest spot to wait is {spot}. Passing needs {needed}.",
        fix=f"Clear a {size(rule.threshold)} square somewhere along the path.",
    )


def _turning_space(observation: Observation, rule: RuleSpec) -> FindingCopy:
    stop = stop_phrase(observation.facts.get("stop"))
    needed = inches(rule.threshold)
    shown = measured(_measured_inches(observation), rule.threshold)
    if observation.satisfied:
        return FindingCopy(
            title=f"There's room to turn around at {stop}",
            detail=f"The clear floor is {shown} across. Turning a wheelchair needs {needed}.",
        )
    return FindingCopy(
        title=f"There's not enough room to turn around at {stop}",
        detail=f"The clear floor is {shown} across. Turning a wheelchair needs {needed}.",
        fix=f"Clear a {size(rule.threshold)} circle at {stop}.",
    )


def _restroom_turning(observation: Observation, rule: RuleSpec) -> FindingCopy:
    needed = inches(rule.threshold)
    shown = measured(observation.measured_inches, rule.threshold)
    if observation.satisfied:
        return FindingCopy(
            title="There's room to turn around in the restroom",
            detail=f"The widest clear circle is {shown} across. Turning a wheelchair needs {needed}.",
        )
    blocking = observation.facts.get("blocking") or []
    what = " and ".join(label.casefold() for label in blocking[:2]) or "what's standing on the floor"
    return FindingCopy(
        title="There's not enough room to turn around in the restroom",
        detail=f"The widest clear circle is {shown} across. Turning a wheelchair needs {needed}.",
        fix=f"Move the {what} so there's a {size(rule.threshold)} circle of clear floor inside the restroom.",
    )


def _turn_width(observation: Observation, rule: RuleSpec) -> FindingCopy:
    destination = stop_phrase(observation.facts.get("destination"))
    pivot = observation.facts.get("pivot")
    around = f"the {pivot.casefold()}" if pivot else "the corner"
    needed = inches(observation.required_inches or rule.threshold)
    shown = measured(_measured_inches(observation), observation.required_inches)
    if observation.satisfied:
        return FindingCopy(
            title=f"The turn around {around} is wide enough",
            detail=f"It's {shown} at the tightest part of the turn, and a wheelchair needs {needed}.",
        )
    return FindingCopy(
        title=f"The turn around {around} is too tight",
        detail=f"It's {shown} at the tightest part of the turn. A wheelchair needs {needed} to come back round on the way to {destination}.",
        fix=f"Move {around} back, or widen the gap beside it.",
    )


def _exit_path(observation: Observation, rule: RuleSpec) -> FindingCopy:
    way_out = stop_phrase(observation.facts.get("exit"))
    if observation.satisfied:
        return FindingCopy(
            title="The way out is clear",
            detail=(
                "There's a path from every seat to "
                f"{way_out}. California also requires that egress stay "
                "continuous to a public way with compliant accessible route components."
            ),
        )
    blockers = things(observation.facts.get("blockers", []))
    subject = f"{blockers} sit" if blockers else "Something sits"
    stranded = observation.facts.get("blocked_from") or []
    origin = stop_phrase(stranded[0]) if stranded else "the seats"
    return FindingCopy(
        title="The way out is blocked",
        detail=f"{subject} across the only path from {origin} to {way_out}.",
        fix="Move enough of it aside to leave a gap all the way through.",
    )


QUESTIONS = {
    "entrance_threshold": FindingCopy(
        title="Send a low photo of the front doorway",
        detail="Open the door, crouch just outside and hold your phone a few inches off the ground, pointed into the shop. If you have a tape measure, stand it next to the threshold so we can read its height against the half inch the standard allows.",
    ),
    "door_hardware": FindingCopy(
        title="Send a photo of the front door handle",
        detail="Stand in front of the handle and get close enough that its shape is clear. We'll check it opens with a closed fist and sits between 34 and 48 inches up.",
    ),
    "door_opening_force": FindingCopy(
        title="Check how hard the doors inside the shop are to push open",
        detail="Inside doors, like a restroom door, should open with no more than 5 pounds of push. A door pressure gauge from a hardware store measures it. The front door isn't part of this, because the federal rule sets no limit for outside doors.",
    ),
    "floor_surface": FindingCopy(
        title="Send a photo of the floor just inside the front door",
        detail="Include any mat. We'll check it lies flat, stays put, and that carpet is no thicker than half an inch.",
    ),
    "restroom_turning_space": FindingCopy(
        title="Send a photo of the customer restroom from the doorway",
        detail="Stand in the door and get the whole room in. We'll check there's a 60 inch circle to turn around in.",
    ),
    "reach_range": FindingCopy(
        title="Send a photo of anything a customer has to reach for",
        detail="The card reader, the light switch by the door, a bell pull. We'll check each one sits between 15 and 48 inches up.",
    ),
}

def _door_clearance(observation: Observation, rule: RuleSpec) -> FindingCopy:
    door = observation.facts.get("door", "door").casefold()
    pull = inches(observation.facts.get("pull_depth", rule.threshold))
    push = inches(observation.facts.get("push_depth", 48.0))
    latch = inches(observation.facts.get("latch_side", 18.0))
    if observation.reason == "complies_either_way":
        return FindingCopy(
            title=f"There's room to work the {door}",
            detail=f"The floor in front of it is clear for {pull}, which is "
            "enough to pull it open from a wheelchair.",
        )
    if observation.reason == "depends_on_the_swing":
        return FindingCopy(
            title=f"Tell us which way the {door} opens",
            detail=f"There's {push} of clear floor in front of it. That's "
            f"enough to push it open and short of the {pull} it takes to pull "
            "it open. Which way it swings decides this one.",
        )
    return FindingCopy(
        title=f"There's not enough room to open the {door}",
        detail=f"Opening it from a wheelchair needs {push} of clear floor in "
        f"front, and {latch} of that clear past the handle side.",
        fix=f"Keep the floor in front of the {door} clear for {pull}.",
    )


def _protrusion(observation: Observation, rule: RuleSpec) -> FindingCopy:
    thing = observation.facts.get("object", "object").casefold()
    edge = inches(observation.facts.get("leading_edge_inches", 40.0))
    out = measured(_measured_inches(observation), observation.required_inches)
    allowed = inches(observation.required_inches or rule.threshold)
    if observation.satisfied:
        return FindingCopy(
            title=f"The {thing} on the wall is out of the way",
            detail=f"It sticks out {out} at {edge} up, and {allowed} is the most "
            "that's allowed at that height.",
        )
    return FindingCopy(
        title=f"The {thing} sticks out where someone could walk into it",
        detail=f"It comes {out} off the wall at {edge} up. At that height "
        f"anything over {allowed} is in the way, because a cane sweeping the "
        "floor never finds it.",
        fix=f"Bring it back to {allowed} off the wall, or put something solid "
        "underneath it that a cane will find.",
    )


def _dining(observation: Observation, rule: RuleSpec) -> FindingCopy:
    total = int(observation.facts.get("surfaces", 0))
    complying = int(observation.facts.get("complying", 0))
    needed = int(observation.facts.get("needed", 1))
    low = inches(observation.facts.get("range_low", 28.0))
    high = inches(observation.facts.get("range_high", 34.0))
    tables = plural("table") if total != 1 else "table"
    if observation.satisfied:
        return FindingCopy(
            title="There's a table someone in a wheelchair can use",
            detail=f"{complying} of your {total} {tables} sit between {low} and "
            f"{high} high, which is the range that works from a wheelchair.",
        )
    return FindingCopy(
        title="None of the tables are a height that works from a wheelchair",
        detail=f"A table needs to sit between {low} and {high} high. "
        f"{_count_phrase(complying, total, tables)}",
        fix=f"Set {needed} of them between {low} and {high} high.",
    )


def _count_phrase(complying: int, total: int, tables: str) -> str:
    if complying == 0:
        return f"All {total} of yours are outside that."
    return f"{complying} of your {total} {tables} are."


def _reach(observation: Observation, rule: RuleSpec) -> FindingCopy:
    subject = str(observation.facts.get("subject", "control")).casefold()
    high = inches(observation.facts.get("high", rule.threshold))
    low = inches(observation.facts.get("low", 15.0))
    if observation.satisfied:
        return FindingCopy(
            title=f"The {subject} is within reach",
            detail=f"It sits between {low} and {high} up, where a seated person can reach it.",
        )
    if observation.reason == "too_low":
        return FindingCopy(
            title=f"The {subject} is too low to reach",
            detail=f"Its bottom is {inches(observation.measured_inches)} up. A seated person reaches down to {low}.",
            fix=f"Raise the {subject} so it sits between {low} and {high} up.",
        )
    return FindingCopy(
        title=f"The {subject} is too high to reach",
        detail=f"Its top is {inches(observation.measured_inches)} up. A seated person reaches up to {high}.",
        fix=f"Lower the {subject} so its top is no more than {high} up.",
    )


def _subject(observation: Observation, key: str, fallback: str) -> str:
    return str(observation.facts.get(key) or fallback).casefold()


def _standing_in(labels: list[str], verb: str) -> str:
    """`The chair sits`, `The two display cases sit`, at the start of a sentence."""
    named = things(labels)
    if not named:
        return f"Something {verb}s"
    return f"{named[:1].upper()}{named[1:]} {verb if len(labels) > 1 else verb + 's'}"


def _ramp_slope(observation: Observation, rule: RuleSpec) -> FindingCopy:
    ramp = _subject(observation, "ramp", "ramp")
    rise = inches(observation.facts.get("rise", 0.0))
    run = span(observation.facts.get("run", 0.0))
    needed = span(observation.required_inches or 0.0)
    detail = (f"It climbs {rise} over {run} of length. A ramp can climb at most 1 inch for every "
              f"{rule.threshold:g} inches of length, so this one needs {needed}.")
    if observation.satisfied:
        return FindingCopy(title=f"The {ramp} is gentle enough", detail=detail)
    return FindingCopy(
        title=f"The {ramp} is too steep",
        detail=detail,
        fix=f"Rebuild the {ramp} at least {needed} long, so it climbs no more than 1 inch in every {rule.threshold:g}.",
    )


def _ramp_rise(observation: Observation, rule: RuleSpec) -> FindingCopy:
    ramp = _subject(observation, "ramp", "ramp")
    limit = inches(rule.threshold)
    detail = f"It climbs {measured(_measured_inches(observation), rule.threshold)} in one go. One run of ramp can climb {limit} before it needs a level landing."
    if observation.satisfied:
        return FindingCopy(title=f"The {ramp}'s climb fits in one run", detail=detail)
    return FindingCopy(
        title=f"The {ramp} climbs too high in one run",
        detail=detail,
        fix=f"Split the {ramp} into runs that each climb {limit} or less, with a level landing between them.",
    )


def _ramp_width(observation: Observation, rule: RuleSpec) -> FindingCopy:
    ramp = _subject(observation, "ramp", "ramp")
    needed = inches(rule.threshold)
    detail = f"It's {measured(_measured_inches(observation), rule.threshold)} wide. A ramp needs {needed}."
    if observation.satisfied:
        return FindingCopy(title=f"The {ramp} is wide enough", detail=detail)
    return FindingCopy(
        title=f"The {ramp} is too narrow",
        detail=detail,
        fix=f"Widen the {ramp} to {needed} clear between its edges or its handrails.",
    )


def _ramp_landing(observation: Observation, rule: RuleSpec) -> FindingCopy:
    ramp = _subject(observation, "ramp", "ramp")
    needed = inches(rule.threshold)
    if observation.satisfied:
        return FindingCopy(
            title=f"There's room to stop at both ends of the {ramp}",
            detail=f"Each end has at least {needed} of clear floor past it, which is what someone coming on or off a ramp needs.",
        )
    if observation.reason == "too_short":
        return FindingCopy(
            title=f"The landing at one end of the {ramp} is too short",
            detail=f"It's {measured(_measured_inches(observation), rule.threshold)} long. A landing needs {needed}.",
            fix=f"Lengthen that landing to {needed}.",
        )
    movable = things(observation.facts.get("movable_blockers", []))
    subject = _standing_in(observation.facts.get("blockers", []), "sit")
    return FindingCopy(
        title=f"There's no clear landing at one end of the {ramp}",
        detail=f"{subject} in the {needed} of floor past the end of it. Someone coming off a ramp needs that much clear floor to stop.",
        fix=f"Move {movable} out of the {needed} past the end of the {ramp}." if movable
        else f"Keep {needed} of floor clear past each end of the {ramp}.",
    )


def _ramp_handrails(observation: Observation, rule: RuleSpec) -> FindingCopy:
    ramp = _subject(observation, "ramp", "ramp")
    rise = inches(observation.facts.get("rise", 0.0))
    limit = inches(rule.threshold)
    if observation.reason == "low_rise":
        return FindingCopy(
            title=f"The {ramp} is low enough to go without handrails",
            detail=f"It climbs {rise}. Handrails are needed once a ramp climbs more than {limit}.",
        )
    if observation.satisfied:
        return FindingCopy(
            title=f"The {ramp} has handrails on both sides",
            detail=f"It climbs {rise}, and a ramp that climbs more than {limit} needs a handrail along each side.",
        )
    return REQUESTS[rule.id]


def _handrail_height(observation: Observation, rule: RuleSpec) -> FindingCopy:
    ramp = _subject(observation, "ramp", "ramp")
    low = inches(observation.facts.get("low", 34.0))
    high = inches(observation.facts.get("high", rule.threshold))
    detail = (f"Its top sits {measured(_measured_inches(observation), observation.required_inches)} above the ramp. "
              f"Handrails belong between {low} and {high} up.")
    if observation.satisfied:
        return FindingCopy(title=f"The handrail on the {ramp} is a good height", detail=detail)
    direction = "Raise" if observation.reason == "too_low" else "Lower"
    return FindingCopy(
        title=f"The handrail on the {ramp} is too {'low' if observation.reason == 'too_low' else 'high'}",
        detail=detail,
        fix=f"{direction} the handrail so its top sits between {low} and {high} above the ramp.",
    )


def _reach_limits(observation: Observation, rule: RuleSpec) -> FindingCopy:
    subject = _subject(observation, "subject", "control")
    high = inches(observation.facts.get("high", rule.threshold))
    low = inches(observation.facts.get("low", 15.0))
    if observation.satisfied:
        return FindingCopy(
            title=f"The {subject} is within reach",
            detail=f"Its top sits {inches(observation.facts.get('top', 0.0))} up. A seated person can reach anything between {low} and {high}.",
        )
    if observation.reason == "too_low":
        return FindingCopy(
            title=f"The {subject} is too low to reach",
            detail=f"Its top is {inches(_measured_inches(observation))} up. A seated person reaches down to {low}.",
            fix=f"Raise the {subject} so what a customer uses sits between {low} and {high} up.",
        )
    if observation.reason == "controls_unplaced":
        return REQUESTS[rule.id]
    return FindingCopy(
        title=f"The {subject} is too high to reach",
        detail=f"Its lowest edge is {inches(_measured_inches(observation))} up. A seated person reaches up to {high}.",
        fix=f"Lower the {subject} so what a customer uses sits no higher than {high}.",
    )


def _kiosk_floor(observation: Observation, rule: RuleSpec) -> FindingCopy:
    kiosk = _subject(observation, "kiosk", "kiosk")
    needed = by(observation.facts.get("required_wide", rule.threshold), observation.facts.get("required_deep", 48.0))
    patch = by_size(observation.facts.get("required_wide", rule.threshold), observation.facts.get("required_deep", 48.0))
    if observation.satisfied:
        return FindingCopy(
            title=f"There's room to pull up to the {kiosk}",
            detail=f"The floor beside it has a clear {patch} patch, which is what using it from a wheelchair needs.",
        )
    movable = things(observation.facts.get("movable_blockers", []))
    subject = _standing_in(observation.facts.get("blockers", []), "stand")
    return FindingCopy(
        title=f"There's not enough room to pull up to the {kiosk}",
        detail=f"{subject} in the {needed} of floor a wheelchair needs in front of it.",
        fix=f"Move {movable} so there's a clear {patch} patch in front of the {kiosk}." if movable
        else f"Clear a {patch} patch of floor in front of the {kiosk}.",
    )


WRITERS: dict[str, Callable[[Observation, RuleSpec], FindingCopy]] = {
    "reach_range": _reach,
    "door_maneuvering_clearance": _door_clearance,
    "protruding_objects": _protrusion,
    "dining_surface_height": _dining,
    "route_clear_width": _route_width,
    "door_clear_width": _door_width,
    "service_counter_height": _counter_height,
    "point_of_sale_height": _point_of_sale,
    "service_counter_approach": _counter_approach,
    "passing_space": _passing_space,
    "turning_space": _turning_space,
    "restroom_turning_space": _restroom_turning,
    "turn_clear_width": _turn_width,
    "exit_path": _exit_path,
    "ramp_running_slope": _ramp_slope,
    "ramp_rise": _ramp_rise,
    "ramp_clear_width": _ramp_width,
    "ramp_landing_length": _ramp_landing,
    "ramp_handrails": _ramp_handrails,
    "handrail_height": _handrail_height,
    "kiosk_reach": _reach_limits,
    "kiosk_clear_floor": _kiosk_floor,
    "self_service_reach": _reach_limits,
}


REQUESTS = {
    "door_maneuvering_clearance": FindingCopy(
        title="Tell us which way your front door opens",
        detail="Pushed outward or pulled inward, from where a customer stands. Pulling one open takes more room in front of it, so it decides whether this passes.",
    ),
    "ramp_handrails": FindingCopy(
        title="Send a photo of the ramp's handrails",
        detail="Stand at the bottom of the ramp and get both sides in. It climbs more than 6 inches, so it needs a handrail along each side.",
    ),
    "ramp_landing_length": FindingCopy(
        title="Send a photo of the floor at each end of the ramp",
        detail="Step back far enough to get 5 feet of floor past the top and past the bottom. Each end needs that much clear, level floor to stop on.",
    ),
    "kiosk_reach": FindingCopy(
        title="Measure how high the kiosk's highest control is",
        detail="Measure from the floor to the top of the touch screen, or to the highest button or card slot a customer uses. It needs to be 48 inches or lower.",
    ),
    "self_service_reach": FindingCopy(
        title="Measure how high the self-serve spouts and stacks are",
        detail="Measure from the floor to the highest lever, spout, lid stack or napkin a customer reaches for. It needs to be 48 inches or lower.",
    ),
    "door_clear_width": FindingCopy(
        title="Measure the front doorway and send us the number",
        detail="Open the door all the way and measure from the face of the door across to the frame. That's the width a wheelchair actually gets, and it needs 32 inches.",
    ),
}

GENERIC_REQUEST = FindingCopy(
    title="Send us one measurement and we'll finish this check",
    detail="A tape measure across the narrowest part is all it takes.",
)


def request(rule: RuleSpec) -> FindingCopy:
    """What to ask for when geometry cannot settle a rule on its own."""
    return REQUESTS.get(rule.id, GENERIC_REQUEST)


def another_look(labels: list[str]) -> FindingCopy:
    """Thin coverage becomes a request, never a red finding."""
    subject = things(labels) or "that corner"
    return FindingCopy(
        title=f"Point the phone at {subject} again",
        detail="A few seconds from a second angle is enough, then we'll measure it.",
    )


NO_ARRANGEMENT = "We couldn't find an arrangement that works."
"""The sentence the plan prescribes for an exhausted search, section 2.

It is always followed by one specific thing the owner could allow, because a
dead end with no next move is not an answer.
"""

RATIONALES = {
    "split_the_gap": "Move {what} {distance} apart.",
    "move_one_aside": "Move {what} {distance} over.",
    "stagger": "Step {what} {distance} apart along the aisle, so they stop lining up.",
    "turn_one": "Turn {what} a quarter turn.",
}

RELAXATIONS = {
    "unlock": "Can {what} be moved? Unlock it and we'll try again.",
    "set_aside": "Try it without {what}?",
}


def proposal_rationale(strategy: str, labels: list[str], inches_moved: float) -> str:
    """What the owner sees on the before and after, in one sentence."""
    template = RATIONALES.get(strategy, "Move {what} {distance}.")
    return template.format(
        what=things(labels) or "it", distance=inches(inches_moved)
    )


REQUEST_SENTENCES = {
    "back": "Move {what} {distance} back.",
    "front": "Move {what} {distance} toward the door.",
    "left": "Move {what} {distance} to the left.",
    "right": "Move {what} {distance} to the right.",
    "apart": "Move {what} {distance} apart.",
    "together": "Move {what} {distance} closer together.",
}

ASK_REPLIES = {
    "asked_to_move_something_fixed": (
        "That one is built in. Tell us which of the loose pieces to move."
    ),
    "asked_about_something_that_is_not_here": (
        "Point at the piece you mean in the plan and we will look at it."
    ),
    "asked_to_move_something_it_also_locked": (
        "That piece is on both lists. Say whether it moves or stays."
    ),
    "question_names_nothing": (
        "Name a piece of furniture and ask again. The counter, the tables, "
        "the chairs."
    ),
    "question_does_not_say_which_dimension": (
        "Say whether you mean how tall, how wide or how deep."
    ),
    "question_names_only_one_end": (
        "Name both ends and we will measure between them."
    ),
    "question_does_not_say_how_big": (
        "Tell us how long it is in inches and we will see where it goes."
    ),
    "question_does_not_say_which_way": (
        "Say which way: back, front, left, right, apart or together."
    ),
    "measurement_out_of_range": "Give us the size in inches and we will try it.",
    "restatement_too_long": "Ask it in one sentence and we will have a go.",
}

ASK_DEFAULT_REPLY = (
    "We did not follow that one. You can ask how many of something you have, "
    "how big it is, where it is, what shape it sits in, or whether something "
    "you are thinking of buying would fit."
)


def request_rationale(direction: str, labels: list[str], inches_moved: float) -> str:
    """What will happen, with the distance, for the owner to approve."""
    template = REQUEST_SENTENCES.get(direction, "Move {what} {distance}.")
    return template.format(
        what=things(labels) or "it", distance=inches(inches_moved)
    )


def no_room_for_request(labels: list[str], blocker: str | None) -> str:
    """Why the pieces stopped where they did, and what to try instead."""
    what = things(labels) or "they"
    subject = what[:1].upper() + what[1:]
    one = len(labels) == 1
    if blocker:
        runs = "runs" if one else "run"
        return (
            f"{subject} {runs} into the {blocker.casefold()} first. "
            "Try a shorter move, or fewer pieces."
        )
    is_are = "is" if one else "are"
    return (
        f"{subject} {is_are} blocked on every side. "
        "Try a shorter move, or fewer pieces."
    )


def ask_reply(reason: str) -> str:
    """What to say when a request could not be read, as something to do."""
    return ASK_REPLIES.get(reason, ASK_DEFAULT_REPLY)


def relaxation_question(kind: str, labels: list[str]) -> str:
    """One specific thing to allow, phrased as a choice the owner makes."""
    return RELAXATIONS[kind].format(what=things(labels) or "one piece")


def no_arrangement(question: str | None) -> str:
    return f"{NO_ARRANGEMENT} {question}" if question else NO_ARRANGEMENT


REPORT_READY = "Your report is ready."


def escalation_note(count: int) -> str:
    """What happens next to something furniture cannot fix."""
    if count == 1:
        return "An accessibility professional will look at this one."
    return f"An accessibility professional will look at these {count}."


def describe(observation: Observation, rule: RuleSpec) -> FindingCopy:
    if rule.id in QUESTIONS and not observation.seen_directly and not (rule.measurable and rule.id in WRITERS):
        return QUESTIONS[rule.id]
    writer = WRITERS.get(rule.id)
    if writer is None:
        raise KeyError(f"no copy written for {rule.id}")
    return writer(observation, rule)
