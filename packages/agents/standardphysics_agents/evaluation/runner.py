"""Running the dataset, and keeping the per-case results.

The local JSON is the authoritative record, because it exists whether or not
anybody has a W&B account and CI has none. When Weave is configured the same
rows go there too, so the per-case results are retrievable in the place the
rest of the trace tree lives.

A case that raises does not take the run down. It is recorded as a failure and
the run is marked incomplete, which is what the gate reads: an evaluation that
did not complete cannot accept anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from standardphysics_contracts import MeasurementProvider

from ..assess import assess
from ..fix.search import propose_fix
from ..router import LocalPolicyRouter, state_for
from ..rules import AgentRulePack, VerificationLedger, load_ledger, load_pack
from ..tracing import is_live, project_url, traced
from .dataset import Case, dataset
from .gate import accepts
from .scorers import LOWER_IS_BETTER, SCORERS, CaseOutcome

FIX_CANDIDATE_LIMIT = 16
"""A shorter ladder for the dataset than for a real shop.

Thirty cases times a full ladder is minutes of measuring, and the scorer only
asks whether a rearrangement exists at all. Eight was a guess, and it was too
short: at a 20 mm occupancy grid the dataset resolves 0.444 of the fixes it
should at eight candidates and 0.778 at sixteen, and at 30 mm it goes from
0.889 to 1.000. Sixteen is where that stops moving, and twenty-four measures
the same candidates as sixteen everywhere. At the 25 mm the pipeline ships,
every fix is found in the first four, which is why a grid that only looked at
25 mm and either side of it read the ladder as doing nothing.
"""


@dataclass(frozen=True)
class EvaluationResult:
    rulepack_version: str
    ran_at: datetime
    completed: bool
    outcomes: list[CaseOutcome]
    scores: dict[str, float] = field(default_factory=dict)
    per_case: dict[str, dict[str, float | None]] = field(default_factory=dict)
    weave_url: str | None = None
    dataset_url: str | None = None
    """Where the rows went, once they have gone. `None` until then, and after a
    publish a third party refused."""

    @property
    def failures(self) -> list[str]:
        return [o.case.id for o in self.outcomes if o.error]

    def score(self, name: str) -> float | None:
        return self.scores.get(name)

    def rows(self) -> list[dict[str, Any]]:
        return [
            {
                "case": outcome.case.id,
                "description": outcome.case.description,
                "problems": sorted(outcome.reported_problems),
                "expected_problems": sorted(outcome.case.expected_problems),
                "questions": sorted(outcome.reported_questions),
                "action": action_name(outcome),
                "expected_action": outcome.case.expected_action,
                "fix": outcome.fix.message if outcome.fix else None,
                "gate_accepted": outcome.gate.accepted if outcome.gate else None,
                "error": outcome.error,
                **{name: value for name, value in self.per_case[outcome.case.id].items()},
            }
            for outcome in self.outcomes
        ]


def action_name(outcome: CaseOutcome) -> str | None:
    decision = outcome.decision
    if decision is None:
        return None
    return getattr(decision, "action", None) or f"rejected:{decision.reason}"


def _score_case(outcome: CaseOutcome) -> dict[str, float | None]:
    return {name: scorer(outcome) for name, scorer in SCORERS.items()}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _aggregate(per_case: dict[str, dict[str, float | None]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for name in SCORERS:
        values = [
            row[name] for row in per_case.values() if row[name] is not None
        ]
        if values:
            scores[name] = _mean(values)
    return scores


@traced("evaluation.case")
def run_case(
    case: Case,
    measure: MeasurementProvider,
    rules: AgentRulePack,
    ledger: VerificationLedger,
    router,
    run_fixes: bool,
    *,
    fix_candidates: int = FIX_CANDIDATE_LIMIT,
) -> CaseOutcome:
    try:
        return _run_case(
            case, measure, rules, ledger, router, run_fixes, fix_candidates
        )
    except Exception as error:  # a broken case must not take the run down
        return CaseOutcome(
            case=case,
                result=assess(
                case.graph, case.scenario, measure,
                rules=rules, ledger=VerificationLedger(), max_tier=case.max_tier,
            ),
            error=f"{type(error).__name__}: {error}",
        )


def _run_case(
    case, measure, rules, ledger, router, run_fixes, fix_candidates
) -> CaseOutcome:
    before = assess(
        case.graph, case.scenario, measure,
        rules=rules, ledger=ledger, max_tier=case.max_tier,
    )
    state = state_for(
        before.findings,
        case.graph,
        rules,
        unevaluated=tuple(gap.rule_id for gap in before.unevaluated),
        actions_taken=case.actions_taken,
    )
    decision = router.decide(state)

    if not (run_fixes and case.fix_should_resolve):
        return CaseOutcome(case=case, result=before, decision=decision)

    fix = propose_fix(
        case.graph, case.scenario, measure, before.problems,
        rules=rules, ledger=ledger, baseline=before,
        max_tier=case.max_tier, limit=fix_candidates,
    )
    after = (
        assess(
            fix.graph, case.scenario, measure,
            rules=rules, ledger=ledger, max_tier=case.max_tier,
        )
        if fix.graph is not None
        else None
    )
    return CaseOutcome(
        case=case,
        result=before,
        decision=decision,
        fix=fix,
        after=after,
        gate=accepts(before, after) if after else None,
    )


@traced("evaluation.run")
def evaluate(
    *,
    measure: MeasurementProvider | None = None,
    rules: AgentRulePack | None = None,
    ledger: VerificationLedger | None = None,
    router=None,
    cases: list[Case] | None = None,
    run_fixes: bool = True,
    fix_candidates: int = FIX_CANDIDATE_LIMIT,
    publish: bool = True,
) -> EvaluationResult:
    pack = rules or load_pack()
    verified = ledger if ledger is not None else load_ledger()
    provider = measure or _default_measurements()
    picked = cases if cases is not None else dataset()

    decider = router or LocalPolicyRouter()
    outcomes = [
        run_case(
            case, provider, pack, verified, decider, run_fixes,
            fix_candidates=fix_candidates,
        )
        for case in picked
    ]
    per_case = {outcome.case.id: _score_case(outcome) for outcome in outcomes}
    result = EvaluationResult(
        rulepack_version=pack.version,
        ran_at=datetime.now(timezone.utc),
        completed=not any(outcome.error for outcome in outcomes),
        outcomes=outcomes,
        scores=_aggregate(per_case),
        per_case=per_case,
        weave_url=project_url(),
    )
    if publish and is_live():
        return replace(result, dataset_url=publish_to_weave(result))
    return result


def _default_measurements() -> MeasurementProvider:
    from standardphysics_pipeline import PipelineMeasurements

    return PipelineMeasurements()


def save(result: EvaluationResult, path: Path) -> Path:
    """Per-case results on disk, so a run can be looked at again later."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "rulepack_version": result.rulepack_version,
                "ran_at": result.ran_at.isoformat(),
                "completed": result.completed,
                "failures": result.failures,
                "scores": result.scores,
                "lower_is_better": sorted(LOWER_IS_BETTER),
                "weave_url": result.weave_url,
                "dataset_url": result.dataset_url,
                "cases": result.rows(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def publish_to_weave(result: EvaluationResult) -> str | None:
    """The same rows, in the place the traces are.

    Returns where they landed, so a run can say it. Every failure mode here is
    a third party's: no account, no network, an SDK that moved the accessor
    this reads. None of them may stop an evaluation that has already run, so
    this reports that it did not publish and the local record stands.
    """
    try:
        import weave
        from weave.trace import urls

        published = weave.publish(
            weave.Dataset(
                name=f"standardphysics-{result.rulepack_version}", rows=result.rows()
            )
        )
        return urls.object_version_path(
            published.entity, published.project, published.name, published.digest
        )
    except Exception:
        return None
