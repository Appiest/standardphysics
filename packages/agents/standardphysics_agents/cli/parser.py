"""The argument parser for every subcommand."""

from __future__ import annotations

import argparse

from ..evaluation.accessibility_sweep import DEFAULT_EVALUATIONS, DEFAULT_SEED
from ..evaluation.accessibility_sweep import DEFAULT_OUTPUT_PATH as DEFAULT_SWEEP_OUTPUT_PATH
from ..evolution import (
    DEFAULT_MEMORY_PATH,
    DEFAULT_PLAYBOOK_PATH,
    DEFAULT_RUN_PATH,
)
from .common import (
    PROVIDERS,
    ROUTERS,
)
from .evaluation import DEFAULT_EVALUATION_PATH, DEFAULT_GRID_PATH
from .simulate import DEFAULT_SIMULATION_PATH


def _add_rule_commands(parent) -> None:
    rules = parent.add_parser("rules", help="the thresholds and who has read them")
    sub = rules.add_subparsers(dest="rules_command", required=True)

    listing = sub.add_parser("list", help="every rule and its state")
    listing.add_argument("--tier", type=int, default=3)

    show = sub.add_parser("show", help="one rule, with the sentence behind it")
    show.add_argument("rule_id")

    verify = sub.add_parser("verify", help="turn a check on after reading its section")
    verify.add_argument("rule_id")
    verify.add_argument("--by", required=True, help="your name, for the report")
    verify.add_argument("--note", default=None)
    verify.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="the number you read, instead of being asked for it",
    )

    review = sub.add_parser("review", help="read every unverified rule in turn")
    review.add_argument("--by", required=True)
    review.add_argument("--tier", type=int, default=1)

    second = sub.add_parser("second-check", help="record a second reader")
    second.add_argument("rule_id")
    second.add_argument("--by", required=True)


def _add_precedent_commands(parent) -> None:
    precedents = parent.add_parser("precedents", help="ADA layout directives and their case references")
    sub = precedents.add_subparsers(dest="precedents_command", required=True)

    sub.add_parser("list", help="list all directives and verification status")

    show = sub.add_parser("show", help="show one directive in full")
    show.add_argument("directive_id")

    sub.add_parser("benchmark", help="run the precedent constraint benchmark")

    verify = sub.add_parser("verify", help="turn a directive on after reading its ADA sections")
    verify.add_argument("directive_id")
    verify.add_argument("--by", required=True, help="your name, for the ledger")

    sign = sub.add_parser("sign-case", help="mark a case reference as read against the opinion")
    sign.add_argument("directive_id")
    sign.add_argument("--by", required=True, help="your name, for the corpus")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="standardphysics-agents")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_rule_commands(commands)
    _add_precedent_commands(commands)

    check = commands.add_parser("check", help="run the checks on the fixture shop")
    check.add_argument("--provider", choices=PROVIDERS, default="pipeline")
    check.add_argument("--tier", type=int, default=1)

    evaluation = commands.add_parser(
        "evaluate", help="score the checks against the labelled dataset"
    )
    evaluation.add_argument("--provider", choices=PROVIDERS, default="pipeline")
    evaluation.add_argument("--router", choices=ROUTERS, default="local")
    evaluation.add_argument(
        "--version",
        default=None,
        help="the label Weave's Evals tab gives this run; defaults to the router's name",
    )
    evaluation.add_argument("--out", default=DEFAULT_EVALUATION_PATH)
    evaluation.add_argument(
        "--no-fixes", action="store_true", help="skip the rearrangement cases"
    )

    sweep = commands.add_parser(
        "accessibility-sweep",
        help="stress the route search over generated rooms without network calls",
    )
    sweep.add_argument("--evaluations", type=int, default=DEFAULT_EVALUATIONS)
    sweep.add_argument("--seed", type=int, default=DEFAULT_SEED)
    sweep.add_argument("--record-runs", type=int, default=0, help="save up to 100 actual runs for replay")
    sweep.add_argument("--out", default=str(DEFAULT_SWEEP_OUTPUT_PATH))

    simulation = commands.add_parser(
        "simulate",
        help="screen a measured room and customer route from JSON snapshots",
    )
    simulation.add_argument("--graph", required=True, help="path to a SceneGraph JSON snapshot")
    simulation.add_argument("--scenario", required=True, help="path to a Scenario JSON snapshot")
    simulation.add_argument("--lidar-mesh", default=None, help="optional path to a captured LiDAR mesh JSON snapshot")
    simulation.add_argument("--samples", type=int, default=1000)
    simulation.add_argument(
        "--workers",
        "--max-workers",
        dest="workers",
        type=int,
        default=4,
        help="maximum number of worker threads",
    )
    simulation.add_argument("--router", choices=ROUTERS, default="local")
    simulation.add_argument("--out", default=DEFAULT_SIMULATION_PATH)

    in_weave = commands.add_parser(
        "weave-eval",
        help="score every configuration in Weave, where the Evals tab compares them",
    )
    in_weave.add_argument(
        "--cases", type=int, default=None, help="only the first N cases, for a quick look"
    )
    in_weave.add_argument(
        "--preview-unverified",
        action="store_true",
        help="development only: score as if a person had verified every rule",
    )

    grid = commands.add_parser(
        "experiments",
        help="score the grid of configurations as W&B runs, for ARIA to read",
    )
    grid.add_argument("--out", default=DEFAULT_GRID_PATH)
    grid.add_argument(
        "--cases", type=int, default=None, help="only the first N cases, for a quick look"
    )
    grid.add_argument(
        "--dry-run", action="store_true", help="name the configurations and stop"
    )
    grid.add_argument(
        "--preview-unverified",
        action="store_true",
        help="development only: score as if a person had verified every rule",
    )

    evolution = commands.add_parser(
        "evolve",
        help="learn lessons for TypeSafe from its failures, keeping only those the scores back",
    )
    evolution.add_argument("--generations", type=int, default=3)
    evolution.add_argument(
        "--cases", type=int, default=None, help="only the first N cases, for a quick look"
    )
    evolution.add_argument("--call-limit", type=int, default=300, help="TypeSafe calls this run may make")
    evolution.add_argument("--playbook", default=str(DEFAULT_PLAYBOOK_PATH))
    evolution.add_argument("--memory", default=str(DEFAULT_MEMORY_PATH))
    evolution.add_argument("--out", default=str(DEFAULT_RUN_PATH))
    evolution.add_argument(
        "--fresh", action="store_true", help="start from an empty playbook instead of the saved one"
    )
    evolution.add_argument(
        "--preview-unverified",
        action="store_true",
        help="development only: score as if a person had verified every rule",
    )

    loop = commands.add_parser("loop", help="run the whole loop on the fixture shop")
    loop.add_argument("--provider", choices=PROVIDERS, default="pipeline")
    loop.add_argument("--router", choices=ROUTERS, default="typesafe")
    loop.add_argument("--tier", type=int, default=1)

    tally = commands.add_parser(
        "count",
        help="how many of something is on the surfaces a scan photographed",
    )
    tally.add_argument("thing", nargs="+", help="what to count, in your own words")
    tally.add_argument("--scan", required=True, help="a scan directory")
    tally.add_argument("--patch", type=float, default=0.6, help="patch size in metres")
    tally.add_argument("--readings", type=int, default=3, help="readings per patch")
    tally.add_argument("--workers", type=int, default=8, help="patches read at once")

    suite = commands.add_parser(
        "held-out",
        help="score the app on scanned rooms it was not developed against",
    )
    suite.add_argument("--seed", type=int, required=True, help="picks the split and the questions")
    suite.add_argument("--questions", type=int, default=80, help="questions per scene")
    suite.add_argument("--hold-out", type=int, default=2, help="scenes to score on")
    suite.add_argument("--root", default=".", help="the repository, where scans are found")
    suite.add_argument("--model", default=None, help="the model that writes and judges")
    suite.add_argument("--base-url", default=None, help="an OpenAI-shaped endpoint")
    suite.add_argument(
        "--api-key-env",
        default=None,
        help="the environment variable holding that endpoint's key",
    )
    suite.add_argument(
        "--workers", type=int, default=8, help="questions scored at once"
    )
    suite.add_argument(
        "--transcript", default=None, help="write every question and answer here, as JSON lines"
    )
    suite.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="seconds to wait for one batch of questions or one verdict",
    )

    question = commands.add_parser(
        "ask", help="ask the fixture shop a question about itself"
    )
    question.add_argument("question", nargs="+")
    question.add_argument("--provider", choices=PROVIDERS, default="pipeline")
    question.add_argument("--tier", type=int, default=1)

    screen = commands.add_parser(
        "screen", help="try many legal layouts on the fixture shop"
    )
    screen.add_argument("--provider", choices=PROVIDERS, default="pipeline")
    screen.add_argument("--samples", type=int, default=256)
    return parser
