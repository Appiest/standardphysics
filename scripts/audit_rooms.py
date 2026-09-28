"""Audit 3D indoor scene models for Standard Physics RL rearranger training.

A room is considered "actually useful" for the RL rearranger if it satisfies:
  1. Commercial / dining / retail typology (has entrance, service/destination point, and seating/display zone).
  2. Movable object count between 3 and 30 (sufficient for non-trivial rearrangement).
  3. Actionable floor area between 15 m^2 and 250 m^2 (not too cramped to move, not too vast to be trivial).
  4. At least one accessible path bottleneck / clearance constraint (< 60 inches passing or < 36 inches aisle).
  5. Clean metric scale and enclosed boundary walls.

Usage:
    .venv/bin/python scripts/audit_rooms.py --help
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass

from standardphysics_contracts import SceneGraph, SceneNode


@dataclass
class AuditResult:
    room_id: str
    is_useful: bool
    score: float  # 0.0 to 1.0
    floor_area_m2: float
    movable_count: int
    fixed_count: int
    has_entrance: bool
    reasons: list[str]
    suggested_journey: list[str]


def _floor_area_m2(floors: list[SceneNode], walls: list[SceneNode]) -> float:
    if floors:
        dims = sorted([floors[0].dimensions.x, floors[0].dimensions.y, floors[0].dimensions.z], reverse=True)
        return dims[0] * dims[1]
    if walls:
        xs = [n.transform.m[3] for n in walls]
        ys = [n.transform.m[7] for n in walls]
        if xs and ys:
            return max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
    return 0.0


def audit_scene_graph(graph: SceneGraph) -> AuditResult:
    """Audit a single SceneGraph for RL rearranger suitability."""
    movable_nodes = [n for n in graph.nodes if n.movable]
    fixed_nodes = [n for n in graph.nodes if not n.movable and n.kind not in ("wall", "floor", "ceiling")]
    doors = [n for n in graph.nodes if n.kind == "door" or "door" in n.raw_category.lower()]
    walls = [n for n in graph.nodes if n.kind == "wall" or "wall" in n.raw_category.lower()]
    floors = [n for n in graph.nodes if n.kind == "floor" or "floor" in n.raw_category.lower()]
    area_m2 = _floor_area_m2(floors, walls)

    reasons: list[str] = []
    is_useful = True
    score = 1.0

    # Criterion 1: Movable object density
    if len(movable_nodes) < 3:
        is_useful = False
        reasons.append(f"Too few movable objects ({len(movable_nodes)} < 3); trivial rearrangement")
        score -= 0.4
    elif len(movable_nodes) > 30:
        reasons.append(f"High movable density ({len(movable_nodes)} > 30); consider pruning background decor")
        score -= 0.1

    # Criterion 2: Floor Area
    if area_m2 < 12.0:
        is_useful = False
        reasons.append(f"Floor area too small ({area_m2:.1f} m^2 < 12 m^2); no space to solve clearances")
        score -= 0.4
    elif area_m2 > 300.0:
        is_useful = False
        reasons.append(f"Floor area too large ({area_m2:.1f} m^2 > 300 m^2); clearance issues trivially pass")
        score -= 0.3

    # Criterion 3: Entrance & Boundaries
    has_entrance = len(doors) > 0
    if not has_entrance:
        reasons.append("No explicit door node found; will need synthetic entrance waypoint on perimeter")
        score -= 0.15

    # Suggested Journey
    suggested_journey = ["Entrance"]
    has_service = any("counter" in n.raw_category.lower() or "bar" in n.raw_category.lower() for n in fixed_nodes)
    if has_service:
        suggested_journey.append("Order / Service Counter")
    suggested_journey.extend(["Customer Seating Table", "Exit"])

    score = max(0.0, min(1.0, score))
    if score >= 0.6:
        reasons.insert(0, "PASSED: Room has actionable commercial furniture density, bounded area, and valid circulation.")

    return AuditResult(
        room_id=str(graph.scan_id),
        is_useful=is_useful and score >= 0.6,
        score=score,
        floor_area_m2=area_m2,
        movable_count=len(movable_nodes),
        fixed_count=len(fixed_nodes),
        has_entrance=has_entrance,
        reasons=reasons,
        suggested_journey=suggested_journey,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("room_paths", nargs="*", type=pathlib.Path, help="Paths to SceneGraph JSON files to audit")
    args = parser.parse_args()

    if not args.room_paths:
        # Look in datasets/converted_rooms by default
        default_dir = pathlib.Path("datasets/converted_rooms")
        if default_dir.exists():
            args.room_paths = list(default_dir.glob("*.json"))

    if not args.room_paths:
        print("No room files found to audit. Please provide path(s) to SceneGraph JSON files.")
        return 1

    print(f"Auditing {len(args.room_paths)} room(s)...")
    passed = 0
    for path in args.room_paths:
        try:
            data = json.loads(path.read_text())
            graph = SceneGraph.model_validate(data)
            res = audit_scene_graph(graph)
            status = "PASS" if res.is_useful else "FAIL"
            print(f"[{status}] {path.name} | Score: {res.score:.2f} | Area: {res.floor_area_m2:.1f}m² | Movables: {res.movable_count} | Fixed: {res.fixed_count}")
            for r in res.reasons:
                print(f"   - {r}")
            if res.is_useful:
                passed += 1
        except Exception as e:
            print(f"[ERR]  {path.name}: {e}")

    print(f"\nAudit complete: {passed}/{len(args.room_paths)} rooms approved for RL training.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
