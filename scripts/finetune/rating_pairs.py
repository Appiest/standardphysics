"""Layout pairs for a person to rate, built to test every part of Q, not only turning.

For up to VARIANTS_PER_WINDOW variants of every window, a pool of rearrangements
is scored with the training reward. The pool holds the search's answer, putting
every scrambled piece back, and putting back one piece at a time. Each of those
is varied further:

    different destination       one moved piece lands 0.4 m elsewhere
    table pushed toward a wall  a table slid most or half of the way to its nearest wall
    chair moved from its table  a seat pulled 0.6 m further from the table it belongs to
    chair turned from its table a seat turned 90 or 180 degrees (rotation only)
    turned                      one moved piece turned 30 degrees (rotation only)

Only layouts that pass every hard constraint and the gate enter the pool. Two
layouts of one variant make a pair when their Q differs by at least MIN_Q_GAP
and they differ in exactly one piece (`differs_by`).
`select` then picks PAIR_COUNT pairs: as many windows as possible, at most a
third rotation-only, quotas for tables at walls, chairs and held-out rooms, and
Q gaps spread across small, middle and large.

    python scripts/finetune/rating_pairs.py --workers 6
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import pathlib
import random
from collections import Counter
from dataclasses import dataclass, field

from multiroom_data import DEFAULT_RUN, _append, _log, _rows, _write_json, checker_for
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.redesign import FurnitureMove, RoomEdits
from standardphysics_agents.training import edits_between, score_completion
from standardphysics_agents.training.edits import edits_json, node_moves
from standardphysics_agents.training.quality import _to_segment, seat_table_pairs, wall_segments
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph, SceneNode
from standardphysics_pipeline.footprints import rotation_about_z

PAIR_COUNT = 30
MIN_Q_GAP = 0.05
VARIANTS_PER_WINDOW = 2
MAX_PAIRS_PER_WINDOW = 2
MAX_ROTATION_ONLY = PAIR_COUNT // 3
QUOTAS = {"table pushed toward a wall": 4, "chair moved from its table": 3, "chair turned from its table": 2}
HELDOUT_QUOTA = 4
GAP_BINS = ((0.05, 0.10), (0.10, 0.20), (0.20, 1.01))
DESTINATION_SHIFT_METERS = 0.4
CHAIR_PULL_METERS = 0.6
WALL_CLEARANCE_METERS = 0.05
ROTATION_ONLY = frozenset({"turned", "chair turned from its table"})
SAME_PLACE_METERS = 0.01


@dataclass
class Layout:
    family: str
    edits: RoomEdits
    verdict: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return edits_json(self.edits)


_WINDOWS: dict[str, Window] = {}


def _load(run: str) -> None:
    for row in _rows(pathlib.Path(run) / "windows.jsonl"):
        _WINDOWS[row["window_id"]] = Window.from_dict(row)


def _wrapped(degrees: float) -> float:
    return float(round((degrees + 180.0) % 360.0 - 180.0))


def _merge(edits: RoomEdits, node_id, dx: float, dy: float, turn: float) -> RoomEdits:
    """The edits with one piece's move extended by a further slide and turn."""
    moves = {move.node_id: move for move in edits.moves}
    old = moves.get(node_id, FurnitureMove(node_id=node_id, dx=0.0, dy=0.0, rotation_degrees=0.0))
    moves[node_id] = FurnitureMove(node_id=node_id, dx=round(old.dx + dx, 2), dy=round(old.dy + dy, 2),
                                   rotation_degrees=_wrapped(old.rotation_degrees + turn))
    return RoomEdits(moves=list(moves.values()))


def bases(variant: SceneGraph, owner: SceneGraph, target: str | None) -> list[Layout]:
    restore = edits_between(variant, owner)
    found = [Layout("put everything back", restore)] if restore.moves else []
    if len(restore.moves) > 1:
        found += [Layout("put one piece back", RoomEdits(moves=[move])) for move in restore.moves]
    if target:
        found.append(Layout("search answer", RoomEdits.model_validate_json(target)))
    return found


def _half_extent(node: SceneNode, direction) -> float:
    cos_t, sin_t = rotation_about_z(node)
    along_x = abs(direction[0] * cos_t + direction[1] * sin_t)
    along_y = abs(-direction[0] * sin_t + direction[1] * cos_t)
    return along_x * node.dimensions.x / 2 + along_y * node.dimensions.y / 2


def _toward_wall(node: SceneNode, walls) -> tuple[float, float, float] | None:
    centre = (node.transform.position.x, node.transform.position.y)
    wall = min(walls, key=lambda segment: _to_segment(centre, segment))
    (ax, ay), (bx, by) = wall
    length = (bx - ax) ** 2 + (by - ay) ** 2 or 1.0
    t = max(0.0, min(1.0, ((centre[0] - ax) * (bx - ax) + (centre[1] - ay) * (by - ay)) / length))
    near = (ax + t * (bx - ax), ay + t * (by - ay))
    distance = math.dist(centre, near)
    if distance == 0:
        return None
    direction = ((near[0] - centre[0]) / distance, (near[1] - centre[1]) / distance)
    gap = distance - _half_extent(node, direction) - WALL_CLEARANCE_METERS
    return (direction[0], direction[1], gap) if gap > 0.1 else None


def _tables(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes
            if node.movable and any(word in node.label.casefold() for word in ("table", "desk"))]


def table_pushes(base: Layout, placed: SceneGraph, walls) -> list[Layout]:
    found = []
    for table in _tables(placed) if walls else []:
        push = _toward_wall(table, walls)
        if push is None:
            continue
        for share in (1.0, 0.5):
            found.append(Layout("table pushed toward a wall",
                                _merge(base.edits, table.id, push[0] * push[2] * share, push[1] * push[2] * share, 0)))
    return found


def chair_changes(base: Layout, placed: SceneGraph, owner: SceneGraph) -> list[Layout]:
    by_id, found = {node.id: node for node in placed.nodes}, []
    for seat_id, table_id in seat_table_pairs(owner):
        seat, table = by_id.get(seat_id), by_id.get(table_id)
        if seat is None or table is None or not seat.movable:
            continue
        away = (seat.transform.position.x - table.transform.position.x,
                seat.transform.position.y - table.transform.position.y)
        length = math.hypot(*away) or 1.0
        pull = (away[0] / length * CHAIR_PULL_METERS, away[1] / length * CHAIR_PULL_METERS)
        found.append(Layout("chair moved from its table", _merge(base.edits, seat_id, pull[0], pull[1], 0)))
        found += [Layout("chair turned from its table", _merge(base.edits, seat_id, 0, 0, turn)) for turn in (90, 180)]
    return found


def simple_changes(base: Layout) -> list[Layout]:
    found = []
    for move in base.edits.moves:
        found += [Layout("different destination", _merge(base.edits, move.node_id, dx, dy, 0))
                  for dx, dy in ((DESTINATION_SHIFT_METERS, 0), (0, DESTINATION_SHIFT_METERS),
                                 (-DESTINATION_SHIFT_METERS, 0), (0, -DESTINATION_SHIFT_METERS))]
        found.append(Layout("turned", _merge(base.edits, move.node_id, 0, 0, 30)))
    return found


def pool_for(variant: SceneGraph, window: Window, target: str | None) -> list[Layout]:
    """Every scored, gate-accepted rearrangement of one variant."""
    checker, walls, accepted = checker_for(window), wall_segments(window.graph), []
    for base in bases(variant, window.graph, target):
        placed = apply_moves(variant, node_moves(base.edits))
        for layout in [base, *simple_changes(base), *table_pushes(base, placed, walls),
                       *chair_changes(base, placed, window.graph)]:
            verdict = score_completion(layout.text, variant, checker)
            if verdict.gate_accepts:
                layout.verdict = verdict.as_dict()
                accepted.append(layout)
    return _distinct(accepted)


def _distinct(layouts: list[Layout]) -> list[Layout]:
    seen, kept = set(), []
    for layout in layouts:
        if layout.text not in seen:
            seen.add(layout.text)
            kept.append(layout)
    return kept


def _pool_task(job: dict) -> dict:
    window = _WINDOWS[job["window_id"]]
    layouts = pool_for(SceneGraph.model_validate(job["graph"]), window, job["target"])
    return {"variant_id": job["variant_id"], "window_id": job["window_id"],
            "layouts": [{"family": x.family, "edits": x.text, "verdict": x.verdict} for x in layouts]}


def _final_places(variant: SceneGraph, edits: str) -> dict:
    placed = apply_moves(variant, node_moves(RoomEdits.model_validate_json(edits)))
    return {node.id: (node.transform.position.x, node.transform.position.y) for node in placed.nodes}


BASE_FAMILIES = frozenset({"put everything back", "put one piece back", "search answer"})


def _moves(layout: dict) -> dict:
    return {move.node_id: move for move in RoomEdits.model_validate_json(layout["edits"]).moves}


def _changed_pieces(first: dict, second: dict) -> set:
    a, b = _moves(first), _moves(second)
    return {key for key in a.keys() | b.keys() if a.get(key) != b.get(key)}


def _kind_of_change(variant: SceneGraph, first: dict, second: dict, piece) -> str:
    families = {first["family"], second["family"]}
    places = (_final_places(variant, first["edits"])[piece], _final_places(variant, second["edits"])[piece])
    if math.dist(*places) <= SAME_PLACE_METERS:
        return "chair turned from its table" if "chair turned from its table" in families else "turned"
    for family in ("table pushed toward a wall", "chair moved from its table"):
        if family in families:
            return family
    return "different destination"


def differs_by(variant: SceneGraph, first: dict, second: dict) -> str | None:
    """The one thing separating two layouts of a variant, or None when they differ in more than one piece.

    Two starting layouts (the search answer, or pieces put back) may differ in
    which pieces moved at all; any other pair has to differ in exactly one piece
    so a rater's choice says something about that one change.
    """
    changed = _changed_pieces(first, second)
    if {first["family"], second["family"]} <= BASE_FAMILIES:
        return "different pieces moved" if changed else None
    if len(changed) != 1:
        return None
    return _kind_of_change(variant, first, second, next(iter(changed)))


def candidate_pairs(pool: dict, variant: SceneGraph, split: str) -> list[dict]:
    layouts, found = pool["layouts"], []
    for i, first in enumerate(layouts):
        for second in layouts[i + 1:]:
            gap = abs(first["verdict"]["quality"]["q"] - second["verdict"]["quality"]["q"])
            kind = differs_by(variant, first, second) if gap >= MIN_Q_GAP else None
            if kind is not None:
                found.append({"window_id": pool["window_id"], "variant_id": pool["variant_id"], "split": split,
                              "gap": round(gap, 4), "differs_by": kind,
                              "first": first, "second": second})
    return found


def _bin(gap: float) -> int:
    return next(index for index, (low, high) in enumerate(GAP_BINS) if low <= gap < high)


@dataclass
class Selection:
    pairs: list = field(default_factory=list)

    def windows(self) -> Counter:
        return Counter(pair["window_id"] for pair in self.pairs)

    def allows(self, pair: dict) -> bool:
        if self.windows()[pair["window_id"]] >= MAX_PAIRS_PER_WINDOW:
            return False
        if pair["differs_by"] in ROTATION_ONLY and self.rotation_only() >= MAX_ROTATION_ONLY:
            return False
        edits = {pair["first"]["edits"], pair["second"]["edits"]}
        return not any(p["window_id"] == pair["window_id"] and edits & {p["first"]["edits"], p["second"]["edits"]}
                       for p in self.pairs)

    def rotation_only(self) -> int:
        return sum(1 for pair in self.pairs if pair["differs_by"] in ROTATION_ONLY)

    def take(self, candidates: list[dict], wanted: int, test) -> None:
        for pair in candidates:
            if wanted <= 0 or len(self.pairs) >= PAIR_COUNT:
                return
            if test(pair) and self.allows(pair):
                self.pairs.append(pair)
                wanted -= 1


def _balanced(candidates: list[dict], selection: Selection) -> list[dict]:
    """Candidates ordered so the next pick fills the emptiest gap bin and kind from an unused window."""
    bins = Counter(_bin(p["gap"]) for p in selection.pairs)
    kinds = Counter(p["differs_by"] for p in selection.pairs)
    used = selection.windows()
    return sorted(candidates, key=lambda p: (used[p["window_id"]], bins[_bin(p["gap"])], kinds[p["differs_by"]]))


def select(candidates: list[dict], ravida_windows: set[str], seed: int = 17) -> list[dict]:
    rng = random.Random(seed)
    rng.shuffle(candidates)
    selection = Selection()
    selection.take(_balanced(candidates, selection), 1, lambda p: p["window_id"] in ravida_windows)
    selection.take(_balanced(candidates, selection), HELDOUT_QUOTA - 1,
                   lambda p: p["split"] == "heldout" and p["window_id"] not in ravida_windows)
    for kind, wanted in QUOTAS.items():
        for _ in range(wanted):
            selection.take(_balanced(candidates, selection), 1, lambda p, kind=kind: p["differs_by"] == kind)
    while len(selection.pairs) < PAIR_COUNT:
        before = len(selection.pairs)
        selection.take(_balanced(candidates, selection), 1, lambda p: True)
        if len(selection.pairs) == before:
            break
    return selection.pairs


def _side(layout: dict, source: str) -> dict:
    return {"source": source, "edits": layout["edits"], "q": layout["verdict"]["quality"],
            "reward": layout["verdict"]["reward"]}


def rating_row(index: int, pair: dict, variant: SceneGraph, rng: random.Random) -> dict:
    sides = [pair["first"], pair["second"]]
    rng.shuffle(sides)
    window = _WINDOWS[pair["window_id"]]
    return {
        "pair_id": f"rating-{index:02d}", "scan_id": window.scan_id, "window_id": pair["window_id"],
        "variant_id": pair["variant_id"], "starting_graph_revision": variant.revision,
        "a": _side(sides[0], sides[0]["family"]), "b": _side(sides[1], sides[1]["family"]),
        "question": "Which rearrangement looks better?", "picked": None,
    }


def _jobs(run: pathlib.Path) -> list[dict]:
    targets = {row["variant_id"]: row["target"] for row in _rows(run / "targets.jsonl")}
    per_window: dict[str, list] = {}
    for row in _rows(run / "variants.jsonl"):
        if row["variant_id"] and row["name"] != "owner":
            per_window.setdefault(row["window_id"], []).append(row)
    jobs = []
    for rows in per_window.values():
        rows.sort(key=lambda row: (targets.get(row["variant_id"]) is None, row["variant_id"]))
        jobs += [{**row, "target": targets.get(row["variant_id"])} for row in rows[:VARIANTS_PER_WINDOW]]
    return jobs


def build_pools(run: pathlib.Path, workers: int) -> list[dict]:
    path = run / "rating_candidates.jsonl"
    done = {row["variant_id"] for row in _rows(path)}
    todo = [job for job in _jobs(run) if job["variant_id"] not in done]
    with multiprocessing.get_context("spawn").Pool(workers, initializer=_load, initargs=(str(run),)) as pool:
        for result in pool.imap_unordered(_pool_task, todo):
            _append(path, result)
            _log(f"pool {result['variant_id']}: {len(result['layouts'])} accepted layouts")
    return _rows(path)


def composition(pairs: list[dict]) -> dict:
    gaps = sorted(pair["gap"] for pair in pairs)
    return {
        "pairs": len(pairs), "windows": len({p["window_id"] for p in pairs}),
        "differs_by": dict(Counter(p["differs_by"] for p in pairs)),
        "rotation_only": sum(1 for p in pairs if p["differs_by"] in ROTATION_ONLY),
        "split": dict(Counter(p["split"] for p in pairs)),
        "q_gap": {"min": gaps[0], "median": gaps[len(gaps) // 2], "max": gaps[-1],
                  "bins": {f"{low:.2f}-{high:.2f}": sum(1 for g in gaps if low <= g < high) for low, high in GAP_BINS}},
        "per_pair": [{"pair_id": f"rating-{i:02d}", "window_id": p["window_id"], "split": p["split"],
                      "differs_by": p["differs_by"], "q_gap": p["gap"],
                      "sources": [p["first"]["family"], p["second"]["family"]]} for i, p in enumerate(pairs)],
    }


def run(run_dir: pathlib.Path, workers: int, seed: int = 17) -> dict:
    _load(str(run_dir))
    pools = build_pools(run_dir, workers)
    report = json.loads((run_dir / "report.json").read_text())
    splits = {row["window_id"]: row["split"] for row in report["windows"]}
    ravida = {row["window_id"] for row in report["windows"] if row["scan"] == "ravida"}
    variants = {row["variant_id"]: row for row in _rows(run_dir / "variants.jsonl") if row["variant_id"]}
    graphs = {vid: SceneGraph.model_validate(variants[vid]["graph"]) for vid in {p["variant_id"] for p in pools}}
    candidates = [pair for pool in pools
                  for pair in candidate_pairs(pool, graphs[pool["variant_id"]], splits[pool["window_id"]])]
    pairs = select(candidates, ravida, seed)
    rng = random.Random(seed)
    rows = [rating_row(i, pair, graphs[pair["variant_id"]], rng) for i, pair in enumerate(pairs)]
    (run_dir / "rating_pairs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = composition(pairs)
    _write_json(run_dir / "rating_pairs.composition.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=pathlib.Path, default=DEFAULT_RUN)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    summary = run(args.run, args.workers)
    print(json.dumps({key: value for key, value in summary.items() if key != "per_pair"}, indent=2))


if __name__ == "__main__":
    main()
