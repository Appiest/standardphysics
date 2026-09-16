"""Millions of distinct task queries on one immutable 3D scan, with replay paths.

Connectivity is built once per body profile, then reused for different
start/approach/target combinations. Counts describe unique scenario queries,
not millions of model calls or millions of repeated BFS computations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from standardphysics_contracts import LidarMesh, SceneGraph, graph_hash
from standardphysics_pipeline.footprints import rotation_about_z

from .scan_space import FLOOR_NOISE_BAND, ScanSpace, build_spaces
from .scan_tasks import PROP_SIZES, ScanTask, TaskSuite, choose_task, propose_tasks, validate_tasks

OUTCOMES = ("route_blocked", "out_of_reach", "route_and_reach_fit")


@dataclass
class TaskDomain:
    space: ScanSpace
    task: ScanTask
    goals: np.ndarray
    targets: np.ndarray

    @property
    def size(self) -> int:
        return len(self.space.starts)*len(self.goals)*len(self.targets)


def task_domain(graph: SceneGraph, space: ScanSpace, task: ScanTask) -> TaskDomain:
    node = graph.by_id(task.target_node_id)
    position = node.transform.position
    cos_t, sin_t = rotation_about_z(node)
    cells = np.argwhere(~space.grid.occupied)
    x = space.grid.origin_x+(cells[:, 1]+0.5)*space.grid.cell_size-position.x
    y = space.grid.origin_y+(cells[:, 0]+0.5)*space.grid.cell_size-position.y
    lx, ly = x*cos_t+y*sin_t, -x*sin_t+y*cos_t
    dx = np.maximum(0, np.abs(lx)-node.dimensions.x/2)
    dy = np.maximum(0, np.abs(ly)-node.dimensions.y/2)
    distance = np.hypot(dx, dy)
    goals = cells[(distance >= 0.05) & (distance <= 0.90)]
    if not len(goals):
        raise ValueError(f"no observed-floor approach candidates for task {task.id}")
    targets = []
    prop_x, prop_y = PROP_SIZES[task.prop]
    half_x, half_y = (node.dimensions.x-prop_x)/2, (node.dimensions.y-prop_y)/2
    if half_x <= 0 or half_y <= 0:
        raise ValueError(f"hypothetical prop cannot fit on task {task.id}'s table")
    for u in (-1, -0.5, 0, 0.5, 1):
        for v in (-1, -0.5, 0, 0.5, 1):
            a, b = u*half_x, v*half_y
            targets.append((position.x+a*cos_t-b*sin_t,
                            position.y+a*sin_t+b*cos_t,
                            position.z+node.dimensions.z/2+0.025))
    return TaskDomain(space, task, goals, np.asarray(targets))


def unique_indices(size: int, count: int, rng: np.random.Generator) -> np.ndarray:
    if not 0 <= count <= size:
        raise ValueError("requested count exceeds distinct scenario space")
    multiplier = int(rng.integers(1, size)) if size > 1 else 1
    while math.gcd(multiplier, size) != 1:
        multiplier = multiplier+1 if multiplier+1 < size else 1
    offset = int(rng.integers(0, size))
    return (np.arange(count, dtype=np.int64)*multiplier+offset) % size


def evaluate_domain(domain: TaskDomain, indices: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    n_targets, n_goals = len(domain.targets), len(domain.goals)
    targets = domain.targets[indices % n_targets]
    goals = domain.goals[(indices // n_targets) % n_goals]
    starts = domain.space.starts[indices // (n_targets*n_goals)]
    components = domain.space.components
    destination_labels = components[goals[:, 0], goals[:, 1]]
    routes = (destination_labels != 0) & (destination_labels == components[starts[:, 0], starts[:, 1]])
    grid, profile = domain.space.grid, domain.space.profile
    shoulder = np.column_stack((grid.origin_x+(goals[:, 1]+0.5)*grid.cell_size,
                                grid.origin_y+(goals[:, 0]+0.5)*grid.cell_size,
                                np.full(len(goals), profile.shoulder_height)))
    reach_distance = np.linalg.norm(targets-shoulder, axis=1)
    reachable = reach_distance <= profile.arm_length
    outcomes = np.where(~routes, 0, np.where(~reachable, 1, 2))
    return outcomes, {"starts": starts, "goals": goals, "targets": targets,
                       "reach_distance": reach_distance}


def bfs_path(mask: np.ndarray, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
    if not mask[start] or not mask[goal]:
        return []
    rows, cols = mask.shape
    queue = deque([start])
    parents = {start: start}
    while queue:
        cell = queue.popleft()
        if cell == goal:
            path = [goal]
            while path[-1] != start:
                path.append(parents[path[-1]])
            return path[::-1]
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            other = cell[0]+dr, cell[1]+dc
            if 0 <= other[0] < rows and 0 <= other[1] < cols and mask[other] and other not in parents:
                parents[other] = cell
                queue.append(other)
    return []


def make_replay(domain: TaskDomain, case_index: int, evaluation: int) -> dict:
    outcomes, values = evaluate_domain(domain, np.asarray([case_index]))
    start = int(values["starts"][0, 0]), int(values["starts"][0, 1])
    goal = int(values["goals"][0, 0]), int(values["goals"][0, 1])
    outcome = int(outcomes[0])
    # Independent BFS checks the component-query answer on every filmed case.
    fit_path = bfs_path(domain.space.traversable, start, goal)
    if bool(fit_path) != (outcome != 0):
        raise AssertionError("recorded route disagrees with independent BFS")
    planned = fit_path or bfs_path(~domain.space.grid.occupied, start, goal)
    path = []
    for cell in planned:
        if not domain.space.traversable[cell]:
            break
        path.append(cell)
    path = path or [start]
    grid = domain.space.grid
    return {
        "task": domain.task.model_dump(mode="json"),
        "profile": asdict(domain.space.profile), "case_index": case_index,
        "evaluation": evaluation, "outcome": OUTCOMES[outcome],
        "start_cell": start, "goal_cell": goal,
        "path": [grid.to_world(*cell).model_dump() for cell in path],
        "target": values["targets"][0].tolist(),
        "prop_size": PROP_SIZES[domain.task.prop],
        "reach_distance": float(values["reach_distance"][0]),
        "bottleneck_meters": min(float(domain.space.clearance[cell])*2 for cell in path),
        "independent_bfs_agrees": True,
        "scope": "route and shoulder-to-target reach envelope; hand motion is illustrative",
    }


def _replay_choice(outcomes: np.ndarray, reach_distances: np.ndarray, task_number: int) -> int:
    preference = (1, 2, 0) if task_number % 3 == 2 else (2, 1, 0)
    for outcome in preference:
        candidates = np.flatnonzero(outcomes == outcome)
        if len(candidates):
            if outcome == 1:
                return int(candidates[np.argmin(reach_distances[candidates])])
            return int(candidates[len(candidates)//2])
    raise ValueError("no evaluated candidates to replay")


def _save(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(result, indent=2)+"\n")
    staged.replace(path)


def run_campaign(graph: SceneGraph, mesh: LidarMesh, suite: TaskSuite,
                 *, evaluations: int, seed: int, output: Path,
                 planner: Callable = choose_task) -> dict:
    if isinstance(evaluations, bool) or not isinstance(evaluations, int) or evaluations < 40:
        raise ValueError("evaluations must be an integer of at least 40")
    validate_tasks(suite, graph)
    started = time.perf_counter()
    immutable_hash = graph_hash(graph)
    spaces = build_spaces(graph, mesh)
    rng = np.random.default_rng(seed)
    digest = hashlib.sha256()
    result = {
        "scan_id": str(graph.scan_id), "revision": graph.revision, "graph_hash": immutable_hash,
        "seed": seed, "requested_evaluations": evaluations, "evaluations": 0,
        "unique_layouts": 1, "connectivity_builds": len(spaces),
        "mesh_triangles": sum(len(part.triangles)//3 for part in mesh.parts),
        "profiles": [asdict(space.profile) for space in spaces],
        "outcomes": dict.fromkeys(OUTCOMES, 0), "batches": [], "recorded_runs": [],
        "complete": False, "model_calls": 0,
        "geometry": {"cell_size_meters": spaces[0].grid.cell_size,
                     "floor_noise_band_meters": FLOOR_NOISE_BAND,
                     "collision_model": "height-aware circular body envelope against RoomPlan solids and LiDAR triangles",
                     "floor": "convex hull of captured RoomPlan floor; raw LiDAR supplies obstacles"},
        "limitations": ["Hypothetical props on actual measured tables; not observed medicine or computers.",
                        "Reach uses a shoulder-centered sphere; grasp, strength, dexterity and keyboard operation are untested.",
                        "Circular body approximation does not model wheelchair steering, feet, knees or articulated limb collisions.",
                        "Unknown or missing scan geometry and floor noise can affect results; no legal compliance determination."],
    }
    remaining = list(suite.tasks)
    for batch_index in range(len(suite.tasks)):
        task, decision = planner(remaining, [batch["summary"] for batch in result["batches"]])
        if task.id not in {item.id for item in remaining}:
            raise ValueError("planner selected a task outside the remaining allowlist")
        remaining = [item for item in remaining if item.id != task.id]
        batch = {"task_id": task.id, "title": task.title, "decision": decision,
                 "profiles": [], "summary": {"title": task.title, **dict.fromkeys(OUTCOMES, 0)}}
        for profile_index, space in enumerate(spaces):
            slot = batch_index*len(spaces)+profile_index
            base, remainder = divmod(evaluations, len(suite.tasks)*len(spaces))
            count = base+int(slot < remainder)
            domain = task_domain(graph, space, task)
            indices = unique_indices(domain.size, count, rng)
            outcomes, values = evaluate_domain(domain, indices)
            counts = {label: int(np.sum(outcomes == code)) for code, label in enumerate(OUTCOMES)}
            if profile_index == 0:
                chosen = _replay_choice(outcomes, values["reach_distance"], batch_index)
                result["recorded_runs"].append(make_replay(domain, int(indices[chosen]), result["evaluations"]+chosen+1))
            for label, value in counts.items():
                result["outcomes"][label] += value
                batch["summary"][label] += value
            digest.update(task.id.encode()+space.profile.id.encode()+indices.tobytes()+outcomes.tobytes())
            batch["profiles"].append({"id": space.profile.id, "evaluations": count,
                                      "distinct_case_space": domain.size, "outcomes": counts})
            result["evaluations"] += count
        result["batches"].append(batch)
        result["model_calls"] += decision["calls"]
        result["digest"] = digest.hexdigest()
        result["elapsed_seconds"] = time.perf_counter()-started
        _save(output, result)
        print(f"{result['evaluations']:,}/{evaluations:,}: {task.title} {batch['summary']}", flush=True)
    if graph_hash(graph) != immutable_hash:
        raise AssertionError("campaign changed the scan")
    result["complete"] = result["evaluations"] == evaluations
    _save(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evaluations", type=int, default=2_000_000)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--replay-decisions", type=Path, help="reuse a saved task order without TypeSafe calls")
    args = parser.parse_args()
    # Reuse the application env loader without opening or mutating its database.
    from standardphysics_api.settings import Settings
    Settings.from_environment()
    graph = SceneGraph.model_validate_json(args.graph.read_bytes())
    mesh = LidarMesh.model_validate_json(args.mesh.read_bytes())
    if args.tasks.exists():
        saved = json.loads(args.tasks.read_text())
        suite = TaskSuite.model_validate({"tasks": saved["tasks"]})
    else:
        suite, source = propose_tasks(graph)
        saved = {"source": source, **suite.model_dump(mode="json")}
        _save(args.tasks, saved)
    planner: Callable = choose_task
    if args.replay_decisions:
        plan = json.loads(args.replay_decisions.read_text())
        task_order = [batch["task_id"] for batch in plan["batches"]]
        if len(task_order) != len(suite.tasks) or set(task_order) != {task.id for task in suite.tasks}:
            raise ValueError("saved plan must contain every task exactly once")
        def replay_planner(remaining, history):
            task = next(task for task in remaining if task.id == task_order[len(history)])
            return task, {"source": "saved TypeSafe order", "calls": 0}
        planner = replay_planner
    result = run_campaign(graph, mesh, suite, evaluations=args.evaluations,
                          seed=args.seed, output=args.out, planner=planner)
    result["task_generation"] = saved.get("source")
    result["sources"] = {name: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                         for name, path in (("graph", args.graph), ("mesh", args.mesh), ("tasks", args.tasks))}
    _save(args.out, result)


if __name__ == "__main__":
    main()
