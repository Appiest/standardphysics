"""Suggest a rearrangement: a model proposes, the measured checker decides.

A suggestion is a background job on its own worker lane, because a Fireworks
deployment at zero replicas takes minutes to start and the page should poll
rather than hold a request open that long:

    POST /api/scans/{id}/rearrangement-suggestion   queue a job for the latest revision
    GET  /api/scans/{id}/rearrangement-suggestion?revision=N   poll it, and learn if the feature is on

The job builds prompts for the whole scan or problem windows, then asks up to
four times with checker feedback after a rejected answer. It scores each with
`training.reward.score_completion`: the edits parser, `apply_moves`,
`violations` (fixture, keep-clear, travel and room-to-use rules included) and
the gate with improvement required, as the fix agent uses it. That training
checker only chooses. What the owner reads, the findings before and after and
the sentence about them, comes from the production assessment the arrange
panel runs, so the two always tell the same story. Nothing is saved.

The optional Fireworks path still manages its deployment lease. OpenRouter is
the default and needs no GPU deployment.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from standardphysics_agents import load_pack
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training.reward import MOVED_PINNED, Verdict
from standardphysics_contracts import (
    Finding,
    RearrangementAttempt,
    RearrangementRequest,
    RearrangementStatus,
    RearrangementSuggestion,
    RewardParts,
    Scenario,
    SceneGraph,
    graph_hash,
)

from . import repository as repo
from .dev_model import nudges_from_prompt
from .errors import ApiProblem
from .fireworks import FakeFireworks, FireworksModel, ModelFailed, ModelWarming, RearrangeModel, Sampling
from .openrouter_rearrange import FakeOpenRouter, OpenRouterRearrange
from .rearrangement_data import record_accepted
from .rearrangement_search import Search, scan_plan, search, whole_checker
from .scenario import suggest_scenario
from .settings import Settings

log = logging.getLogger(__name__)

REARRANGE = "rearrange"
LEASE = "rearrange"
"""The one row in `rearrange_deployments`: this server asks one model."""

WARMUP_LIMIT_SECONDS = 600.0
FIRST_RETRY_SECONDS = 5.0
RETRY_GROWTH = 1.5
LONGEST_RETRY_SECONDS = 60.0

UNAVAILABLE = "Suggestions need the rearrangement model, and this server doesn't have one set up yet."
STALE = "a newer layout was saved since this one started"
STILL_STARTING = "The model is still starting after 10 minutes. Try again in a few minutes."
NO_ANSWER = "The model didn't send back any layouts. Try again."
INTERRUPTED = "The server restarted while it was suggesting a layout. Ask again."
BROKE = "Something went wrong while we checked the model's layouts. Try again."
NOTHING_TO_FIX = "Nothing here is something moving furniture can fix, so we didn't ask the model."
NO_WINDOWS = "We couldn't cut out a part of this floor around its problems for the model to work on."

REASON_CLAUSES = (
    ("unparseable", "its answer wasn't a list of moves we could read"),
    (MOVED_PINNED, "it moved a piece we aren't sure is really there"),
    ("no_supported_furniture_move", "it didn't move anything"),
    ("no_op_moves", "it didn't move anything"),
    ("duplicate_objects", "it moved the same piece twice"),
    ("unknown_objects", "it named a piece that isn't in this room"),
    ("collided", "it pushed a piece into something else"),
    ("blocked_a_door", "it put a piece in the way of a door"),
    ("blocked_keep_clear", "it put a piece in a space that has to stay clear"),
    ("moved_something_fixed", "it moved something that's fixed in place"),
    ("left_the_floor", "it pushed a piece past the edge of the room"),
    ("moved_too_far", "it carried a piece more than 5 feet"),
    ("no_room_to_use", "it left a table or counter with no room to pull up to it"),
    ("resized", "it changed the size of a piece"),
    ("inventory_changed", "it added or took away a piece"),
    ("no_legal_spot_for_any_move", "it asked for spots no piece could legally reach"),
    ("precedent_violation", "it broke an ADA layout rule for this kind of space"),
    ("less_useful", "it made the room less useful"),
    ("new problem", "it caused a new problem"),
    ("more problems than before", "it made more problems than it fixed"),
    ("within measurement noise", "it changed things by less than we can measure"),
    ("nothing measurable changed", "it didn't make any problem measurably better"),
    ("stopped", "it left part of the room we could no longer check"),
    ("lost its measured answer", "it left part of the room we could no longer check"),
    ("turned from a question", "it changed something we haven't measured yet"),
)
"""Checker and gate reasons as the end of a sentence for the owner, matched in order."""


@dataclass
class Rearranger:
    model: RearrangeModel | None
    provider: str = "fireworks"
    keep_warm_seconds: float = 300.0
    sampling: Sampling = field(default_factory=lambda: Sampling(attempts=1))
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep

    @property
    def available(self) -> bool:
        return self.model is not None

    @classmethod
    def from_settings(cls, settings: Settings) -> Rearranger:
        return cls(model=model_from_settings(settings), provider=settings.rearrange_provider,
                   keep_warm_seconds=settings.rearrange_keep_warm_seconds)


def model_from_settings(settings: Settings) -> RearrangeModel | None:
    if settings.rearrange_provider not in {"openrouter", "fireworks"}:
        raise ValueError("SP_REARRANGE_PROVIDER must be openrouter or fireworks")
    if settings.rearrange_fake_model:
        log.warning("SP_REARRANGE_FAKE_MODEL is on: suggestions come from a local stand-in")
        if settings.rearrange_provider == "openrouter":
            return FakeOpenRouter(answer=nudges_from_prompt, model=settings.rearrange_openrouter_model)
        return FakeFireworks(answer=nudges_from_prompt, warmups=1, controls_deployment=False)
    if settings.rearrange_provider == "openrouter":
        if not settings.openrouter_api_key:
            return None
        return OpenRouterRearrange(api_key=settings.openrouter_api_key,
                                   model=settings.rearrange_openrouter_model,
                                   reasoning_effort=settings.rearrange_reasoning_effort,
                                   token_cap=settings.rearrange_token_cap,
                                   cost_cap_dollars=settings.rearrange_cost_cap_dollars)
    if not settings.rearrange_model or not settings.fireworks_api_key:
        return None
    return FireworksModel(api_key=settings.fireworks_api_key, model=settings.rearrange_model,
                          deployment=settings.rearrange_deployment)


# --- queue and status ---------------------------------------------------------


def _require_latest(connection, scan_id: uuid.UUID, revision: int) -> None:
    if not repo.scan_exists(connection, scan_id):
        raise ApiProblem(404, "no scan")
    latest = repo.get_revision(connection, scan_id)
    if latest is None:
        raise ApiProblem(409, "the shop is still being measured")
    if latest["revision"] != revision:
        raise ApiProblem(409, STALE)


def _active(connection, scan_id: uuid.UUID, revision: int) -> bool:
    return connection.execute(
        "SELECT 1 FROM jobs WHERE scan_id=? AND kind=? AND revision=? AND state IN ('queued', 'running')",
        (str(scan_id), REARRANGE, revision),
    ).fetchone() is not None


def queue_suggestion(database, worker, rearranger: Rearranger, scan_id: uuid.UUID,
                     body: RearrangementRequest) -> RearrangementStatus:
    """Start a suggestion for the latest revision, or report the one already on its way."""
    if not rearranger.available:
        raise ApiProblem(503, UNAVAILABLE)
    with database.transaction() as connection:
        _require_latest(connection, scan_id, body.base_revision)
        if not _active(connection, scan_id, body.base_revision):
            connection.execute(
                "INSERT INTO rearrangements (scan_id, revision) VALUES (?, ?) ON CONFLICT(scan_id, revision)"
                " DO UPDATE SET phase='waiting', phase_reason=NULL, result_json=NULL",
                (str(scan_id), body.base_revision),
            )
            repo.queue_job_again(connection, scan_id, REARRANGE, body.base_revision)
    worker.wake()
    return suggestion_status(database, rearranger, scan_id, body.base_revision)


def suggestion_status(database, rearranger: Rearranger, scan_id: uuid.UUID, revision: int) -> RearrangementStatus:
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = connection.execute(
            "SELECT r.phase, r.phase_reason, r.result_json, j.state, j.error FROM rearrangements r JOIN jobs j"
            " ON r.scan_id=j.scan_id AND r.revision=j.revision AND j.kind=? WHERE r.scan_id=? AND r.revision=?",
            (REARRANGE, str(scan_id), revision),
        ).fetchone()
    available = rearranger.available
    common = {"base_revision": revision, "available": available,
              "unavailable_reason": None if available else UNAVAILABLE}
    if row is None:
        return RearrangementStatus(**common, state="idle")
    working = row["state"] in ("queued", "running")
    return RearrangementStatus(
        **common, state=row["state"], phase=row["phase"] if working else None,
        phase_reason=row["phase_reason"] if working else None, error=row["error"],
        result=RearrangementSuggestion.model_validate_json(row["result_json"]) if row["result_json"] else None,
    )


def _set_phase(database, scan_id: uuid.UUID, revision: int, phase: str, reason: str | None = None) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE rearrangements SET phase=?, phase_reason=? WHERE scan_id=? AND revision=?",
                           (phase, reason, str(scan_id), revision))


# --- the job -------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    graph: SceneGraph
    stored_scenario: Scenario | None
    """The route the owner confirmed, or None; production checks read it exactly as the panel does."""

    @property
    def scenario(self) -> Scenario:
        return self.stored_scenario or suggest_scenario(self.graph)


def _inputs(database, scan_id: uuid.UUID, revision: int) -> Inputs:
    with database.connect() as connection:
        row = repo.get_revision(connection, scan_id, revision)
        scenario = repo.get_scenario(connection, scan_id)
    if row is None:
        raise ModelFailed("That layout isn't there any more. Reload the page and ask again.")
    return Inputs(repo.graph_of(row), scenario)


def ask_patiently(rearranger: Rearranger, messages: list[dict], on_warming: Callable[[], None]) -> list[str]:
    """Ask, and while the deployment is starting from zero, wait and ask again for up to ten minutes."""
    model, started, delay = rearranger.model, rearranger.clock(), FIRST_RETRY_SECONDS
    while True:
        try:
            return model.complete(messages, rearranger.sampling)
        except ModelWarming:
            on_warming()
            if rearranger.clock() - started + delay > WARMUP_LIMIT_SECONDS:
                raise ModelFailed(STILL_STARTING) from None
            rearranger.sleep(delay)
            delay = min(delay * RETRY_GROWTH, LONGEST_RETRY_SECONDS)


def run_suggestion(database, rearranger: Rearranger, stages, scan_id: uuid.UUID, revision: int) -> None:
    if hasattr(rearranger.model, "reset_job"):
        rearranger.model.reset_job()
    inputs = _inputs(database, scan_id, revision)
    plan = scan_plan(scan_id, inputs.graph, inputs.scenario)
    checker = whole_checker(plan)
    problems = checker.fixable_problems(checker.assess(plan.graph))
    found = Search()
    if problems:
        _set_phase(database, scan_id, revision, "asking_model")
        warming = lambda: _set_phase(database, scan_id, revision, "starting_model")  # noqa: E731
        with deployment_lease(database, rearranger):
            found = search(plan, checker, problems,
                           lambda messages: ask_patiently(rearranger, messages, warming),
                           provider=rearranger.provider,
                           progress=lambda phase, reason: _set_phase(database, scan_id, revision, phase, reason))
    _set_phase(database, scan_id, revision, "checking")
    suggestion = describe(found, inputs, stages, revision, nothing_to_fix=not problems, small=plan.small,
                          rearranger=rearranger)
    with database.transaction() as connection:
        connection.execute("UPDATE rearrangements SET result_json=? WHERE scan_id=? AND revision=?",
                           (suggestion.model_dump_json(), str(scan_id), revision))
        if suggestion.accepted:
            record_accepted(connection, scan_id, revision, graph_hash(inputs.graph), suggestion, found.chains)


# --- describing the result, in the panel's own numbers --------------------------


def reason_clause(reason: str) -> str:
    first = re.split(r"[,;]", reason, maxsplit=1)[0].strip()
    return next((clause for needle, clause in REASON_CLAUSES if needle in first), "it didn't pass our checks")


def _attempt(verdict: Verdict) -> RearrangementAttempt:
    return RearrangementAttempt(accepted=verdict.gate_accepts, reward=verdict.reward,
                                reason="" if verdict.gate_accepts else reason_clause(verdict.reason))


def _pieces(count: int) -> str:
    return "1 piece" if count == 1 else f"{count} pieces"


def problems_in(findings: list[Finding]) -> list[Finding]:
    """What the panel counts as things to fix."""
    return [finding for finding in findings if finding.outcome == "problem"]


def furniture_can_fix(findings: list[Finding]) -> int:
    pack = load_pack()
    return sum(1 for finding in problems_in(findings) if pack.by_id(finding.check_id).rearrangeable)


def accepted_message(pieces: int, before: list[Finding], after: list[Finding]) -> str:
    """The change in the same production findings the panel shows, so the two never disagree.

    What is left is not repeated: the panel's own "N things still to fix" sits directly above.
    """
    fixable, cleared = furniture_can_fix(before), furniture_can_fix(before) - furniture_can_fix(after)
    if cleared > 0:
        return f"Moving {_pieces(pieces)} clears {cleared} of the {fixable} problems furniture can fix here."
    return f"Moving {_pieces(pieces)} gives the tightest spots measurably more room."


def rejected_message(attempts: list[RearrangementAttempt]) -> str:
    if not attempts:
        return NO_ANSWER
    clause = Counter(attempt.reason for attempt in attempts).most_common(1)[0][0]
    if len(attempts) == 1:
        return f"The layout the model tried didn't pass our checks, because {clause}."
    return f"None of the {len(attempts)} layouts the model tried passed our checks, mostly because {clause}."


def _nothing_chosen_message(found: Search, attempts: list[RearrangementAttempt], nothing_to_fix: bool) -> str:
    if nothing_to_fix:
        return NOTHING_TO_FIX
    if found.parts == 0:
        return NO_WINDOWS
    if found.budget_reached:
        return "The cost limit stopped this suggestion before a layout passed the checks."
    if found.whole_scan_reason:
        return f"The model's layouts worked in their own parts of the floor but not together, because " \
               f"{reason_clause(found.whole_scan_reason)}."
    return rejected_message(attempts)


def _reward_parts(verdict: Verdict) -> RewardParts:
    return RewardParts(reward=verdict.reward, recovered=round(verdict.shortfall_recovered, 4),
                       all_clear=verdict.fixable_left == 0, usability=verdict.usability or 0.0,
                       disruption_meters=round(verdict.disruption_meters, 4))


def describe(found: Search, inputs: Inputs, stages, revision: int, *, nothing_to_fix: bool,
             small: bool, rearranger: Rearranger) -> RearrangementSuggestion:
    """The chosen layout with production findings before and after, or why there is none."""
    attempts = [_attempt(verdict) for verdict in found.verdicts]
    model = rearranger.model
    cost = {"base_revision": revision, "attempts": attempts, "model_calls": found.model_calls,
            "windows": 0 if small else found.parts, "provider": rearranger.provider,
            "model": getattr(model, "model", None), "rounds": sum(len(chain["rounds"]) for chain in found.chains),
            "prompt_tokens": getattr(model, "prompt_tokens", 0),
            "completion_tokens": getattr(model, "completion_tokens", 0),
            "cost_dollars": getattr(model, "cost_dollars", 0.0),
            "snap_rescues": found.snap_rescues, "budget_reached": found.budget_reached}
    if found.chosen is None:
        message = _nothing_chosen_message(found, attempts, nothing_to_fix)
        return RearrangementSuggestion(accepted=False, message=message, **cost)
    moves = found.chosen.moves
    suggested = apply_moves(inputs.graph, moves)
    before = stages.assess(inputs.graph, inputs.stored_scenario, revision + 1).findings
    after = stages.assess(suggested, inputs.stored_scenario, revision + 1).findings
    message = accepted_message(len(moves), before, after)
    if found.chosen.snapped:
        message += " A small adjustment cleared a collision."
    return RearrangementSuggestion(
        suggestion_id=str(uuid.uuid4()), accepted=True,
        message=message, moves=moves,
        graph_hash=graph_hash(suggested), findings_before=before, findings_after=after,
        reward=_reward_parts(found.chosen.verdict), **cost,
    )


# --- letting the deployment run, and scaling it back to zero ------------------


def _controls_deployment(rearranger: Rearranger) -> bool:
    return rearranger.model is not None and rearranger.model.controls_deployment


def _hold(database) -> bool:
    """Mark the deployment in use, and say whether it was already allowed to run."""
    with database.transaction() as connection:
        row = connection.execute("SELECT may_run FROM rearrange_deployments WHERE name=?", (LEASE,)).fetchone()
        connection.execute(
            "INSERT INTO rearrange_deployments (name, may_run, scale_down_after) VALUES (?, 1, NULL)"
            " ON CONFLICT(name) DO UPDATE SET may_run=1, scale_down_after=NULL",
            (LEASE,),
        )
    return bool(row and row["may_run"])


def _release(database, scale_down_after: float) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE rearrange_deployments SET scale_down_after=? WHERE name=?",
                           (scale_down_after, LEASE))


@contextlib.contextmanager
def deployment_lease(database, rearranger: Rearranger):
    """Let the deployment run one replica for the block, then leave it warm for `keep_warm_seconds`.

    The row is marked before the PATCH is sent, so a PATCH that half-happened
    still gets scaled back down.
    """
    if not _controls_deployment(rearranger):
        yield
        return
    already_running = _hold(database)
    try:
        if not already_running:
            rearranger.model.allow_one_replica()
        yield
    finally:
        _release(database, rearranger.clock() + rearranger.keep_warm_seconds)


def _due_to_scale_down(database, now: float) -> bool:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT may_run, scale_down_after FROM rearrange_deployments WHERE name=?", (LEASE,)
        ).fetchone()
        busy = connection.execute(
            "SELECT 1 FROM jobs WHERE kind=? AND state IN ('queued', 'running')", (REARRANGE,)
        ).fetchone()
    if row is None or not row["may_run"] or busy:
        return False
    return row["scale_down_after"] is None or row["scale_down_after"] <= now


def scale_down_when_idle(database, rearranger: Rearranger) -> bool:
    """Scale the deployment to zero once its keep-warm window has passed and no suggestion is waiting.

    A deployment marked as running with no window at all was left by a job the
    server never finished, so it is scaled down straight away. Returns whether
    it scaled anything down.
    """
    if not _controls_deployment(rearranger) or not _due_to_scale_down(database, rearranger.clock()):
        return False
    try:
        rearranger.model.scale_to_zero()
    except (ModelFailed, ValueError) as error:
        log.warning("could not scale the rearrangement deployment to zero yet: %s", error)
        return False
    with database.transaction() as connection:
        connection.execute("UPDATE rearrange_deployments SET may_run=0, scale_down_after=NULL WHERE name=?",
                           (LEASE,))
    return True


def failure_text(error: Exception) -> str:
    return str(error) if isinstance(error, ModelFailed) else BROKE
