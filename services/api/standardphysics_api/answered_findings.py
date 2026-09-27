"""The findings as the owner's answers leave them.

A question finding stays a question in the stored assessment. When it's read,
an answer turns it into a pass or a problem: a number is checked against the
rule straight away, and a photo becomes a result once a person on the team has
looked at it. A "no" to the restroom or inside-door question removes the
findings that only mattered if the answer was yes.
"""

from __future__ import annotations

from collections.abc import Callable

from standardphysics_agents import FindingCopy, Observation, describe, load_pack
from standardphysics_contracts import Assessment, Finding, OwnerRequest

PHOTO_RESULTS: dict[str, dict[str, FindingCopy]] = {
    "entrance_threshold": {
        "passes": FindingCopy(
            title="The front doorway has no step to trip on",
            detail="We checked your photo. Any step there is half an inch or less.",
        ),
        "problem": FindingCopy(
            title="The front doorway has a step",
            detail="We checked your photo. The step is taller than the half inch a wheelchair can roll over.",
            fix="Add a small ramp or a beveled threshold so the step is half an inch or less.",
        ),
    },
    "door_hardware": {
        "passes": FindingCopy(
            title="The front door handle works with a closed fist",
            detail="We checked your photo. It opens without gripping or twisting.",
        ),
        "problem": FindingCopy(
            title="The front door handle needs a tight grip",
            detail="We checked your photo. It has to be gripped and twisted to open.",
            fix="Swap it for a lever or a push bar, mounted between 34 and 48 inches up.",
        ),
    },
    "floor_surface": {
        "passes": FindingCopy(
            title="The floor inside the front door is firm and flat",
            detail="We checked your photo. Nothing there can catch a wheel.",
        ),
        "problem": FindingCopy(
            title="The floor inside the front door can catch a wheel",
            detail="We checked your photo. A loose mat or thick carpet there can stop a wheelchair.",
            fix="Tape the mat down all the way around, or swap it for a thin one that stays put.",
        ),
    },
    "restroom_turning_space": {
        "passes": FindingCopy(
            title="The restroom has room to turn around",
            detail="We checked your photo. There's a 60 inch circle of clear floor.",
        ),
        "problem": FindingCopy(
            title="The restroom is too tight to turn around in",
            detail="We checked your photo. There isn't a 60 inch circle of clear floor.",
            fix="Move whatever stands on the floor so a 60 inch circle is clear.",
        ),
    },
}

WAITING_FOR_REVIEW = FindingCopy(
    title="We have your photo",
    detail="A person on our team is checking it. We'll let you know what we find.",
)


def _door_force(finding: Finding, pounds: float, passes: bool) -> FindingCopy:
    most = f"{load_pack().by_id(finding.check_id).threshold:g}"
    measured = f"They take {pounds:g} pounds to push open. Inside doors can take up to {most} pounds."
    if passes:
        return FindingCopy(title="The inside doors open easily", detail=measured)
    return FindingCopy(
        title="The inside doors are too hard to push open",
        detail=measured,
        fix=f"Loosen the door closers until each door opens with {most} pounds of push or less.",
    )


def _doorway(finding: Finding, width: float, passes: bool) -> FindingCopy:
    rule = load_pack().by_id(finding.check_id)
    observation = Observation(
        rule_id=rule.id, satisfied=passes, measured_inches=width, required_inches=rule.threshold,
        facts={"door": "front doorway"},
    )
    return describe(observation, rule)


NUMBER_COPY: dict[str, Callable[[Finding, float, bool], FindingCopy]] = {
    "door_opening_force": _door_force,
    "door_clear_width": _doorway,
}


def _with(finding: Finding, outcome: str, copy: FindingCopy, measured: float | None = None) -> Finding:
    return finding.model_copy(update={
        "outcome": outcome, "title": copy.title, "detail": copy.detail,
        "fix": copy.fix if outcome == "problem" else None, "asks": None,
        "measured_inches": measured if measured is not None else finding.measured_inches,
    })


def _numbered(finding: Finding, request: OwnerRequest) -> Finding:
    if request.answer is None or request.answer.number is None or finding.check_id not in NUMBER_COPY:
        return finding
    number = request.answer.number
    rule = load_pack().by_id(finding.check_id)
    passes = rule.satisfied_by(number)
    copy = NUMBER_COPY[finding.check_id](finding, number, passes)
    return _with(finding, "passes" if passes else "problem", copy, number if rule.unit == "in" else None)


def _photographed(finding: Finding, request: OwnerRequest) -> Finding:
    if request.status == "answered":
        return finding.model_copy(update={
            "title": WAITING_FOR_REVIEW.title, "detail": WAITING_FOR_REVIEW.detail, "asks": "review",
        })
    results = PHOTO_RESULTS.get(finding.check_id)
    if request.review is None or results is None:
        return finding
    return _with(finding, request.review, results[request.review])


SETTLERS: dict[str, Callable[[Finding, OwnerRequest], Finding]] = {
    "number": _numbered,
    "photo": _photographed,
}


def _settled(finding: Finding, request: OwnerRequest | None) -> Finding | None:
    if request is None:
        return finding
    if request.status == "not_applicable":
        return None
    settle = SETTLERS.get(request.kind)
    return settle(finding, request) if settle else finding


def apply_answers(assessment: Assessment, requests: list[OwnerRequest]) -> Assessment:
    by_finding = {request.finding_id: request for request in requests if request.finding_id is not None}
    settled = (_settled(finding, by_finding.get(finding.id)) for finding in assessment.findings)
    findings = [finding for finding in settled if finding is not None]
    return assessment.model_copy(update={"findings": findings})

