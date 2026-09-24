"""Suggest a rearrangement: the fine-tuned model proposes, the training checker decides.

A suggestion is a background job on its own worker lane, because a Fireworks
deployment at zero replicas takes minutes to start and the page should poll
rather than hold a request open that long:

    POST /api/scans/{id}/rearrangement-suggestion   queue a job for the latest revision
    GET  /api/scans/{id}/rearrangement-suggestion?revision=N   poll it, and learn if the feature is on

The job builds the prompt exactly as training did (`training.prompt_messages`
on the scan with its phantoms pinned, as `training.rooms.plan_scan` pins
them), asks for four answers at the training evaluation's settings, and
scores each with `training.reward.score_completion`: the edits parser,
`apply_moves`, `violations` (fixture, keep-clear, travel and room-to-use
rules included) and the gate with improvement required, as the fix agent
uses it. The best accepted answer comes back as moves; nothing is saved.

While the job runs the deployment may run one replica. Afterwards it stays
warm for `keep_warm_seconds`, so a second click soon after pays no cold start,
and then the rearrange lane scales it to zero. That step reads the database,
not a timer, so it still happens after a failed job or a restart.
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

from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training import TrainingChecker, parse_edits, prompt_messages
from standardphysics_agents.training.edits import node_moves
from standardphysics_agents.training.phantoms import phantoms, pin, scan_errors, without_nodes, without_unmeasured
from standardphysics_agents.training.reward import MOVED_PINNED, Verdict, score_completion
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
    keep_warm_seconds: float = 300.0
    sampling: Sampling = field(default_factory=Sampling)
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep

    @property
    def available(self) -> bool:
        return self.model is not None

    @classmethod
    def from_settings(cls, settings: Settings) -> Rearranger:
        return cls(model=model_from_settings(settings), keep_warm_seconds=settings.rearrange_keep_warm_seconds)


def model_from_settings(settings: Settings) -> RearrangeModel | None:
    if settings.rearrange_fake_model:
        log.warning("SP_REARRANGE_FAKE_MODEL is on: suggestions come from a local stand-in, not the trained model")
        return FakeFireworks(answer=nudges_from_prompt, warmups=1, controls_deployment=False)
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
                " DO UPDATE SET phase='waiting', result_json=NULL",
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
            "SELECT r.phase, r.result_json, j.state, j.error FROM rearrangements r JOIN jobs j"
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
        **common, state=row["state"], phase=row["phase"] if working else None, error=row["error"],
        result=RearrangementSuggestion.model_validate_json(row["result_json"]) if row["result_json"] else None,
    )


def _set_phase(database, scan_id: uuid.UUID, revision: int, phase: str) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE rearrangements SET phase=? WHERE scan_id=? AND revision=?",
                           (phase, str(scan_id), revision))


# --- the job -------------------------------------------------------------------


def training_room(graph: SceneGraph, scenario: Scenario) -> tuple[SceneGraph, TrainingChecker]:
    """The scan as training saw it: unmeasured boxes and floating scan errors out, phantoms pinned."""
    measured = without_unmeasured(graph)
    pinned = phantoms(measured)
    removed = {node.id for node in scan_errors(measured, pinned)}
    room = pin(without_nodes(measured, removed), pinned)
    checker = TrainingChecker(scenario, pinned=frozenset(item.node_id for item in pinned), owner_layout=room)
    return room, checker


def _inputs(database, scan_id: uuid.UUID, revision: int) -> tuple[SceneGraph, Scenario]:
    with database.connect() as connection:
        row = repo.get_revision(connection, scan_id, revision)
        scenario = repo.get_scenario(connection, scan_id)
    if row is None:
        raise ModelFailed("That layout isn't there any more. Reload the page and ask again.")
    graph = repo.graph_of(row)
    return graph, scenario or suggest_scenario(graph)


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


def run_suggestion(database, rearranger: Rearranger, scan_id: uuid.UUID, revision: int) -> None:
    graph, scenario = _inputs(database, scan_id, revision)
    room, checker = training_room(graph, scenario)
    messages = prompt_messages(room, checker)
    _set_phase(database, scan_id, revision, "asking_model")
    with deployment_lease(database, rearranger):
        completions = ask_patiently(
            rearranger, messages, lambda: _set_phase(database, scan_id, revision, "starting_model")
        )
    if not completions:
        raise ModelFailed(NO_ANSWER)
    _set_phase(database, scan_id, revision, "checking")
    suggestion = judge(graph, room, checker, completions, revision)
    with database.transaction() as connection:
        connection.execute("UPDATE rearrangements SET result_json=? WHERE scan_id=? AND revision=?",
                           (suggestion.model_dump_json(), str(scan_id), revision))


# --- judging the answers -------------------------------------------------------


def reason_clause(reason: str) -> str:
    first = re.split(r"[,;]", reason, maxsplit=1)[0].strip()
    return next((clause for needle, clause in REASON_CLAUSES if needle in first), "it didn't pass our checks")


def _attempt(verdict: Verdict) -> RearrangementAttempt:
    return RearrangementAttempt(accepted=verdict.gate_accepts, reward=verdict.reward,
                                reason="" if verdict.gate_accepts else reason_clause(verdict.reason))


def _pieces(count: int) -> str:
    return "1 piece" if count == 1 else f"{count} pieces"


def accepted_message(pieces: int, before: list[Finding], after: list[Finding]) -> str:
    if not after:
        return f"Moving {_pieces(pieces)} clears every problem furniture can fix here."
    cleared = len(before) - len(after)
    if cleared > 0:
        return f"Moving {_pieces(pieces)} clears {cleared} of the {len(before)} problems furniture can fix here."
    return f"Moving {_pieces(pieces)} gives the tightest spots measurably more room."


def rejected_message(attempts: list[RearrangementAttempt]) -> str:
    clause = Counter(attempt.reason for attempt in attempts).most_common(1)[0][0]
    if len(attempts) == 1:
        return f"The layout the model tried didn't pass our checks, because {clause}."
    return f"None of the {len(attempts)} layouts the model tried passed our checks, mostly because {clause}."


def _reward_parts(verdict: Verdict) -> RewardParts:
    return RewardParts(reward=verdict.reward, recovered=round(verdict.shortfall_recovered, 4),
                       all_clear=verdict.fixable_left == 0, usability=verdict.usability or 0.0,
                       disruption_meters=round(verdict.disruption_meters, 4))


def judge(graph: SceneGraph, room: SceneGraph, checker: TrainingChecker, completions: list[str],
          revision: int) -> RearrangementSuggestion:
    """Score every answer as training did, and keep the best one the gate accepts."""
    verdicts = [score_completion(text, room, checker) for text in completions]
    attempts = [_attempt(verdict) for verdict in verdicts]
    accepted = [(verdict, text) for verdict, text in zip(verdicts, completions) if verdict.gate_accepts]
    if not accepted:
        return RearrangementSuggestion(base_revision=revision, accepted=False, message=rejected_message(attempts),
                                       attempts=attempts)
    verdict, text = max(accepted, key=lambda pair: pair[0].reward)
    moves = node_moves(parse_edits(text))
    before = checker.fixable_problems(checker.assess(room))
    after = checker.fixable_problems(checker.assess(apply_moves(room, moves)))
    return RearrangementSuggestion(
        base_revision=revision, accepted=True, message=accepted_message(len(moves), before, after), moves=moves,
        graph_hash=graph_hash(apply_moves(graph, moves)), findings_before=before, findings_after=after,
        reward=_reward_parts(verdict), attempts=attempts,
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
