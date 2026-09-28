"""Running the dataset, and keeping the per-case results.

The local JSON is the authoritative record, because it exists whether or not
anybody has a W&B account and CI has none. When Weave is configured the run is
also logged to Weave's Evals tab, one prediction per case with every score,
labelled with the router that answered. Two routers scored on the same cases
then sit side by side there, next to the traces.

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
from ..router.decision import Rejected
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

EVALUATION_NAME = "standardphysics-loop"
DATASET_NAME = "standardphysics-cases"


@dataclass(frozen=True)
class EvaluationResult:
    rulepack_version: str
    ran_at: datetime
    completed: bool
    outcomes: list[CaseOutcome]
    scores: dict[str, float] = field(default_factory=dict)
    per_case: dict[str, dict[str, float | None]] = field(default_factory=dict)
    weave_url: str | None = None
    evaluation_url: str | None = None
    """Where this run sits in Weave's Evals tab. `None` when it was not logged,
    including when a third party refused it."""

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
    if isinstance(decision, Rejected):
        return f"rejected:{decision.reason}"
    return decision.action


def _score_case(outcome: CaseOutcome) -> dict[str, float | None]:
    return {name: scorer(outcome) for name, scorer in SCORERS.items()}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _aggregate(per_case: dict[str, dict[str, float | None]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for name in SCORERS:
        values = [
            value for row in per_case.values() if (value := row[name]) is not None
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
    version: str | None = None,
) -> EvaluationResult:
    """Score every case. `version` labels the run in Weave's Evals tab and
    defaults to the name of the router that actually answered."""
    pack = rules or load_pack()
    verified = ledger if ledger is not None else load_ledger()
    provider = measure or _default_measurements()
    picked = cases if cases is not None else dataset()

    decider = router or LocalPolicyRouter()
    logger = (
        _evaluation_logger(str(version or getattr(decider, "provider", "unknown")))
        if publish and is_live()
        else None
    )
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
    if logger is None:
        return result
    return replace(result, evaluation_url=publish_evaluation(logger, result))


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
                "evaluation_url": result.evaluation_url,
                "cases": result.rows(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _evaluation_logger(model: str) -> Any:
    """Opened before the first case, so the calls a case makes land under it.

    Every failure mode here is a third party's: no account, no network, an SDK
    that moved. None of them may stop an evaluation from running, so a refusal
    leaves the run unlogged and the local record stands.
    """
    try:
        import weave

        return weave.EvaluationLogger(
            name=EVALUATION_NAME, model=model, dataset=DATASET_NAME
        )
    except Exception:
        return None


def publish_evaluation(logger: Any, result: EvaluationResult) -> str | None:
    """One prediction per case with every score it has, then the means.

    Returns where the evaluation sits in Weave, or `None` when Weave refused
    any part of it.
    """
    try:
        for outcome in result.outcomes:
            prediction = logger.log_prediction(
                inputs={"case": outcome.case.id}, output=action_name(outcome)
            )
            for name, value in result.per_case[outcome.case.id].items():
                if value is not None:
                    prediction.log_score(scorer=name, score=value)
            prediction.finish()
        logger.log_summary(result.scores)
        return logger.ui_url
    except Exception:
        return None
