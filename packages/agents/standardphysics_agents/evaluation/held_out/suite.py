"""The held-out suite: fresh questions, real rooms, scored twice.

A run does the same thing twice. Once against scenes named the way the scan named
them, and once against the same geometry with every name replaced by a token
nobody has a word for. The two scores should match. Where they do not, something
was answering from an English word rather than from a measurement, and the gap is
the size of that problem.

The scenes it scores on are held out of the split the run reports, so nothing can
be developed against them. Nothing here has an expected answer, and nothing here
is stored between runs.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ...models import OpenRouter
from .judge import Verdict, judge
from .questions import Question, write
from .scenes import Scene, real_scenes, scramble, split


@dataclass(frozen=True)
class Run:
    """One pass over one set of scenes, named or scrambled."""

    scrambled: bool
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def answerable(self) -> list[Verdict]:
        return [verdict for verdict in self.verdicts if verdict.question.answerable]

    @property
    def unanswerable(self) -> list[Verdict]:
        return [verdict for verdict in self.verdicts if not verdict.question.answerable]

    @property
    def score(self) -> float:
        return _share([verdict.passed for verdict in self.verdicts])

    @property
    def answered_well(self) -> float:
        return _share([verdict.passed for verdict in self.answerable])

    @property
    def refused_well(self) -> float:
        return _share([verdict.passed for verdict in self.unanswerable])

    @property
    def numbers_traced(self) -> float:
        answered = [verdict for verdict in self.verdicts if not verdict.refused]
        return _share([verdict.numbers_traced for verdict in answered])

    @property
    def hallucinations(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.hallucinated)

    @property
    def unparsed(self) -> float:
        """How often the app would not take the sentence at all.

        A question the resolver cannot parse never reaches a measurement, so this
        separates a system that judged it could not answer from one that did not
        understand what was asked.
        """
        return _share([verdict.rejected is not None for verdict in self.verdicts])


@dataclass(frozen=True)
class Result:
    seed: int
    built_on: list[str]
    scored_on: list[str]
    named: Run
    scrambled: Run

    @property
    def scramble_gap(self) -> float:
        """How much of the score was resting on the names."""
        return self.named.score - self.scrambled.score


WORKERS = 8
"""How many questions are in flight at once.

Every call waits on a network round trip, so the suite is idle almost all of the
time it takes. Eighty questions over two scenes, scored twice, is 320 of them;
serially that is over an hour of waiting for one number.
"""


def run(
    root: pathlib.Path,
    seed: int,
    per_scene: int = 80,
    held_out: int = 2,
    model: OpenRouter | None = None,
    workers: int = WORKERS,
    watch: Callable[[str], None] | None = None,
) -> Result:
    """`watch` is called with a line of progress, because a run is long enough
    that silence is indistinguishable from a hang."""
    built_on, scored_on = split(real_scenes(root), seed=seed, held_out=held_out)
    model = model or OpenRouter()
    said = watch or (lambda line: None)
    return Result(
        seed=seed,
        built_on=[scene.name for scene in built_on],
        scored_on=[scene.name for scene in scored_on],
        named=_pass(scored_on, per_scene, seed, model, workers, said, scrambled=False),
        scrambled=_pass(
            [scramble(scene, seed) for scene in scored_on],
            per_scene,
            seed,
            model,
            workers,
            said,
            scrambled=True,
        ),
    )


def _pass(
    scenes: list[Scene],
    per_scene: int,
    seed: int,
    model: OpenRouter,
    workers: int,
    said: Callable[[str], None],
    *,
    scrambled: bool,
) -> Run:
    label = "scrambled" if scrambled else "named"
    asked = _ask_for_questions(scenes, per_scene, seed, model, workers, said, label)
    said(f"{label}: scoring {len(asked)} answers")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        verdicts = list(pool.map(lambda pair: _score(*pair, model), asked))
    said(f"{label}: done, {sum(verdict.passed for verdict in verdicts)} of {len(verdicts)} right")
    return Run(scrambled=scrambled, verdicts=verdicts)


def _ask_for_questions(
    scenes: list[Scene],
    per_scene: int,
    seed: int,
    model: OpenRouter,
    workers: int,
    said: Callable[[str], None],
    label: str,
) -> list[tuple[Scene, Question]]:
    asked: list[tuple[Scene, Question]] = []
    for scene in scenes:
        written = write(scene, per_scene, seed, model, workers)
        said(f"{label}: {len(written)} questions about {scene.name}")
        asked.extend((scene, question) for question in written)
    return asked


def _score(scene: Scene, question: Question, model: OpenRouter) -> Verdict:
    return judge(question, _answer(question, scene), scene, model)


def _answer(question: Question, scene: Scene):
    from ...ask import ask
    from ...entrypoint import default_measurements

    return ask(question.text, scene.graph, _scenario(scene), default_measurements())


def _scenario(scene: Scene):
    """The route the app would lay over this room in production.

    Imported here rather than at the top because the API owns it and this package
    must not depend on the API to be imported.
    """
    from standardphysics_api.scenario import suggest_scenario

    return suggest_scenario(scene.graph)


def _share(outcomes: list[bool]) -> float:
    return sum(outcomes) / len(outcomes) if outcomes else 0.0


def report(result: Result) -> str:
    """The numbers, plainly enough to paste into a pull request."""
    lines = [
        f"Held-out suite, seed {result.seed}",
        f"  built on   {', '.join(result.built_on)}",
        f"  scored on  {', '.join(result.scored_on)}",
        "",
        f"  {'':22} {'named':>8} {'scrambled':>10}",
        _row("questions", len(result.named.verdicts), len(result.scrambled.verdicts)),
        _row("score", result.named.score, result.scrambled.score),
        _row("answered well", result.named.answered_well, result.scrambled.answered_well),
        _row("refused well", result.named.refused_well, result.scrambled.refused_well),
        _row("numbers traced", result.named.numbers_traced, result.scrambled.numbers_traced),
        _row("answered anyway", result.named.hallucinations, result.scrambled.hallucinations),
        _row("not understood", result.named.unparsed, result.scrambled.unparsed),
        "",
        f"  names were worth {result.scramble_gap:+.1%} of the score",
    ]
    return "\n".join(lines)


def transcript(result: Result) -> list[dict]:
    """Every question, what the app said, and how it scored.

    A score nobody can read behind is a score nobody can act on, so a run can be
    written out and gone through by hand.
    """
    return [
        {
            "run": "scrambled" if run.scrambled else "named",
            "question": verdict.question.text,
            "approach": verdict.question.approach,
            "answerable": verdict.question.answerable,
            "answer": verdict.answer,
            "rejected": verdict.rejected,
            "refused": verdict.refused,
            "supported": verdict.supported,
            "numbers_traced": verdict.numbers_traced,
            "passed": verdict.passed,
            "why": verdict.reason,
        }
        for run in (result.named, result.scrambled)
        for verdict in run.verdicts
    ]


def _row(label: str, named, scrambled) -> str:
    return f"  {label:22} {_cell(named):>8} {_cell(scrambled):>10}"


def _cell(value) -> str:
    return f"{value:.1%}" if isinstance(value, float) else str(value)
