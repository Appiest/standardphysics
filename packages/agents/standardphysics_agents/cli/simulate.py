"""The simulate command: screen a measured room from JSON snapshots."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel
from standardphysics_contracts import LidarMesh, Scenario, SceneGraph

from ..router import LocalPolicyRouter
from ..rules import load_ledger, load_pack
from ..simulation_report import simulation_result
from ..workflows import (
    DEFAULT_PROFILES,
    TypeSafeWorkflowConfigurationError,
    build_workflow_suite,
    run_typesafe_workflow_batch,
    run_workflow_batch,
)

DEFAULT_SIMULATION_PATH = "runs/simulation.json"


ModelT = TypeVar("ModelT", bound=BaseModel)


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


def simulate_room(args) -> int:
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
