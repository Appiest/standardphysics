"""The command line a person uses to enable a check.

Verification is the one thing in this lane an agent must not do alone, so it
gets a real interface rather than a JSON file to hand-edit. `verify` prints the
sentence from the standard and then asks for the number back. Typing it is the
confirmation: it cannot be satisfied by someone who did not read the section.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel
from standardphysics_contracts import (
    LidarMesh,
    Scenario,
    SceneGraph,
)

from .ask import ask as ask_question
from .ask import resolver
from .assess import assess
from .evaluation import (
    DEFAULT_GRID,
    DEFAULT_SETUPS,
    blocker,
    evaluate,
    evaluate_in_weave,
    log_experiments,
    previewing,
    run_accessibility_sweep,
    run_grid,
    save,
    save_accessibility_sweep,
    save_experiments,
    target,
)
from .evaluation import dataset as labelled_cases
from .evaluation.accessibility_sweep import (
    DEFAULT_EVALUATIONS,
    DEFAULT_SEED,
)
from .evaluation.accessibility_sweep import (
    DEFAULT_OUTPUT_PATH as DEFAULT_SWEEP_OUTPUT_PATH,
)
from .evaluation.scorers import LOWER_IS_BETTER, SCORERS, loop_trajectory_ok
from .evolution import (
    DEFAULT_MEMORY_PATH,
    DEFAULT_PLAYBOOK_PATH,
    DEFAULT_RUN_PATH,
    Evolution,
    Playbook,
    evolve,
    load_playbook,
    save_evolution,
    save_playbook,
)
from .loop import run_loop
from .router import LocalPolicyRouter, TypeSafeCallBudget, TypeSafeRouter
from .rules import RuleSpec, load_ledger, load_pack, save_ledger
from .simulation_report import simulation_result
from .tracing import init as init_tracing
from .tracing import is_live, project_url
from .workflows import (
    DEFAULT_PROFILES,
    TypeSafeWorkflowConfigurationError,
    build_workflow_suite,
    run_typesafe_workflow_batch,
    run_workflow_batch,
)

READ_BACK_TOLERANCE = 1e-9

PROVIDERS = ("stub", "pipeline")

ROUTERS = ("typesafe", "local")

DEFAULT_EVALUATION_PATH = "runs/evaluation.json"
DEFAULT_SIMULATION_PATH = "runs/simulation.json"


ModelT = TypeVar("ModelT", bound=BaseModel)

DEFAULT_GRID_PATH = "runs/experiments.json"


def _measurements(name: str):
    if name == "pipeline":
        from standardphysics_pipeline import PipelineMeasurements

        return PipelineMeasurements()
    from standardphysics_fixtures import FixtureMeasurements

    return FixtureMeasurements()


def _fixture_shop():
    from standardphysics_fixtures import build_graph, build_scenario

    return build_graph(), build_scenario()


def _state(rule: RuleSpec, ledger) -> str:
    entry = ledger.entry_for(rule)
    if entry is None:
        return "waiting on a person"
    if entry.second_check_by:
        return f"verified by {entry.verified_by}, checked by {entry.second_check_by}"
    return f"verified by {entry.verified_by}, wants a second reader"


def _list(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    for rule in pack.within_tier(args.tier):
        print(
            f"{rule.id:28} tier {rule.tier}  "
            f"{rule.threshold:>6g} {rule.unit:<4} {rule.comparison:<9} "
            f"{rule.citation.display():<48} {_state(rule, ledger)}"
        )
    return 0


def _show(args) -> int:
    _print_rule(load_pack().by_id(args.rule_id), load_ledger())
    return 0


def _print_rule(rule: RuleSpec, ledger) -> None:
    print(f"{rule.id}\n")
    print(f"  {rule.title}")
    print(f"  {rule.citation.authority} {rule.citation.display()}")
    print(f"  {rule.citation.url or ''}")
    print(f"  threshold: {rule.threshold:g} {rule.unit} ({rule.comparison})")
    for name, value in sorted(rule.parameters.items()):
        print(f"  {name}: {value:g}")
    print(f"  evidence: {rule.evidence}")
    print(f"  state: {_state(rule, ledger)}\n")
    print(f"  {rule.source_text}\n")
    if rule.review_note:
        print(f"  review note: {rule.review_note}\n")


def _read_back(rule: RuleSpec, given: float | None) -> bool:
    if given is not None:
        return math.isclose(given, rule.threshold, abs_tol=READ_BACK_TOLERANCE)
    typed = input(f"Type the number you read in {rule.citation.section}: ").strip()
    return _matches(typed, rule)


def _verify(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    rule = pack.by_id(args.rule_id)
    _print_rule(rule, ledger)
    if not _read_back(rule, args.threshold):
        print(
            f"That is not the threshold in the pack. {rule.id} stays off.",
            file=sys.stderr,
        )
        return 1
    save_ledger(ledger.record(rule, verified_by=args.by, note=args.note))
    print(f"{rule.id} is on. {rule.threshold:g} {rule.unit}, read by {args.by}.")
    return 0


def _review(args) -> int:
    """Walk every rule nobody has read yet, one section at a time."""
    pack = load_pack()
    waiting = [r for r in pack.within_tier(args.tier) if not load_ledger().verifies(r)]
    if not waiting:
        print(f"Every tier {args.tier} rule has a reader.")
        return 0

    print(f"{len(waiting)} rules to read. Enter a blank line to stop.\n")
    for rule in waiting:
        if _review_one(rule, args.by) is False:
            break
    return 0


def _review_one(rule: RuleSpec, reviewer: str) -> bool:
    _print_rule(rule, load_ledger())
    typed = input(
        f"Number you read in {rule.citation.section} (blank to stop): "
    ).strip()
    if not typed:
        return False
    if not _matches(typed, rule):
        print(f"  That is not what the pack says. {rule.id} stays off.\n")
        return True
    save_ledger(load_ledger().record(rule, verified_by=reviewer))
    print(f"  {rule.id} is on.\n")
    return True


def _matches(typed: str, rule: RuleSpec) -> bool:
    try:
        return math.isclose(float(typed), rule.threshold, abs_tol=READ_BACK_TOLERANCE)
    except ValueError:
        return False


def _second_check(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    rule = pack.by_id(args.rule_id)
    save_ledger(ledger.second_check(rule, checked_by=args.by))
    print(f"{rule.id} has two readers.")
    return 0


def _check(args) -> int:
    graph, scenario = _fixture_shop()
    result = assess(graph, scenario, _measurements(args.provider), max_tier=args.tier)
    if not result.findings:
        print("No checks are enabled. Run: rules verify <id> --by \"<name>\"")
    for finding in result.findings:
        mark = {"problem": "!", "question": "?", "passes": " "}[finding.outcome]
        print(f"{mark} {finding.title}")
        print(f"  {finding.detail}")
        if finding.fix:
            print(f"  {finding.fix}")
        print(f"  {finding.citation.authority} {finding.citation.display()}")
    for gap in result.unevaluated:
        print(f"- {gap.rule_id} is held, waiting on {gap.waiting_on}")
    return 0


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _router(name: str):
    """TypeSafe when it is configured, and a labelled local policy when not."""
    if name == "local":
        return LocalPolicyRouter()
    router = TypeSafeRouter()
    if router.configured:
        return router
    print(
        "TYPESAFE_API_KEY and TYPESAFE_BASE_URL are not set. "
        "Running the local policy, labelled as one.",
        file=sys.stderr,
    )
    return LocalPolicyRouter()


NOTHING_ENABLED = (
    'No checks are enabled. Run: rules review --by "<name>"'
)

WEAVE_NOT_CONFIGURED = (
    "Weave is not configured. Set WANDB_PROJECT and WANDB_ENTITY, then try again."
)

GRID_STAYED_LOCAL = "The grid is on disk. To put it where ARIA reads it: "



def _nothing_enabled(pack, ledger) -> bool:
    if pack.enabled(ledger, max_tier=1):
        return False
    print(NOTHING_ENABLED, file=sys.stderr)
    return True


def _evaluate(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    if _nothing_enabled(pack, ledger):
        return 1
    result = evaluate(
        measure=_measurements(args.provider),
        rules=pack,
        ledger=ledger,
        router=_router(args.router),
        run_fixes=not args.no_fixes,
        version=args.version,
    )
    print(f"rule pack {result.rulepack_version}, {len(result.outcomes)} cases")
    print(f"completed: {result.completed}")
    for name, value in sorted(result.scores.items()):
        direction = "lower is better" if name in LOWER_IS_BETTER else ""
        print(f"  {name:24} {value:.4f}  {direction}")
    for case_id in result.failures:
        print(f"  failed: {case_id}", file=sys.stderr)
    written = save(result, Path(args.out))
    print(f"per-case results: {written}")
    if result.weave_url:
        print(f"traces: {result.weave_url}")
    if result.evaluation_url:
        print(f"evaluation in weave: {result.evaluation_url}")
    return 0 if result.completed else 1


def _accessibility_sweep(args) -> int:
    result = run_accessibility_sweep(
        evaluations=args.evaluations,
        seed=args.seed,
        record_runs=args.record_runs,
    )
    written = save_accessibility_sweep(result, Path(args.out))
    print(
        f"{result.evaluations} evaluations across "
        f"{result.layouts} layouts and {result.routes} routes"
    )
    print(f"failures: {result.failures}")
    for profile_id, count in result.profile_route_fits.items():
        total = result.profile_evaluations[profile_id]
        print(f"  {profile_id:16} {count}/{total} routes fit")
    print(f"seed: {result.seed}")
    print(f"digest: {result.digest}")
    print(f"result: {written}")
    return 0 if result.passed else 1


def _load_json_model(path: Path, model: type[ModelT], label: str) -> ModelT | None:
    """Read one of the measured JSON snapshots used by ``simulate``."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        print(f"could not read {label} {path}: {exc}", file=sys.stderr)
        return None
    try:
        return model.model_validate_json(payload)
    except (TypeError, ValueError) as exc:
        print(f"could not parse {label} {path}: {exc}", file=sys.stderr)
        return None



def _simulate(args) -> int:
    graph = _load_json_model(Path(args.graph), SceneGraph, "graph")
    scenario = _load_json_model(Path(args.scenario), Scenario, "scenario")
    if graph is None or scenario is None:
        return 2

    mesh = None
    if args.lidar_mesh:
        mesh = _load_json_model(Path(args.lidar_mesh), LidarMesh, "LiDAR mesh")
        if mesh is None:
            return 2

    rules, ledger = load_pack(), load_ledger()
    workflows = build_workflow_suite(graph, scenario)
    from standardphysics_pipeline import PipelineMeasurements

    measure_factory = PipelineMeasurements
    try:
        if args.router == "typesafe":
            batch = run_typesafe_workflow_batch(
                graph,
                workflows=workflows,
                profiles=list(DEFAULT_PROFILES),
                samples=args.samples,
                max_workers=args.workers,
                measure_factory=measure_factory,
                rules=rules,
                ledger=ledger,
                lidar_mesh=mesh,
                max_tier=3,
            )
        else:
            batch = run_workflow_batch(
                graph,
                workflows=workflows,
                profiles=list(DEFAULT_PROFILES),
                samples=args.samples,
                max_workers=args.workers,
                measure_factory=measure_factory,
                router_factory=LocalPolicyRouter,
                rules=rules,
                ledger=ledger,
                lidar_mesh=mesh,
                max_tier=3,
            )
    except TypeSafeWorkflowConfigurationError as exc:
        print(f"simulation could not start: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"simulation configuration is invalid: {exc}", file=sys.stderr)
        return 2

    result = simulation_result(batch, rules, ledger, mesh)
    written = Path(args.out)
    try:
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"could not write simulation result {written}: {exc}", file=sys.stderr)
        return 2

    print(
        f"{result.total_runs} trials across {len(workflows)} workflows and "
        f"{len(DEFAULT_PROFILES)} profiles"
    )
    print(f"completed: {result.completed_runs}; rejected: {result.rejected_runs}")
    print(f"result: {written}")
    if (
        args.router == "typesafe"
        and result.total_runs > 0
        and result.rejected_runs == result.total_runs
    ):
        return 1
    return 0


def _weave_eval(args) -> int:
    """Every configuration against every case, left in Weave's Evals tab."""
    if not is_live():
        print(WEAVE_NOT_CONFIGURED, file=sys.stderr)
        return 1
    if not args.preview_unverified and _nothing_enabled(load_pack(), load_ledger()):
        return 1
    setups = previewing(DEFAULT_SETUPS) if args.preview_unverified else DEFAULT_SETUPS
    cases = labelled_cases()[: args.cases] if args.cases else None
    for label, result in evaluate_in_weave(setups, cases=cases).items():
        print(f"\n{label}")
        _print_weave_scores(result)
    print(f"\nevals: {project_url()}")
    return 0


def _print_weave_scores(result: dict) -> None:
    for name in sorted(SCORERS):
        mean = _mean_of(result.get(name))
        direction = "lower is better" if name in LOWER_IS_BETTER else ""
        reading = f"{mean:.4f}" if mean is not None else "nothing to score"
        print(f"  {name:24} {reading:>16}  {direction}")


def _mean_of(scored) -> float | None:
    """Weave summarizes a numeric scorer as {"mean": value}."""
    if isinstance(scored, dict):
        return scored.get("mean")
    return scored if isinstance(scored, (int, float)) else None


def _experiments(args) -> int:
    """The grid as W&B runs, which is the form ARIA reads."""
    if not args.preview_unverified and _nothing_enabled(load_pack(), load_ledger()):
        return 1
    setups = previewing(DEFAULT_GRID) if args.preview_unverified else list(DEFAULT_GRID)
    if args.dry_run:
        for configuration in setups:
            print(f"  {configuration.label}")
        return 0
    cases = labelled_cases()[: args.cases] if args.cases else None
    experiments = run_grid(setups, cases=cases)
    _print_grid(experiments)
    print(f"\nthe grid: {save_experiments(experiments, Path(args.out))}")
    _print_runs(experiments)
    return 0 if all(e.result.completed for e in experiments) else 1


GRID_HEADER = (
    f"{'configuration':34} {'weakest score':28} {'error in':>9} "
    f"{'measurements':>13} {'seconds':>8}"
)


def _print_grid(experiments) -> None:
    print(f"\n{GRID_HEADER}")
    for experiment in experiments:
        metrics = experiment.metrics()
        print(
            f"{experiment.setup.label:34} {_weakest(metrics):28} "
            f"{_reading(metrics.get('measurement_error_in')):>9} "
            f"{metrics['cost/measurements_taken']:>13} "
            f"{metrics['cost/wall_seconds']:>8.1f}"
        )


def _weakest(metrics: dict) -> str:
    """The lowest of the scores where higher is better, and which one it is."""
    scored = {
        name: value
        for name, value in metrics.items()
        if name in SCORERS and name not in LOWER_IS_BETTER
    }
    if not scored:
        return "nothing to score"
    name = min(scored, key=lambda key: scored[key])
    return f"{scored[name]:.4f} {name}"


def _reading(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "-"


def _print_runs(experiments) -> None:
    reason = blocker()
    if reason is not None:
        print(f"{GRID_STAYED_LOCAL}{reason}", file=sys.stderr)
        return
    project, team = target()
    print(f"\n{len(experiments)} runs in {team or 'your default entity'}/{project}")
    for url in log_experiments(experiments):
        print(f"  {url}")


TYPESAFE_NEEDED = (
    "Evolving the playbook needs TypeSafe. Set TYPESAFE_API_KEY and TYPESAFE_BASE_URL, then try again."
)


def _evolve(args) -> int:
    """The outer loop: score TypeSafe, learn a lesson, keep it only if it helps."""
    from .evaluation.configuration import ledger as configured_ledger
    from .evaluation.configuration import measurements as shared_measurements

    pack, ledger = load_pack(), configured_ledger(args.preview_unverified)
    if not args.preview_unverified and _nothing_enabled(pack, ledger):
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


def _ask(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    graph, scenario = _fixture_shop()
    picked = resolver()
    if picked.provider == "local_keywords":
        print(
            "OPENROUTER_API_KEY is not set. Matching keywords instead, "
            "labelled as such.",
            file=sys.stderr,
        )
    answer = ask_question(
        " ".join(args.question),
        graph,
        scenario,
        _measurements(args.provider),
        rules=pack,
        ledger=ledger,
        max_tier=args.tier,
        with_resolver=picked,
    )
    print(answer.text)
    if answer.kind:
        looking = (
            f" look at {_count(len(answer.locus.node_ids), 'piece')}"
            if answer.locus
            else ""
        )
        print(f"  [{answer.kind}]{looking}")
    return 0 if answer.understood else 1


def _loop(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    if _nothing_enabled(pack, ledger):
        return 1
    graph, scenario = _fixture_shop()
    steps = run_loop(
        graph,
        scenario,
        _measurements(args.provider),
        _router(args.router),
        rules=pack,
        ledger=ledger,
        max_tier=args.tier,
    )
    for step in steps:
        action = step.action or f"nothing authorized ({step.rejected})"
        print(f"pass {step.pass_number}: {action}")
        print(f"  {_count(len(step.assessment.problems), 'problem')}, "
              f"{_count(len(step.assessment.questions), 'question')}")
        if step.message:
            print(f"  {step.message}")
        gate = step.result.gate
        if gate:
            print(f"  gate: {'accepted' if gate.accepted else 'rejected'}, "
                  f"{gate.shortfall_before:.1f} in short -> {gate.shortfall_after:.1f}")
    print(f"trajectory: {TRAJECTORY_READINGS[loop_trajectory_ok(steps, pack)]}")
    return 0


TRAJECTORY_READINGS = {
    1.0: "every pass after the kept rearrangement did something new",
    0.0: "a pass repeated work or aimed a fix at something furniture cannot change",
    None: "no rearrangement was kept, so there is no hand-off to judge",
}


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "rules.list": _list,
    "rules.show": _show,
    "rules.verify": _verify,
    "rules.review": _review,
    "rules.second-check": _second_check,
    "check": _check,
    "evaluate": _evaluate,
    "accessibility-sweep": _accessibility_sweep,
    "simulate": _simulate,
    "weave-eval": _weave_eval,
    "experiments": _experiments,
    "evolve": _evolve,
    "loop": _loop,
    "ask": _ask,
}


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="standardphysics-agents")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_rule_commands(commands)

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

    question = commands.add_parser(
        "ask", help="ask the fixture shop a question about itself"
    )
    question.add_argument("question", nargs="+")
    question.add_argument("--provider", choices=PROVIDERS, default="pipeline")
    question.add_argument("--tier", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    init_tracing()
    key = (
        f"{args.command}.{args.rules_command}"
        if args.command == "rules"
        else args.command
    )
    return HANDLERS[key](args)


if __name__ == "__main__":
    raise SystemExit(main())
