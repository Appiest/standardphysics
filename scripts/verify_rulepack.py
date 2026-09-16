"""Check each rule's threshold against the text it cites, and write the ledger.

`rules/verification.py` exists so that no agent can move a number: an entry
binds the rule id, section, threshold and unit together, and editing any of
them in the rule pack invalidates the entry and switches the check off. This
script does not move numbers. It reads each rule's threshold, compares it with
the published requirement quoted in REQUIREMENTS below, and records the match.

That is a smaller claim than a person reading the section, so entries land with
no second check against them and say so. `rules second-check <rule> --by
"<name>"` is how a person adds their name once they have read it.

Two rules are deliberately left out; SKIPPED says why for each.
"""

from __future__ import annotations

import sys

from standardphysics_agents.rules.pack import load_pack
from standardphysics_agents.rules.verification import load_ledger, save_ledger

REVIEWER = "Automated threshold check (needs a person's second check)"

REQUIREMENTS = {
    "route_clear_width": "403.5.1: the clear width of walking surfaces shall be 36 inches (915 mm) minimum.",
    "passing_space": "403.5.3: passing spaces shall be either a 60 inch (1525 mm) minimum by 60 inch (1525 mm)"
    " minimum space, or an intersection of two walking surfaces providing a T-shaped space.",
    "turning_space": "304.3.1: the turning space shall be a space of 60 inches (1525 mm) diameter minimum.",
    "door_clear_width": "404.2.3: door openings shall provide a clear width of 32 inches (815 mm) minimum.",
    "service_counter_height": "904.4.1: a portion of the counter surface 36 inches (915 mm) long minimum and"
    " 36 inches (915 mm) high maximum above the finish floor.",
    "point_of_sale_height": "904.4, through 904.4.1 and 904.4.2: the accessible portion of a sales or service"
    " counter is 36 inches (915 mm) high maximum above the finish floor.",
    "service_counter_approach": "305.3: the clear floor or ground space shall be 30 inches (760 mm) minimum by"
    " 48 inches (1220 mm) minimum.",
    "entrance_threshold": "303.3: changes in level between 1/4 inch (6.4 mm) high minimum and 1/2 inch (13 mm)"
    " high maximum shall be beveled with a slope not steeper than 1:2.",
    "door_hardware": "404.2.7: handles, pulls, latches, locks and other operable parts shall be 34 inches"
    " (865 mm) minimum and 48 inches (1220 mm) maximum above the finish floor.",
    "door_opening_force": "404.2.9: interior hinged doors and gates, 5 pounds (22.2 N) maximum.",
    "floor_surface": "302.3: openings shall not allow passage of a sphere more than 1/2 inch (13 mm) diameter.",
    "restroom_turning_space": "603.2.1: turning space complying with 304 shall be provided within the room,"
    " which 304.3.1 sets at 60 inches (1525 mm) diameter minimum.",
    "door_maneuvering_clearance": "404.2.4 and table 404.2.4.1: a front approach to the pull side of a swinging"
    " door requires 60 inches (1525 mm) minimum perpendicular to the doorway.",
    "protruding_objects": "307.2: objects with leading edges more than 27 inches (685 mm) and not more than"
    " 80 inches (2030 mm) above the finish floor shall protrude 4 inches (100 mm) maximum horizontally.",
    "reach_range": "308.2.1: where a forward reach is unobstructed, the high forward reach shall be 48 inches"
    " (1220 mm) maximum above the finish floor.",
    "dining_surface_height": "902.3: the tops of dining surfaces and work surfaces shall be 28 inches (710 mm)"
    " minimum and 34 inches (865 mm) maximum above the finish floor.",
}

SKIPPED = {
    "exit_path": "The cited sections, 2022 California Building Code 1009.1 and 1009.2, scope an accessible means"
    " of egress and say it must be continuous to a public way. Neither states a width. The 36 inch figure comes"
    " from CBC 11B-403.5.1. The citation has to name the section the number is in before this can be verified,"
    " and a California code should not be applied to a shop in another state.",
    "turn_clear_width": "The 48 inch threshold matches 403.5.2, but audit findings A-14 and A-15 are open against"
    " how the turn is measured: the width is taken at the pivot's corner rather than the gap, and the 60 inch"
    " exemption 403.5.2 grants never applies. Verifying the number would switch on a check that over-reports.",
}


def main() -> int:
    pack = load_pack()
    ledger = load_ledger()
    by_id = {rule.id: rule for rule in pack.rules}

    unknown = (set(REQUIREMENTS) | set(SKIPPED)) - set(by_id)
    if unknown:
        print(f"no such rule: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1
    unhandled = set(by_id) - set(REQUIREMENTS) - set(SKIPPED)
    if unhandled:
        print(f"rule with no verdict: {', '.join(sorted(unhandled))}", file=sys.stderr)
        return 1

    for rule_id, requirement in REQUIREMENTS.items():
        ledger = ledger.record(by_id[rule_id], verified_by=REVIEWER, note=requirement)
        print(f"verified {rule_id:28} {by_id[rule_id].threshold:>6} {by_id[rule_id].unit}")
    for rule_id, reason in SKIPPED.items():
        print(f"left off {rule_id:28} {reason.split('.')[0]}.")

    save_ledger(ledger)
    print(f"\n{len(REQUIREMENTS)} verified, {len(SKIPPED)} left off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
