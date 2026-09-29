"""The evolve command: learn lessons for TypeSafe and keep only those the scores back."""

from __future__ import annotations

import sys
from pathlib import Path

from ..evaluation import dataset as labelled_cases
from ..evolution import (
    Evolution,
    Playbook,
    evolve,
    load_playbook,
    save_evolution,
    save_playbook,
)
from ..router import TypeSafeCallBudget, TypeSafeRouter
from ..rules import load_pack
from .common import (
    nothing_enabled,
)

TYPESAFE_NEEDED = (
    "Evolving the playbook needs TypeSafe. Set TYPESAFE_API_KEY and TYPESAFE_BASE_URL, then try again."
)


def evolve_playbook(args) -> int:
    """The outer loop: score TypeSafe, learn a lesson, keep it only if it helps."""
    from ..evaluation.configuration import ledger as configured_ledger
    from ..evaluation.configuration import measurements as shared_measurements

    pack, ledger = load_pack(), configured_ledger(args.preview_unverified)
    if not args.preview_unverified and nothing_enabled(pack, ledger):
        return 1
    if not TypeSafeRouter().configured:
        print(TYPESAFE_NEEDED, file=sys.stderr)
        return 1
    budget = TypeSafeCallBudget(args.call_limit)
    playbook_path = Path(args.playbook)
    cases = labelled_cases()[: args.cases] if args.cases else labelled_cases()
    evolution = evolve(
        cases=cases,
        generations=args.generations,
        router_factory=lambda playbook: TypeSafeRouter(playbook=playbook, budget=budget),
        measure=shared_measurements("pipeline"),
        rules=pack,
        ledger=ledger,
        playbook=Playbook() if args.fresh else load_playbook(playbook_path),
        memory_path=Path(args.memory),
    )
    _print_evolution(evolution)
    print(f"\nplaybook v{evolution.playbook.version}: {save_playbook(evolution.playbook, playbook_path)}")
    print(f"generations: {save_evolution(evolution, Path(args.out))}")
    print(f"TypeSafe calls: {budget.used} of {budget.limit}")
    return 0


def _print_evolution(evolution: Evolution) -> None:
    print(f"baseline: {_control_reading(evolution.baseline)}")
    for generation in evolution.generations:
        verdict = "kept" if generation.kept else "refused"
        print(f"\ngeneration {generation.number}: {len(generation.failures)} failing cases")
        if generation.lesson is not None:
            print(f"  lesson ({generation.lesson.source}): {generation.lesson.text}")
        print(f"  {verdict}: {', '.join(generation.reasons) or 'the action scores rose and nothing fell'}")
        if generation.before:
            print(f"  on {len(generation.trial_cases)} trial cases: "
                  f"{_control_reading(generation.before)} -> {_control_reading(generation.after)}")
    if evolution.final is not None:
        print(f"\nevery case, final playbook: {_control_reading(evolution.final)}")


def _control_reading(scores: dict) -> str:
    names = ("router_action_match", "trajectory_ok")
    return ", ".join(f"{name} {scores[name]:.2f}" for name in names if name in scores)
