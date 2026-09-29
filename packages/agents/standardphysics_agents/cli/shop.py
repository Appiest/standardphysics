"""Commands that run on the fixture shop or a scanned room."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..ask import ask as ask_question
from ..ask import resolver
from ..assess import assess
from ..evaluation.scorers import loop_trajectory_ok
from ..loop import run_loop
from ..rules import load_ledger, load_pack
from ..simulate import screen_layouts
from .common import (
    count_noun,
    fixture_shop,
    measurements,
    nothing_enabled,
    router_for,
)


def check_shop(args) -> int:
    graph, scenario = fixture_shop()
    result = assess(graph, scenario, measurements(args.provider), max_tier=args.tier)
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


def ask_shop(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    graph, scenario = fixture_shop()
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
        measurements(args.provider),
        rules=pack,
        ledger=ledger,
        max_tier=args.tier,
        with_resolver=picked,
    )
    print(answer.text)
    if answer.kind:
        looking = (
            f" look at {count_noun(len(answer.locus.node_ids), 'piece')}"
            if answer.locus
            else ""
        )
        print(f"  [{answer.kind}]{looking}")
    return 0 if answer.understood else 1


def screen_shop(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    if nothing_enabled(pack, ledger):
        return 1
    graph, scenario = fixture_shop()
    measured = assess(graph, scenario, measurements(args.provider), rules=pack, ledger=ledger)
    report = screen_layouts(
        graph,
        scenario,
        measurements(args.provider),
        measured.findings,
        rules=pack,
        ledger=ledger,
        samples=args.samples,
    )
    print(f"tried {report.samples}, measured {report.measured}")
    if report.best:
        print(f"found {report.best.candidate.strategy}")
        return 0
    print("no arrangement in this screen")
    return 1


def run_shop_loop(args) -> int:
    pack, ledger = load_pack(), load_ledger()
    if nothing_enabled(pack, ledger):
        return 1
    graph, scenario = fixture_shop()
    steps = run_loop(
        graph,
        scenario,
        measurements(args.provider),
        router_for(args.router),
        rules=pack,
        ledger=ledger,
        max_tier=args.tier,
    )
    for step in steps:
        action = step.action or f"nothing authorized ({step.rejected})"
        print(f"pass {step.pass_number}: {action}")
        print(f"  {count_noun(len(step.assessment.problems), 'problem')}, "
              f"{count_noun(len(step.assessment.questions), 'question')}")
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


def count_on_surfaces(args) -> int:
    """How many of something is on the surfaces of a scanned room."""
    from standardphysics_pipeline import surfaces

    thing = " ".join(args.thing)
    try:
        scan = surfaces.open_scan(Path(args.scan))
        read = surfaces.counter()
    except (surfaces.ScanNotReadable, surfaces.NoCounterConfigured) as refusal:
        print(refusal)
        return 1
    mesh = "with lidar" if scan.cloud is not None else "no lidar, nothing can be tested for occlusion"
    print(f"{scan.frame_count} frames, {len(scan.graph.nodes)} regions, {mesh}", file=sys.stderr)
    result = surfaces.tally(
        scan.graph, scan.cameras, scan.frames, thing, read,
        size=args.patch, readings=args.readings, workers=args.workers, cloud=scan.cloud,
    )
    print(surfaces.report(result))
    return 0


def _suite_model(args):
    """Whichever endpoint is asked for, defaulting to the one models.py knows.

    The writer and the judge are the measuring instrument rather than the app, so
    which model runs them is a separate decision from the provider policy the app
    itself follows.
    """
    from ..models import OpenRouter

    if not (args.model or args.base_url or args.api_key_env or args.timeout):
        return None
    return OpenRouter(
        api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
        model=args.model,
        base_url=args.base_url,
        timeout=args.timeout,
    )


def score_held_out(args) -> int:
    """Score the app on rooms it was not developed against."""
    from ..evaluation import held_out

    try:
        result = held_out.run(
            Path(args.root),
            seed=args.seed,
            per_scene=args.questions,
            held_out=args.hold_out,
            model=_suite_model(args),
            workers=args.workers,
            watch=lambda line: print(line, file=sys.stderr, flush=True),
        )
    except (held_out.NoRealScenes, held_out.CouldNotWriteQuestions, held_out.CouldNotJudge) as refusal:
        print(refusal)
        return 1
    print(held_out.report(result))
    if args.transcript:
        Path(args.transcript).write_text(
            "\n".join(json.dumps(row) for row in held_out.transcript(result)) + "\n"
        )
        print(f"every question written to {args.transcript}")
    return 0
