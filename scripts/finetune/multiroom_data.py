"""Build the multi-room rearrangement dataset: windows, scrambled variants, search targets, splits.

Stages, each resumable (a rerun skips every item already written):

    export    local only: the database, read-only, to compact scan JSON under RUN/exports
    windows   plan each scan and cut its windows            -> RUN/windows.jsonl, RUN/plans.json
    variants  scramble every window                         -> RUN/variants.jsonl
    targets   deterministic search on every variant         -> RUN/targets.jsonl
    split     held-out rooms by place, then prompt rows     -> RUN/dataset/*.jsonl, RUN/report.json
    corrections  accepted one-step search traces -> RUN/dataset/corrections.jsonl
    trace-corrections  checked training-room model traces -> RUN/dataset/corrections_from_trace.jsonl
    all       windows, variants, targets and split in order

Progress is rewritten atomically to RUN/progress.json after every item.

    python scripts/finetune/multiroom_data.py export --database services/api/var/standardphysics.sqlite3
    nohup caffeinate -s python scripts/finetune/multiroom_data.py all --workers 6 > RUN/generate.log 2>&1 &
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import pathlib
import time
import uuid
import zlib
from collections import Counter

from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training import TrainingChecker, edits_between, edits_json, prompt_messages, scramble
from standardphysics_agents.training.edits import node_moves, parse_edits
from standardphysics_agents.training.feedback import feedback_message
from standardphysics_agents.training.reward import score_completion
from standardphysics_agents.training.rooms import build_window, plan_scan, whole_scan
from standardphysics_agents.training.scans import export_scan, load_export, read_only, write_export
from standardphysics_agents.training.scramble import LIGHT
from standardphysics_agents.training.split import (
    DROPPED,
    HELDOUT,
    TRAIN,
    HoldOut,
    RoomRecord,
    assign,
    pick_floor,
    split_report,
)
from standardphysics_agents.training.targets import searched_layout
from standardphysics_agents.training.usability import usability
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph, bounds_the_room

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_RUN = ROOT / "runs/finetune/multiroom"
SCAN_NAMES = (
    "A-102 Moffett Library floor (overlay)", "Moffett Library floor (overlay)", "Combined: left + center + top",
    "center", "room 6", "test1", "ravida", "bread test", "Sample boba shop",
)
CONTAINED_SHARE = 0.9
"""A scan whose node ids are at least this share inside a bigger scan is the same place, and skipped."""
VARIANTS_PER_WINDOW = 8
HELDOUT_SMALL_SCAN = "ravida"
HELDOUT_BIG_SCAN_WINDOWS = 9
MAX_CORRECTION_STEPS = 5


def _write_json(path: pathlib.Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    os.replace(temporary, path)


def _rows(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path: pathlib.Path, row: dict) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", flush=True)


class Progress:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.state = json.loads(path.read_text()) if path.exists() else {"stages": {}}

    def stage(self, name: str, total: int, done: int) -> None:
        now = time.time()
        self.state["current"] = name
        self.state["stages"][name] = {"total": total, "done": done, "started": now, "resumed_at_done": done,
                                      "updated": now, "status": "running"}
        _write_json(self.path, self.state)

    def tick(self, name: str) -> None:
        entry = self.state["stages"][name]
        entry["done"] += 1
        entry["updated"] = time.time()
        fresh = entry["done"] - entry["resumed_at_done"]
        rate = (entry["updated"] - entry["started"]) / fresh if fresh else None
        entry["eta_minutes"] = None if rate is None else round(rate * (entry["total"] - entry["done"]) / 60, 1)
        _write_json(self.path, self.state)

    def finish(self, name: str, **summary) -> None:
        self.state["stages"][name].update(status="done", finished=time.time(), **summary)
        _write_json(self.path, self.state)


def _contained_in(export, others) -> str | None:
    ids = {node["id"] for node in export.latest["nodes"]}
    for other in others:
        if len(other.latest["nodes"]) <= len(ids) or other.scan_id == export.scan_id:
            continue
        shared = len(ids & {node["id"] for node in other.latest["nodes"]}) / len(ids)
        if shared >= CONTAINED_SHARE:
            return f"{shared:.0%} of its node ids are in {other.name!r}, the same place"
    return None


def skip_reasons(exports) -> dict[str, str]:
    """Why each skipped scan is skipped; scans not named are used."""
    reasons = {}
    for export in exports:
        why = [text for text in (
            _contained_in(export, exports),
            None if export.scenario is not None else "no saved scenario",
            None if not export.duplicates_dropped else f"{len(export.duplicates_dropped)} node ids used twice",
        ) if text]
        if why:
            reasons[export.scan_id] = "; ".join(why)
    return reasons


def run_export(database: pathlib.Path, run: pathlib.Path) -> None:
    connection = read_only(database)
    try:
        exports = [export_scan(connection, name) for name in SCAN_NAMES]
    finally:
        connection.close()
    directory = run / "exports"
    scans: list[dict] = []
    for export in exports:
        path = write_export(directory, export)
        scans.append({"scan_id": export.scan_id, "name": export.name, "latest_revision": export.latest_revision,
                      "nodes": len(export.latest["nodes"]), "bytes": path.stat().st_size,
                      "duplicates_dropped": export.duplicates_dropped})
        _log(f"exported {export.name!r} r{export.latest_revision}: {len(export.latest['nodes'])} nodes, "
             f"{len(export.duplicates_dropped)} duplicate ids dropped")
    _write_json(directory / "export_log.json", {"scans": scans, "skipped": skip_reasons(exports)})


def _used_exports(run: pathlib.Path):
    exports = [load_export(path) for path in sorted((run / "exports").glob("*-*.json"))]
    skipped = skip_reasons(exports)
    return [export for export in exports if export.scan_id not in skipped], skipped


_PLANS: dict = {}


def _load_plans(run: pathlib.Path) -> None:
    for export in _used_exports(run)[0]:
        _PLANS[export.scan_id] = plan_scan(export.scan_id, export.graph(), export.scenario_model())


def _window_task(task: tuple) -> tuple[dict | None, dict]:
    scan_id, centre, seed, window_id = task
    window, entry = build_window(_PLANS[scan_id], tuple(centre), seed, window_id)
    return (None if window is None else window.as_dict()), entry


def _window_tasks(plan) -> list[tuple]:
    centres = [(tuple(seed["at"]), seed) for seed in plan.seeds] + [(centre, None) for centre in plan.clusters]
    return [(plan.scan_id, centre, seed, f"{plan.scan_id[:8]}:w{index:02d}")
            for index, (centre, seed) in enumerate(centres)]


def _plan_summary(plan, name: str) -> dict:
    return {"name": name, "small": plan.small, "nodes": len(plan.graph.nodes),
            "pinned": [item.as_dict() for item in plan.pinned], "unmeasured_dropped": plan.unmeasured,
            "removed_scan_errors": [{"node_id": str(n.id), "label": n.label} for n in plan.removed],
            "seeds": plan.seeds, "skipped_seeds": plan.skipped_seeds,
            "clusters": [[round(x, 3), round(y, 3)] for x, y in plan.clusters]}


def _pool(workers: int, run: pathlib.Path):
    return multiprocessing.get_context("spawn").Pool(workers, initializer=_initialise, initargs=(str(run),))


def _initialise(run: str) -> None:
    _load_plans(pathlib.Path(run))


def run_windows(run: pathlib.Path, workers: int, progress: Progress) -> None:
    exports, skipped = _used_exports(run)
    _load_plans(run)
    names = {export.scan_id: export.name for export in exports}
    _write_json(run / "plans.json", {"skipped_scans": skipped,
                                    "plans": {sid: _plan_summary(plan, names[sid]) for sid, plan in _PLANS.items()}})
    done = {row["window_id"] for row in _rows(run / "windows_log.jsonl")}
    for window, entry in (whole_scan(plan) for plan in _PLANS.values() if plan.small):
        if entry["window_id"] in done:
            continue
        if window is not None:
            _append(run / "windows.jsonl", window.as_dict())
        _append(run / "windows_log.jsonl", entry)
    tasks = [task for plan in _PLANS.values() if not plan.small for task in _window_tasks(plan) if task[3] not in done]
    progress.stage("windows", len(tasks) + len(done), len(done))
    with _pool(workers, run) as pool:
        for window, entry in pool.imap_unordered(_window_task, tasks):
            if window is not None:
                _append(run / "windows.jsonl", window)
            _append(run / "windows_log.jsonl", entry)
            progress.tick("windows")
            _log(f"window {entry['window_id']} kept={entry['kept']} objects={entry.get('objects')}")
    progress.finish("windows", kept=len(_rows(run / "windows.jsonl")))


TRAINING_SCOPE = os.environ.get("TRAINING_SCOPE", "layout")
"""`fittings` also asks about counter, table and reach heights; `layout` keeps the furniture-only checker."""


def checker_for(window: Window) -> TrainingChecker:
    return TrainingChecker(window.scenario, pinned=frozenset(uuid.UUID(i) for i in window.pinned),
                           owner_layout=window.graph, scope=TRAINING_SCOPE)


def _variant_task(row: dict) -> list[dict]:
    window = Window.from_dict(row)
    checker = checker_for(window)
    seed = zlib.crc32(window.window_id.encode())
    rows = []
    fixable = checker.fixable_problems(checker.assess(window.graph))
    if fixable:
        rows.append(_variant_row(window, "owner", window.graph, sorted({f.check_id for f in fixable})))
    for variant in scramble(window.graph, checker, VARIANTS_PER_WINDOW, seed=seed, how=LIGHT):
        rows.append(_variant_row(window, variant.name, variant.graph, list(variant.fixable)))
    return rows or [{"window_id": window.window_id, "variant_id": None}]


def _variant_row(window: Window, name: str, graph: SceneGraph, fixable: list[str]) -> dict:
    return {"variant_id": f"{window.window_id}:{name}", "window_id": window.window_id, "scan_id": window.scan_id,
            "name": name, "fixable": fixable, "graph": graph.model_dump(mode="json")}


def run_variants(run: pathlib.Path, workers: int, progress: Progress) -> None:
    windows = _rows(run / "windows.jsonl")
    done = {row["window_id"] for row in _rows(run / "variants.jsonl")}
    todo = [row for row in windows if row["window_id"] not in done]
    progress.stage("variants", len(windows), len(windows) - len(todo))
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for rows in pool.imap_unordered(_variant_task, todo):
            for row in rows:
                _append(run / "variants.jsonl", row)
            progress.tick("variants")
            _log(f"variants for {rows[0]['window_id']}: {sum(1 for r in rows if r['variant_id'])}")
    progress.finish("variants", variants=sum(1 for r in _rows(run / "variants.jsonl") if r["variant_id"]))


_WINDOWS: dict[str, Window] = {}


def _load_windows(run: str) -> None:
    for row in _rows(pathlib.Path(run) / "windows.jsonl"):
        _WINDOWS[row["window_id"]] = Window.from_dict(row)


def search_record(variant: SceneGraph, checker: TrainingChecker) -> dict:
    """The search's answer for one variant and how well it does, including when it fails."""
    edits = edits_between(variant, searched_layout(variant, checker))
    if not edits.moves:
        return {"target": None, "verdict": None, "why": "search found no improvement"}
    verdict = score_completion(edits_json(edits), variant, checker)
    accepted = verdict.gate_accepts
    return {"target": edits_json(edits) if accepted else None, "verdict": verdict.as_dict(),
            "why": None if accepted else f"search answer rejected: {verdict.reason}"}


def put_back_record(variant: SceneGraph, owner: SceneGraph, checker: TrainingChecker) -> dict | None:
    """Every scrambled piece returned to where the owner has it, scored like any answer."""
    edits = edits_between(variant, owner)
    if not edits.moves:
        return None
    return {"edits": edits_json(edits), "verdict": score_completion(edits_json(edits), variant, checker).as_dict()}


def chosen_target(put_back: dict | None, search: dict) -> dict:
    """Putting things back when the owner's layout passes the constraints and the gate, else the search's fix."""
    if put_back and put_back["verdict"]["gate_accepts"]:
        return {"target": put_back["edits"], "source": "put_back", "verdict": put_back["verdict"], "why": None}
    if search["target"]:
        return {"target": search["target"], "source": "search", "verdict": search["verdict"], "why": None}
    return {"target": None, "source": None, "verdict": search["verdict"], "why": search["why"]}


def _target_task(row: dict) -> dict:
    started = time.time()
    window = _WINDOWS[row["window_id"]]
    checker, variant = checker_for(window), SceneGraph.model_validate(row["graph"])
    search = search_record(variant, checker)
    put_back = put_back_record(variant, window.graph, checker)
    return {"variant_id": row["variant_id"], "window_id": row["window_id"], **chosen_target(put_back, search),
            "search": search, "put_back": put_back, "seconds": round(time.time() - started, 1)}


def run_targets(run: pathlib.Path, workers: int, progress: Progress) -> None:
    variants = [row for row in _rows(run / "variants.jsonl") if row["variant_id"]]
    done = {row["variant_id"] for row in _rows(run / "targets.jsonl")}
    todo = [row for row in variants if row["variant_id"] not in done]
    progress.stage("targets", len(variants), len(variants) - len(todo))
    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, initializer=_load_windows, initargs=(str(run),), maxtasksperchild=20) as pool:
        for record in pool.imap_unordered(_target_task, todo):
            _append(run / "targets.jsonl", record)
            progress.tick("targets")
            verdict = record["verdict"] or {}
            _log(f"target {record['variant_id']} {record['seconds']}s accepted={bool(record['target'])} "
                 f"recovered={verdict.get('shortfall_recovered')} left={verdict.get('fixable_left')}")
    progress.finish("targets", with_target=sum(1 for r in _rows(run / "targets.jsonl") if r["target"]))


def room_record(window: Window) -> RoomRecord:
    pieces = frozenset(str(node.id) for node in window.graph.nodes if not bounds_the_room(node))
    return RoomRecord(window.window_id, window.scan_id, window.floor_id(), pieces)


def hold_out(rooms: list[RoomRecord], names: dict[str, str]) -> HoldOut:
    small = {sid for sid, name in names.items() if name == HELDOUT_SMALL_SCAN}
    per_scan = Counter(room.scan_id for room in rooms)
    big = max(per_scan, key=lambda scan: per_scan[scan])
    floor = pick_floor(rooms, big, HELDOUT_BIG_SCAN_WINDOWS)
    return HoldOut(scans=frozenset(small), floors=frozenset({(big, floor)} if floor else set()))


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def target_quality(targets: list[dict]) -> dict:
    accepted = [row["verdict"] for row in targets if row["target"]]
    count = len(targets)
    return {
        "variants": count,
        "with_target": len(accepted),
        "share_with_target": round(len(accepted) / count, 4) if count else None,
        "mean_shortfall_recovered_over_targets": _mean([v["shortfall_recovered"] for v in accepted]),
        "mean_shortfall_recovered_over_variants": round(sum(v["shortfall_recovered"] for v in accepted) / count, 4)
        if count else None,
        "share_clearing_every_fixable": round(sum(1 for v in accepted if v["fixable_left"] == 0) / count, 4)
        if count else None,
        "why_no_target": dict(Counter((row["why"] or "").split(":")[0] for row in targets if not row["target"])),
        "target_source": dict(Counter(row.get("source") for row in targets if row["target"])),
        "mean_usability_over_targets": _mean([v["usability"] for v in accepted if v.get("usability") is not None]),
    }


def _prompt_row(variant: dict, window: Window, target: dict | None) -> dict:
    messages = prompt_messages(SceneGraph.model_validate(variant["graph"]), checker_for(window))
    row = {"messages": messages, "variant": variant["variant_id"], "window": window.window_id}
    if target and target["target"]:
        row["target"] = target["target"]
    return row


def _dataset_rows(run: pathlib.Path, windows: dict[str, Window], splits: dict[str, str]) -> dict[str, list]:
    targets = {row["variant_id"]: row for row in _rows(run / "targets.jsonl")}
    out: dict[str, list[dict]] = {"sft": [], "rl": [], "heldout": []}
    for variant in _rows(run / "variants.jsonl"):
        if not variant["variant_id"] or variant["variant_id"] not in targets:
            continue
        split = splits[variant["window_id"]]
        row = _prompt_row(variant, windows[variant["window_id"]], targets[variant["variant_id"]])
        if split == HELDOUT:
            out["heldout"].append({key: value for key, value in row.items() if key != "target"})
        elif split == TRAIN:
            out["rl"].append({key: value for key, value in row.items() if key != "target"})
            if "target" in row:
                out["sft"].append({"messages": [*row["messages"], {"role": "assistant", "content": row["target"]}],
                                   "variant": row["variant"], "window": row["window"]})
    return out


def correction_rows(variant: dict, window: Window, max_steps: int = MAX_CORRECTION_STEPS) -> list[dict]:
    """SFT prefixes for accepted search moves and the checker feedback between them.

    This checker trusts uncertain scan geometry for training; these labels are
    not claims that a physical shop was measured or verified.
    """
    graph, checker = SceneGraph.model_validate(variant["graph"]), checker_for(window)
    baseline = graph
    messages = prompt_messages(graph, checker)
    rows = []
    for step in range(1, max_steps + 1):
        edits = edits_between(graph, searched_layout(graph, checker, rounds=1))
        if not edits.moves:
            break
        target = edits_json(edits)
        verdict = score_completion(target, graph, checker)
        if not verdict.gate_accepts:
            break
        candidate = apply_moves(graph, node_moves(edits))
        step_usability = usability(graph, candidate, graph, checker.scenario)
        baseline_usability = usability(baseline, candidate, baseline, checker.scenario)
        answer = {"role": "assistant", "content": target}
        rows.append({"messages": [*messages, answer], "variant": variant["variant_id"],
                     "window": window.window_id, "step": step, "all_fixable_cleared": verdict.fixable_left == 0,
                     "baseline_usability": baseline_usability, "checker_scope": "trusted_geometry"})
        graph = candidate
        if verdict.fixable_left == 0:
            break
        messages = [*messages, answer, feedback_message(
            graph, checker, accepted=True, reason=verdict.reason, fixable_left=verdict.fixable_left,
            parsed=verdict.parsed, hard_constraints_pass=verdict.hard_constraints_pass,
            step_usability=step_usability, candidate_baseline_usability=baseline_usability,
            current_baseline_usability=baseline_usability,
        )]
    return rows


def _replay_trace_attempt(attempt: dict, graph: SceneGraph, baseline: SceneGraph,
                          checker: TrainingChecker, current_usability: float) -> tuple[SceneGraph, dict, float]:
    completion = attempt["completion"]
    verdict = score_completion(completion, graph, checker)
    if verdict.gate_accepts != attempt["accepted"]:
        raise ValueError("Trace acceptance differs from checker")
    step_usability = candidate_usability = None
    if verdict.gate_accepts:
        edits = parse_edits(completion)
        candidate = apply_moves(graph, node_moves(edits))
        step_usability = usability(graph, candidate, graph, checker.scenario)
        candidate_usability = usability(baseline, candidate, baseline, checker.scenario)
        graph, current_usability = candidate, candidate_usability
    feedback = feedback_message(
        graph, checker, accepted=verdict.gate_accepts, reason=verdict.reason,
        fixable_left=len(checker.fixable_problems(checker.assess(graph))), parsed=verdict.parsed,
        hard_constraints_pass=verdict.hard_constraints_pass, step_usability=step_usability,
        candidate_baseline_usability=candidate_usability, current_baseline_usability=current_usability,
    )
    if json.loads(feedback["content"]) != attempt["feedback"]:
        raise ValueError("Trace feedback differs from checker")
    return graph, feedback, current_usability


def _validate_trace_header(variant: dict, window: Window, trace: dict, checker: TrainingChecker,
                           graph: SceneGraph) -> None:
    expected = {"variant": variant["variant_id"], "window_id": window.window_id,
                "scan_id": variant["scan_id"],
                "baseline_fixable_left": len(checker.fixable_problems(checker.assess(graph)))}
    if any(trace.get(key) != value for key, value in expected.items()):
        raise ValueError(f"Trace room metadata does not match {variant['variant_id']}")
    if len(trace["attempts"]) > MAX_CORRECTION_STEPS:
        raise ValueError("Trace exceeds the five-attempt limit")


def trace_correction_rows(variant: dict, window: Window, trace: dict) -> list[dict]:
    """Pair actual train-room attempts with an accepted next move from search.

    The recorded feedback is the exact user message the model saw. Replay
    accepted moves through the checker so old or inconsistent traces fail fast.
    """
    graph, checker = SceneGraph.model_validate(variant["graph"]), checker_for(window)
    _validate_trace_header(variant, window, trace, checker, graph)
    baseline = graph
    current_usability = 1.0
    messages = prompt_messages(graph, checker)
    rows = []
    for index, attempt in enumerate(trace["attempts"][:MAX_CORRECTION_STEPS - 1], start=1):
        if attempt["index"] != index:
            raise ValueError("Trace attempts are out of order")
        completion = attempt["completion"]
        graph, feedback, current_usability = _replay_trace_attempt(
            attempt, graph, baseline, checker, current_usability,
        )
        messages = [*messages, {"role": "assistant", "content": completion}, feedback]
        if not checker.fixable_problems(checker.assess(graph)):
            break
        edits = edits_between(graph, searched_layout(graph, checker, rounds=1))
        if not edits.moves:
            continue
        target = edits_json(edits)
        target_verdict = score_completion(target, graph, checker)
        if target_verdict.gate_accepts:
            rows.append({"messages": [*messages, {"role": "assistant", "content": target}],
                         "variant": variant["variant_id"], "window": window.window_id,
                         "after_attempt": attempt["index"], "checker_scope": "trusted_geometry"})
    return rows


def run_trace_corrections(run: pathlib.Path, trace_path: pathlib.Path) -> int:
    training = {row["variant"] for row in _rows(run / "dataset/rl.jsonl")}
    variants = {row["variant_id"]: row for row in _rows(run / "variants.jsonl") if row["variant_id"] in training}
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(run / "windows.jsonl")}
    rows = []
    seen = set()
    for trace in _rows(trace_path):
        variant_id = trace["variant"]
        if variant_id not in variants or variant_id in seen:
            raise ValueError(f"Unknown, held-out, or duplicate trace variant: {variant_id}")
        seen.add(variant_id)
        variant = variants[variant_id]
        rows.extend(trace_correction_rows(variant, windows[variant["window_id"]], trace))
    output = run / "dataset/corrections_from_trace.jsonl"
    temporary = output.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(row) + "\n" for row in rows))
    os.replace(temporary, output)
    return len(rows)


def _correction_task(variant: dict) -> tuple[str, list[dict]]:
    return variant["variant_id"], correction_rows(variant, _WINDOWS[variant["window_id"]])


def _correction_records(variants: list[dict], windows: dict[str, Window], run: pathlib.Path, workers: int):
    if workers == 1:
        for variant in variants:
            yield variant["variant_id"], correction_rows(variant, windows[variant["window_id"]])
        return
    context = multiprocessing.get_context("spawn")
    with context.Pool(workers, initializer=_load_windows, initargs=(str(run),), maxtasksperchild=20) as pool:
        yield from pool.imap_unordered(_correction_task, variants)


def run_corrections(run: pathlib.Path, progress: Progress, workers: int = 1) -> None:
    """Build a separate train-only SFT file; leave the one-shot files untouched."""
    dataset = run / "dataset"
    if not (dataset / "rl.jsonl").exists():
        raise FileNotFoundError("Build the dataset split before correction rows")
    training = {row["variant"] for row in _rows(dataset / "rl.jsonl")}
    variants = [row for row in _rows(run / "variants.jsonl") if row["variant_id"] in training]
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(run / "windows.jsonl")}
    output, log = dataset / "corrections.jsonl", dataset / "corrections_log.jsonl"
    recorded = {row["variant"]: row["steps"] for row in _rows(log)}
    existing = _rows(output)
    counts = Counter(row["variant"] for row in existing)
    done = {variant for variant, steps in recorded.items() if counts[variant] == steps}
    retained = [row for row in existing if row["variant"] in done]
    if len(retained) != len(existing):
        temporary = output.with_suffix(".jsonl.tmp")
        temporary.write_text("".join(json.dumps(row) + "\n" for row in retained))
        os.replace(temporary, output)
    progress.stage("corrections", len(variants), len(done))
    todo = [variant for variant in variants if variant["variant_id"] not in done]
    for variant_id, rows in _correction_records(todo, windows, run, workers):
        for row in rows:
            _append(output, row)
        _append(log, {"variant": variant_id, "steps": len(rows),
                      "fully_cleared": bool(rows and rows[-1]["all_fixable_cleared"])})
        progress.tick("corrections")
    progress.finish("corrections", rows=len(_rows(output)))


def run_split(run: pathlib.Path, progress: Progress) -> dict:
    windows = {row["window_id"]: Window.from_dict(row) for row in _rows(run / "windows.jsonl")}
    exports, skipped = _used_exports(run)
    names = {export.scan_id: export.name for export in exports}
    rooms = [room_record(window) for window in windows.values()]
    held = hold_out(rooms, names)
    splits = assign(rooms, held)
    dataset = run / "dataset"
    dataset.mkdir(exist_ok=True)
    for name, rows in _dataset_rows(run, windows, splits).items():
        (dataset / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = _report(run, windows, splits, held, names, skipped, rooms)
    _write_json(run / "report.json", report)
    progress.state["stages"]["split"] = {"status": "done", "finished": time.time(), **report["split"]["totals"]}
    _write_json(progress.path, progress.state)
    return report


def _window_table(windows: dict[str, Window], splits: dict[str, str], names: dict[str, str]) -> list[dict]:
    return [{"window_id": w.window_id, "scan": names[w.scan_id], "split": splits[w.window_id], "route": w.route,
             "objects": w.object_count(), "movable": w.movable_count(), "seed": w.seed and w.seed["check"]}
            for w in windows.values()]


def _report(run, windows, splits, held, names, skipped, rooms) -> dict:
    targets = _rows(run / "targets.jsonl")
    by_split = {split: [t for t in targets if splits.get(t["window_id"]) == split] for split in (TRAIN, HELDOUT)}
    variants = [row for row in _rows(run / "variants.jsonl") if row["variant_id"]]
    return {
        "skipped_scans": skipped,
        "split": {**split_report(rooms, splits, names),
                  "held_out": {"scans": sorted(names[s] for s in held.scans),
                               "floors": [[names[s], f] for s, f in sorted(held.floors)]}},
        "windows": _window_table(windows, splits, names),
        "variants": {"total": len(variants), "by_origin": dict(Counter("owner" if r["name"] == "owner" else "scrambled"
                                                                       for r in variants))},
        "targets": {"all": target_quality(targets), **{s: target_quality(rows) for s, rows in by_split.items()}},
        "dropped_windows": sorted(w for w, s in splits.items() if s == DROPPED),
        "windows_dropped_for_phantoms": [row for row in _rows(run / "windows_log.jsonl")
                                         if str(row.get("why", "")).startswith("phantoms are")],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("export", "windows", "variants", "targets", "split",
                                          "corrections", "trace-corrections", "all"))
    parser.add_argument("--database", type=pathlib.Path, default=ROOT / "services/api/var/standardphysics.sqlite3")
    parser.add_argument("--run", type=pathlib.Path, default=DEFAULT_RUN)
    parser.add_argument("--workers", type=int, default=max(1, math.floor((os.cpu_count() or 2) * 0.75)))
    parser.add_argument("--trace", type=pathlib.Path, help="five-loop train-room JSONL for correction targets")
    args = parser.parse_args()
    args.run.mkdir(parents=True, exist_ok=True)
    if args.stage == "export":
        run_export(args.database, args.run)
        return
    progress = Progress(args.run / "progress.json")
    stages = {"windows": run_windows, "variants": run_variants, "targets": run_targets}
    for name in (("windows", "variants", "targets") if args.stage == "all" else (args.stage,)):
        if name in stages:
            _log(f"stage {name}")
            stages[name](args.run, args.workers, progress)
    if args.stage in ("split", "all"):
        print(json.dumps(run_split(args.run, progress)["targets"], indent=2))
    if args.stage == "corrections":
        run_corrections(args.run, progress, args.workers)
    if args.stage == "trace-corrections":
        if args.trace is None:
            parser.error("trace-corrections requires --trace")
        _log(f"train-trace correction rows: {run_trace_corrections(args.run, args.trace)}")


if __name__ == "__main__":
    main()
