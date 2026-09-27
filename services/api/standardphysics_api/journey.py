"""Where a shop is in the owner's journey, and the one thing to do next.

docs/UX.md names six stages. Set up happens on the phone before the server
knows the shop, so the server derives the other five from what's done: the
upload, the in-shop answers and photos, the measuring, the counter and the
customer path, the follow-ups and the checklist. Nothing about the stage is
stored, so no screen can disagree with another about it.

The next step's title is written here once, for the app's home card and the
web alike.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from standardphysics_contracts import Assessment, Checklist, Journey, NextStep, OwnerRequest, Scan
from standardphysics_contracts.owner import JourneyStage

from .checklist import problems_of

ANSWERABLE = {"yes_no", "number", "photo"}


@dataclass(frozen=True)
class ShopState:
    scan: Scan
    shop_name: str
    requests: list[OwnerRequest]
    assessment: Assessment | None
    """With the owner's answers laid over it."""
    measured: bool
    """Whether the shop's model exists yet. The counter and the path need the model, not the results."""
    counter_marked: bool
    path_confirmed: bool
    checklist: Checklist


Step = tuple[JourneyStage, NextStep]


def _count(count: int, one: str, many: str) -> str:
    return one if count == 1 else many.format(count=count)


def _open(state: ShopState, timing: str, kinds: set[str]) -> list[OwnerRequest]:
    return [
        request for request in state.requests
        if request.timing == timing and request.status == "open" and request.kind in kinds
    ]


def _walk(state: ShopState) -> Step | None:
    if state.scan.state == "failed":
        return "walk", NextStep(kind="failed", title="Walk your shop again")
    if state.scan.state == "uploading":
        return "walk", NextStep(kind="upload", title="Finish uploading your walk")
    return None


def _in_shop(state: ShopState) -> Step | None:
    questions = _open(state, "in_shop", {"yes_no"})
    if questions:
        title = _count(len(questions), "Answer 1 quick question", "Answer {count} quick questions")
        return "fill_in_the_gaps", NextStep(kind="answers", title=title, count=len(questions))
    photos = _open(state, "in_shop", {"photo"})
    if photos:
        title = _count(len(photos), "Take 1 more photo", "Take {count} more photos")
        return "fill_in_the_gaps", NextStep(kind="photos", title=title, count=len(photos))
    numbers = _open(state, "in_shop", {"number"})
    if numbers:
        return "fill_in_the_gaps", NextStep(kind="answers", title="Answer 1 quick question", count=len(numbers))
    return None


def _measuring(state: ShopState) -> Step | None:
    if not state.measured:
        return "fill_in_the_gaps", NextStep(kind="measuring", title="We're measuring your shop")
    return None


def _checking(state: ShopState) -> Step | None:
    if state.assessment is None:
        return "fill_in_the_gaps", NextStep(kind="measuring", title="We're checking your shop")
    return None


def _two_checks(state: ShopState) -> Step | None:
    if state.path_confirmed:
        return None
    if not state.counter_marked:
        return "fill_in_the_gaps", NextStep(kind="counter", title="Show us where customers pay")
    return "fill_in_the_gaps", NextStep(kind="path", title="Check the path customers take")


def _follow_ups(state: ShopState) -> Step | None:
    waiting = _open(state, "follow_up", ANSWERABLE)
    if not waiting:
        return None
    title = _count(len(waiting), "Send us 1 more measurement", "Send us {count} more measurements")
    return "fill_in_the_gaps", NextStep(kind="follow_ups", title=title, count=len(waiting))


def _next_fix(state: ShopState) -> str:
    to_do = {item.finding_id for item in state.checklist.items if item.status == "to_do"}
    finding = next(finding for finding in problems_of(state.assessment) if finding.id in to_do)
    words = finding.fix or finding.title
    return words[:1].lower() + words[1:]


def _still_to_check(state: ShopState) -> int:
    findings = state.assessment.findings if state.assessment else []
    return sum(1 for finding in findings if finding.outcome == "question")


def _nothing_to_fix(state: ShopState) -> Step:
    """No problems is only an all-clear once nothing is still waiting to be checked."""
    waiting = _still_to_check(state)
    if waiting == 0:
        return "tools", NextStep(kind="done", title="There's nothing on your list to fix")
    title = _count(waiting, "Nothing to fix so far. 1 spot still needs checking",
                   "Nothing to fix so far. {count} spots still need checking")
    return "tools", NextStep(kind="done", title=title, count=waiting)


def _fixing(state: ShopState) -> Step:
    done, total = state.checklist.done, state.checklist.total
    if total == 0:
        return _nothing_to_fix(state)
    if done == 0:
        title = _count(total, "Your results are ready. 1 thing to fix", "Your results are ready. {count} things to fix")
        return "results", NextStep(kind="results", title=title, count=total)
    if done < total:
        title = f"{done} of {total} done. Next: {_next_fix(state)}"
        return "fix", NextStep(kind="checklist", title=title, count=total - done)
    return "tools", NextStep(kind="done", title="Everything on your list is done")


STEPS: tuple[Callable[[ShopState], Step | None], ...] = (
    _walk, _in_shop, _measuring, _two_checks, _checking, _follow_ups,
)


def journey(state: ShopState) -> Journey:
    stage, next_step = next((step for check in STEPS if (step := check(state))), None) or _fixing(state)
    return Journey(
        scan_id=state.scan.id,
        shop_name=state.shop_name,
        stage=stage,
        next_step=next_step,
        tools_unlocked=stage in {"results", "fix", "tools"},
    )
