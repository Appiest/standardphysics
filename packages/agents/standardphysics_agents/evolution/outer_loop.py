"""The outer loop: score the router, learn one lesson, keep it only if the scores say so.

Fang et al. (2025) describe a self-evolving system as an agent, an environment
and an optimizer in a closed loop. Here the agent is the review loop with
TypeSafe choosing each action, the environment is the labelled cases and the
rooms inside them, and the optimizer is this file.

One generation reads the cases the router got wrong, asks reflection for one
lesson, and scores a playbook carrying that lesson on those cases plus a few it
already got right. The lesson is kept only when the action scores rise and
nothing falls. Scoring a small trial set rather than every case is what keeps
a generation to a handful of TypeSafe calls.

What evolves is the router's instructions. The measurements, the rule pack and
`accepts()` are the fixed ground truth it is scored against and never move.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from standardphysics_contracts import MeasurementProvider

from ..evaluation.dataset import Case
from ..evaluation.runner import EvaluationResult, evaluate
from ..rules import AgentRulePack, VerificationLedger
from ..tracing import traced
from .memory import CONTROL_SCORES, Experience, experiences, load_memory, recall, remember
from .playbook import Lesson, Playbook
from .reflect import reflect

GUARD_CASES = 4
"""Cases the router already gets right, scored again with every candidate
lesson, so a lesson that fixes one case by breaking another is caught."""

GUARDED_ERROR = "measurement_error_in"

DEFAULT_RUN_PATH = Path("runs/evolution.json")

RouterFactory = Callable[[Playbook], object]


@dataclass(frozen=True)
class Generation:
    number: int
    playbook_version: int
    failures: tuple[str, ...]
    lesson: Lesson | None
    kept: bool
    reasons: tuple[str, ...]
    trial_cases: tuple[str, ...] = ()
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    evaluation_url: str | None = None


@dataclass(frozen=True)
class Evolution:
    generations: tuple[Generation, ...]
    playbook: Playbook
    baseline: dict[str, float]
    final: dict[str, float] | None


@dataclass(frozen=True)
class _Scoring:
    cases: list[Case]
    router_factory: RouterFactory
    measure: MeasurementProvider
    rules: AgentRulePack
    ledger: VerificationLedger
    memory_path: Path | None
    publish: bool
    run_fixes: bool

    def score(self, cases: list[Case], playbook: Playbook, label: str) -> EvaluationResult:
        result = evaluate(
            measure=self.measure,
            rules=self.rules,
            ledger=self.ledger,
            router=self.router_factory(playbook),
            cases=cases,
            run_fixes=self.run_fixes,
            publish=self.publish,
            version=label,
        )
        if self.memory_path is not None:
            remember(experiences(result, playbook.version), self.memory_path)
        return result

    def memory(self) -> list[Experience]:
        return load_memory(self.memory_path) if self.memory_path is not None else []


def lesson_helps(
    before: dict[str, dict[str, float | None]], after: dict[str, dict[str, float | None]]
) -> tuple[bool, tuple[str, ...]]:
    """Kept only when the action scores rise in total and none of them falls.

    `before` and `after` map case id to that case's scores on the same cases.
    """
    reasons: list[str] = []
    gained = 0.0
    for name in CONTROL_SCORES:
        change = _total(after, name, before) - _total(before, name, after)
        if change < 0:
            reasons.append(f"{name} fell by {-change:g}")
        gained += change
    if _total(after, GUARDED_ERROR, before) > _total(before, GUARDED_ERROR, after) + 1e-9:
        reasons.append(f"{GUARDED_ERROR} rose")
    if not reasons and gained <= 0:
        reasons.append("nothing improved")
    return not reasons, tuple(reasons)


def _total(scores: dict, name: str, other: dict) -> float:
    """The sum over cases scored on both sides, so a case one side skipped is not counted."""
    shared = [
        case_id
        for case_id in scores
        if case_id in other
        and scores[case_id].get(name) is not None
        and other[case_id].get(name) is not None
    ]
    return sum(scores[case_id][name] for case_id in shared)


def _means(scores: dict[str, dict[str, float | None]]) -> dict[str, float]:
    means: dict[str, float] = {}
    for name in (*CONTROL_SCORES, GUARDED_ERROR):
        values = [row[name] for row in scores.values() if row.get(name) is not None]
        if values:
            means[name] = sum(values) / len(values)
    return means


def _label(playbook: Playbook, suffix: str = "") -> str:
    return f"typesafe-playbook-v{playbook.version}{suffix}"


def _trial_ids(known: dict[str, Experience], failures: list[Experience]) -> list[str]:
    passing = sorted(case_id for case_id, item in known.items() if not item.failed)
    return [item.case_id for item in failures] + passing[:GUARD_CASES]


@traced("evolve.generation")
def _generation(
    scoring: _Scoring,
    number: int,
    playbook: Playbook,
    known: dict[str, Experience],
    tried: set[str],
    reflect_with,
) -> tuple[Generation, Playbook]:
    failures = [item for item in known.values() if item.failed]
    lesson = reflect(
        failures,
        recall(scoring.memory(), failures),
        playbook,
        avoid=frozenset(tried),
        model=reflect_with,
    )
    failed_ids = tuple(item.case_id for item in failures)
    if lesson is None:
        return Generation(number, playbook.version, failed_ids, None, False, ("no new lesson to try",)), playbook
    tried.add(lesson.text.casefold())
    return _trial(scoring, number, playbook, lesson, known, failures)


def _trial(
    scoring: _Scoring,
    number: int,
    playbook: Playbook,
    lesson: Lesson,
    known: dict[str, Experience],
    failures: list[Experience],
) -> tuple[Generation, Playbook]:
    candidate = playbook.with_lesson(lesson)
    trial_ids = _trial_ids(known, failures)
    cases = [case for case in scoring.cases if case.id in set(trial_ids)]
    result = scoring.score(cases, candidate, _label(candidate, "-trial"))
    after = {item.case_id: item for item in experiences(result, candidate.version)}
    before_scores = {case_id: known[case_id].scores for case_id in after}
    after_scores = {case_id: item.scores for case_id, item in after.items()}
    kept, reasons = lesson_helps(before_scores, after_scores)
    if kept:
        known.update(after)
    generation = Generation(
        number=number,
        playbook_version=playbook.version,
        failures=tuple(item.case_id for item in failures),
        lesson=lesson,
        kept=kept,
        reasons=reasons,
        trial_cases=tuple(trial_ids),
        before=_means(before_scores),
        after=_means(after_scores),
        evaluation_url=result.evaluation_url,
    )
    return generation, candidate if kept else playbook


@traced("evolve.run")
def evolve(
    *,
    cases: list[Case],
    generations: int,
    router_factory: RouterFactory,
    measure: MeasurementProvider,
    rules: AgentRulePack,
    ledger: VerificationLedger,
    playbook: Playbook | None = None,
    memory_path: Path | None = None,
    publish: bool = True,
    run_fixes: bool = False,
    reflect_with=None,
    confirm: bool = True,
) -> Evolution:
    """Run up to `generations` rounds of learn, trial and keep.

    Stops early when every case passes or reflection has nothing new to try.
    With `confirm`, a playbook that changed is scored once more on every case,
    so the before and after in Weave's Evals tab cover the same set.
    """
    if not 1 <= generations <= 20:
        raise ValueError("generations must be between 1 and 20")
    scoring = _Scoring(cases, router_factory, measure, rules, ledger, memory_path, publish, run_fixes)
    current = playbook or Playbook()
    baseline = scoring.score(cases, current, _label(current))
    known = {item.case_id: item for item in experiences(baseline, current.version)}
    tried: set[str] = set()
    history: list[Generation] = []
    for number in range(1, generations + 1):
        if not any(item.failed for item in known.values()):
            break
        generation, current = _generation(scoring, number, current, known, tried, reflect_with)
        history.append(generation)
        if generation.lesson is None:
            break
    changed = any(generation.kept for generation in history)
    final = scoring.score(cases, current, _label(current)).scores if confirm and changed else None
    return Evolution(tuple(history), current, baseline.scores, final)


def save_evolution(evolution: Evolution, path: Path = DEFAULT_RUN_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "baseline": evolution.baseline,
                "final": evolution.final,
                "playbook": evolution.playbook.to_json(),
                "generations": [asdict(generation) for generation in evolution.generations],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
