"""One step of a room rebuild judged against the compliance ledger, and where a finished rebuild ended up.

A failure is one measured spot the ledger fails: a rule at one route leg, stop or group of pieces, or one
directive constraint. Every measured failure counts, including failures already present in the owner's layout.
Unknown measurements and unsigned requirements are reported separately; clearing measured failures is not
legal ADA sign-off.

Where a rebuild ended up is measured against the owner's layout:

    usefulness   `usefulness.Usefulness` (seats facing what they serve, clear fronts on wall pieces, the
                 accessible dining share) and which of those shares the rebuild lost
    look         `quality.layout_quality` from the owner's layout: wall relations and seat and table pairs
                 of every piece that is not where the owner had it, and sightlines. Unvalidated: it agreed
                 with a person in 12 of 24 rated pairs
    distance     how far the movable floor pieces are from where the owner had them, and the share back
                 within `HOME_METERS` and `HOME_DEGREES`
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from multiroom_data import checker_for
from standardphysics_agents.compliance import (
    ComplianceResult,
    RequirementEntry,
    RequirementEvidence,
    evaluate_candidate_room,
)
from standardphysics_agents.training import TrainingChecker, trusted_geometry
from standardphysics_agents.training.edits import yaw_degrees
from standardphysics_agents.training.quality import layout_quality
from standardphysics_agents.training.scramble import floor_furniture
from standardphysics_agents.training.snapped_prompt import prompt_messages
from standardphysics_agents.training.snapped_reward import judge
from standardphysics_agents.training.usability import usability
from standardphysics_agents.training.usefulness import usefulness
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph

HOME_METERS = 0.25
HOME_DEGREES = 15.0
MAX_LISTED = 6
"""Failures spelled out in one feedback message; the rest are counted."""
COMPARISONS = {"at_least": "at least", "at_most": "at most"}


@dataclass(frozen=True)
class Failure:
    key: str
    entry: RequirementEntry
    evidence: RequirementEvidence | None


def ledger(graph: SceneGraph, window: Window, checker: TrainingChecker) -> ComplianceResult:
    return evaluate_candidate_room(trusted_geometry(graph), window.scenario, checker.measure, checker.space_typology,
                                   base=window.graph, directives=checker.directives)


def failures(result: ComplianceResult) -> dict[str, Failure]:
    found = {}
    for entry in result.entries:
        if not entry.applicable or entry.measured != "fail":
            continue
        spots = [item for item in entry.evidence if item.measured == "fail"]
        evidence: list[RequirementEvidence | None] = [*spots]
        if not evidence:
            evidence.append(None)
        for item in evidence:
            key = f"{entry.id} @ {item.target if item else entry.target}"
            found[key] = Failure(key, entry, item)
    return found


def describe(failure: Failure) -> str:
    entry, item = failure.entry, failure.evidence
    name = entry.id.split(":")[-1].replace("_", " ")
    text = f"{entry.citation} {name} at {item.target if item else entry.target}"
    if item and item.measured_value is not None and entry.threshold is not None:
        needs = COMPARISONS.get(entry.comparison or "", entry.comparison or "")
        text += f" measures {item.measured_value:.0f} {entry.unit or ''}, needs {needs} {entry.threshold:.0f}"
    return text


def feedback(failing: list[Failure]) -> str:
    if not failing:
        return ""
    listed = "; ".join(describe(failure) for failure in failing[:MAX_LISTED])
    more = f" and {len(failing) - MAX_LISTED} more" if len(failing) > MAX_LISTED else ""
    return (f"The compliance ledger still fails {len(failing)} measured check(s): "
            f"{listed}{more}. Move furniture to clear them without breaking anything that passes.")


def refusal_message(verdict, directives) -> str:
    lines = [f"That layout was refused: {verdict.reason}."]
    if verdict.reason.startswith("precedent_violation"):
        lines += [f"ADA requirement ({directive.title}): {directive.plain_english_warning}" for directive in directives]
    lines.append("Propose a different layout that clears the problems without that. Answer with JSON only.")
    return " ".join(lines)


def with_note(messages: list[dict], note: str) -> list[dict]:
    if not note:
        return messages
    *head, last = messages
    return [*head, {**last, "content": f"{last['content']}\n\n{note}"}]


_ROOMS: dict[str, tuple[Window, TrainingChecker]] = {}


def room(window_row: dict) -> tuple[Window, TrainingChecker]:
    """The window and its checker, built once per process."""
    if window_row["window_id"] not in _ROOMS:
        window = Window.from_dict(window_row)
        _ROOMS[window.window_id] = (window, checker_for(window))
    return _ROOMS[window_row["window_id"]]


def measured_failures(graph: SceneGraph, window: Window, checker: TrainingChecker) -> list[Failure]:
    return [failure for _, failure in sorted(failures(ledger(graph, window, checker)).items())]


def step(window_row: dict, current: dict, completion: str) -> dict:
    """Judge one answer; say what the next turn is: a fresh prompt for a moved-on room, or a reply to a refusal."""
    window, checker = room(window_row)
    before = SceneGraph.model_validate(current)
    judged = judge(completion, before, checker)
    layout = judged.layout or before
    failing = measured_failures(layout, window, checker)
    note = feedback(failing)
    out = {"verdict": judged.verdict.as_dict(), "failing": [failure.key for failure in failing]}
    if judged.layout is not None:
        out["layout"] = layout.model_dump(mode="json")
        out["next_messages"] = with_note(prompt_messages(layout, checker), note)
    else:
        out["reply"] = " ".join(filter(None, (refusal_message(judged.verdict, checker.directives_for(before)), note)))
    return out


def _turned(before: float, after: float) -> float:
    return abs((after - before + 180.0) % 360.0 - 180.0)


def distance(owner: SceneGraph, layout: SceneGraph) -> dict:
    now = {node.id: node for node in layout.nodes}
    pairs = [(node, now[node.id]) for node in floor_furniture(owner) if node.id in now]
    if not pairs:
        return {"pieces": 0, "mean_meters": None, "mean_degrees": None, "at_home": None}
    slid = [math.hypot(after.transform.position.x - before.transform.position.x,
                       after.transform.position.y - before.transform.position.y) for before, after in pairs]
    turned = [_turned(yaw_degrees(before), yaw_degrees(after)) for before, after in pairs]
    home = [meters <= HOME_METERS and degrees <= HOME_DEGREES for meters, degrees in zip(slid, turned, strict=True)]
    return {"pieces": len(pairs), "mean_meters": round(sum(slid) / len(slid), 4),
            "mean_degrees": round(sum(turned) / len(turned), 2), "at_home": round(sum(home) / len(home), 4)}


def measures(owner: SceneGraph, layout: SceneGraph, checker: TrainingChecker) -> dict:
    useful_owner, useful = usefulness(owner), usefulness(layout)
    return {"usefulness": useful.as_dict(), "usefulness_owner": useful_owner.as_dict(),
            "usefulness_lost": useful.worse_than(useful_owner),
            "amenity_usability": usability(owner, layout, owner, checker.scenario),
            "look": layout_quality(owner, layout, owner, checker.measure).as_dict(),
            "distance": distance(owner, layout)}


def final(window_row: dict, layout: dict, owner_failures: list[str]) -> dict:
    window, checker = room(window_row)
    graph = SceneGraph.model_validate(layout)
    result = ledger(graph, window, checker)
    every = failures(result)
    return {"failing_total": len(every), "measured_clear": not every,
            "measured_unknown": list(result.measured_unknown),
            "verified_for_final_layout": result.accept_for_final_layout,
            "failing_new": sorted(key for key in every if key not in set(owner_failures)),
            **measures(window.graph, graph, checker)}
