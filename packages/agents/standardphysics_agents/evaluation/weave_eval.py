"""The same cases and the same scorers, run as a Weave Evaluation.

`evaluate()` in runner.py scores one run and writes the rows to disk. This
module puts the same work in Weave's Evals tab, which draws a mean per scorer,
the per-case table behind each mean, and a side by side when the same cases are
scored against more than one configuration of the system. Comparing
configurations is the point: each one changes a single part and the columns say
what that part is worth. Swapping Lane B's measurement pipeline for the
fixtures' simplified stand-in is how you find out how much of the score rests
on measuring the room rather than on the rules.

A configuration is a `Setup` from configuration.py, which experiments.py also
publishes as W&B runs for ARIA to read. Scoring stays in scorers.py. A scorer
there reads a whole `CaseOutcome` — the assessment before a fix, the one after,
the node ids each label resolved to, the inches measured per check — which is
more than a dataset row can hold, so each scorer here reports the number that
module already computed instead of recomputing it from the row. A threshold and
a score each keep one definition.

Weave is optional everywhere else in this lane and stays optional here: import
this module without it installed and nothing happens until you call something,
which then says what to install.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any

from .configuration import DEFAULT_SETUPS, Setup, review
from .dataset import Case, dataset
from .runner import FIX_CANDIDATE_LIMIT
from .scorers import LOWER_IS_BETTER, SCORERS

DEFAULT_NAME = "shop-review"


def rows(cases: list[Case] | None = None) -> list[dict[str, Any]]:
    """One row per case, holding what a correct answer looks like.

    The scene graph stays out. A row is what someone reads in the Evals table
    to see what the case asked for, and `predict` looks the geometry up by id.
    """
    return [
        {
            "case": case.id,
            "description": case.description,
            "expected_problems": sorted(case.expected_problems),
            "forbidden_problems": sorted(case.forbidden_problems),
            "expected_questions": sorted(case.expected_questions),
            "expected_action": case.expected_action,
            "expects_a_fix": case.fix_should_resolve,
            "tier": case.max_tier,
        }
        for case in (cases if cases is not None else dataset())
    ]


def scorer(module: Any, name: str):
    """A Weave scorer reporting one number scorers.py already computed."""
    direction = " Lower is better." if name in LOWER_IS_BETTER else ""

    def score(output: dict[str, Any] | None) -> float | None:
        return (output or {}).get("scores", {}).get(name)

    score.__name__ = name
    score.__doc__ = f"{name}, from the evaluation's own scorers.{direction}"
    return module.op(score)


def evaluate_in_weave(
    setups: list[Setup] | tuple[Setup, ...] | None = None,
    *,
    cases: list[Case] | None = None,
    name: str = DEFAULT_NAME,
) -> dict[str, dict[str, Any]]:
    """Score every configuration against every case, in Weave.

    Returns Weave's own summary per configuration. `weave.init()` has to have
    run first, which `init_tracing()` does.
    """
    module = _weave()
    picked = list(cases if cases is not None else dataset())
    evaluation = _evaluation(module, picked, name)
    model_class = _model_class(module)
    scored: dict[str, dict[str, Any]] = {}
    for setup in setups if setups is not None else DEFAULT_SETUPS:
        scored[setup.label] = _scored(evaluation, model_class(**setup.fields()))
        _flush(module)
    return scored


def _scored(evaluation: Any, model: Any) -> dict[str, Any]:
    """`Evaluation.evaluate` is a coroutine in current Weave, and was a plain
    call in earlier ones. A scorer may not care which."""
    result = evaluation.evaluate(model)
    if inspect.isawaitable(result):
        return asyncio.run(_awaited(result))
    return result


async def _awaited(awaitable: Any) -> dict[str, Any]:
    return await awaitable


def _flush(module: Any) -> None:
    """Calls upload in the background and a command exits as soon as it prints.

    Pushing each configuration out before the next one starts means a dropped
    connection costs that run rather than every run behind it.
    """
    client = getattr(module, "get_client", lambda: None)()
    flush = getattr(client, "flush", None)
    if flush is not None:
        flush()


def _evaluation(module: Any, cases: list[Case], name: str):
    return module.Evaluation(
        name=name,
        description=(
            f"{len(cases)} labelled cases from the rule pack, scored by "
            f"{', '.join(sorted(SCORERS))}."
        ),
        dataset=module.Dataset(name=f"{name}-cases", rows=rows(cases)),
        scorers=[scorer(module, metric) for metric in SCORERS],
    )


@functools.lru_cache(maxsize=1)
def _model_class(module: Any):
    """Built on first use, because the base class comes from Weave.

    The fields are the configuration, so the Evals tab names what produced each
    column of numbers rather than leaving it to whoever reads them.
    """

    class ShopReview(module.Model):
        measurements: str = "pipeline"
        router: str = "local"
        run_fixes: bool = True
        fix_candidates: int = FIX_CANDIDATE_LIMIT
        preview_unverified: bool = False

        @module.op
        def predict(self, case: str) -> dict[str, Any]:
            return review(self, case)

    return ShopReview


def _weave() -> Any:
    try:
        import weave
    except ImportError as error:
        raise RuntimeError(
            "Weave evaluations need: "
            "python -m pip install -e 'packages/agents[observability]'"
        ) from error
    return weave


__all__ = [
    "DEFAULT_NAME", "evaluate_in_weave", "rows", "scorer",
]
