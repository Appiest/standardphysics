"""Routes a parsed command line to the function that runs it."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from ..tracing import init as init_tracing
from .evaluation import evaluate_checks, run_experiments, score_in_weave, sweep_accessibility
from .evolve import evolve_playbook
from .parser import build_parser
from .precedent import benchmark_precedents, list_precedents, show_precedent, sign_precedent_case, verify_precedent
from .rules import list_rules, review_rules, second_check_rule, show_rule, verify_rule
from .shop import ask_shop, check_shop, count_on_surfaces, run_shop_loop, score_held_out, screen_shop
from .simulate import simulate_room

HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "rules.list": list_rules,
    "rules.show": show_rule,
    "rules.verify": verify_rule,
    "rules.review": review_rules,
    "rules.second-check": second_check_rule,
    "check": check_shop,
    "evaluate": evaluate_checks,
    "accessibility-sweep": sweep_accessibility,
    "simulate": simulate_room,
    "weave-eval": score_in_weave,
    "experiments": run_experiments,
    "evolve": evolve_playbook,
    "loop": run_shop_loop,
    "ask": ask_shop,
    "screen": screen_shop,
    "held-out": score_held_out,
    "count": count_on_surfaces,
    "precedents.list": list_precedents,
    "precedents.show": show_precedent,
    "precedents.benchmark": benchmark_precedents,
    "precedents.verify": verify_precedent,
    "precedents.sign-case": sign_precedent_case,
}
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    init_tracing()
    if args.command == "rules":
        key = f"{args.command}.{args.rules_command}"
    elif args.command == "precedents":
        key = f"{args.command}.{args.precedents_command}"
    else:
        key = args.command
    return HANDLERS[key](args)




