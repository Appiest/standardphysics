"""Training rows for choosing from a blind menu, and the reward that scores a choice.

Each row is one room's first menu, shown the way the "can the model read the
owner?" arm shows it: options shuffled and no inferred-wish labels
(`MenuView(order="shuffled", wishes_shown=False)`). Every option, and every
compatible pair of the best few, is scored by `score_completion`, whose reward
includes the share of the owner's hidden wishes kept. The SFT answer is the
best-scoring pick; RL samples choices and pays the same reward, so the model
is trained to prefer what the checker and the owner's own layout prefer, from
the menu and the room alone.

    python menu_data.py build --train-data runs/finetune/synthetic-20260924 --heldout-data ../valshops \\
        --out runs/finetune/menu-blind --train-rooms 240 --workers 6

The menu's options are stored with each row, so scoring a reply never rebuilds
the menu: the stored edits are resolved exactly as the five-loop resolves them.
"""

from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing
import pathlib
import zlib
from dataclasses import dataclass, field

from multiroom_train_data import MultiroomData
from multiroom_train_data import load as load_multiroom
from standardphysics_agents.training import Verdict, score_completion
from standardphysics_agents.training.edits import TrainingEdits, edits_json
from standardphysics_agents.training.menu import (
    Menu,
    MenuChoice,
    MenuView,
    Option,
    build_menu,
    menu_messages,
    resolve,
    resolve_choice,
)

PAIR_FROM_BEST = 4
"""Pairs are tried among this many best single options; more options rarely combine into a better pair."""


def _view(variant: str) -> MenuView:
    return MenuView(order="shuffled", wishes_shown=False, seed=zlib.crc32(variant.encode()))


def _score(room, checker, menu: Menu, picks: list[int]) -> float:
    resolution = resolve_choice(room, menu, MenuChoice(choose=picks))
    return score_completion(resolution.completion, room, checker).reward if resolution.applied else 0.0


def _best_pick(room, checker, menu: Menu) -> tuple[list[int], dict]:
    """The single option or compatible pair the checker pays most for, and every score it measured."""
    singles = {option.number: _score(room, checker, menu, [option.number]) for option in menu.options}
    ranked = sorted(singles, key=singles.get, reverse=True)[:PAIR_FROM_BEST]
    pairs = {(a, b): _score(room, checker, menu, [a, b]) for a, b in itertools.combinations(ranked, 2)
             if b not in menu.option(a).effect.get("clashes_with", [])}
    scores = {**{(number,): reward for number, reward in singles.items()}, **pairs}
    best = max(scores, key=lambda picks: (scores[picks], -len(picks)))
    return list(best), {",".join(map(str, picks)): round(reward, 4) for picks, reward in scores.items()}


def _why(menu: Menu, picks: list[int]) -> str:
    cleared = sorted({label for number in picks for label in menu.option(number).effect.get("clears", [])})
    inches = sum(menu.option(number).effect.get("inches_moved", 0) for number in picks)
    moving = f" with {inches:.0f} in of moving" if inches >= 1 else ""
    if cleared:
        return f"It clears {', '.join(cleared)}{moving} and keeps the owner's layout as it stands."
    return f"It improves the room the most{moving} and keeps the owner's layout as it stands."


def menu_row(data: MultiroomData, variant: str, source: str) -> dict | None:
    window = data.variants[variant]["window_id"]
    checker, room = data.checker(window), data.graph(variant)
    menu = build_menu(room, checker, view=_view(variant))
    if not menu.options:
        return None
    picks, scores = _best_pick(room, checker, menu)
    target = {"choose": picks, "why": _why(menu, picks)}
    return {"variant": variant, "source": source, "messages": menu_messages(room, checker, menu, None),
            "options": [{"number": option.number, "wording": option.wording, "edits": edits_json(option.edits),
                         "clashes_with": option.effect.get("clashes_with", [])} for option in menu.options],
            "target": target, "best_reward": scores[",".join(map(str, picks))], "scores": scores}


@dataclass
class _Job:
    data_dir: str
    source: str
    variant: str


_LOADED: dict[str, MultiroomData] = {}


def _data(path: str) -> MultiroomData:
    if path not in _LOADED:
        _LOADED[path] = load_multiroom(pathlib.Path(path))
    return _LOADED[path]


def _run(job: _Job) -> dict | None:
    try:
        return menu_row(_data(job.data_dir), job.variant, job.source)
    except Exception as error:  # a room the menu cannot be built for is skipped, never the whole build
        return {"variant": job.variant, "source": job.source, "error": repr(error)}


def _done(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["variant"] for line in path.read_text().splitlines() if line.strip()}


def _jobs(data_dir: pathlib.Path, split: str, count: int | None, done: set[str]) -> list[_Job]:
    data = _data(str(data_dir))
    rows = data.heldout if split == "heldout" else data.rl
    seen_windows, picked = set(), []
    for row in rows:
        window = data.variants[row["variant"]]["window_id"]
        if split == "train" and window in seen_windows:
            continue
        seen_windows.add(window)
        picked.append(row["variant"])
    picked = picked[:count] if count else picked
    return [_Job(str(data_dir), split, variant) for variant in picked if variant not in done]


def build(train_data: pathlib.Path, heldout_data: pathlib.Path, out: pathlib.Path, train_rooms: int,
          workers: int) -> None:
    """Heldout rows first, then one row per training window, appended as each finishes so a rerun resumes."""
    out.mkdir(parents=True, exist_ok=True)
    for split, data_dir, count in (("heldout", heldout_data, None), ("train", train_data, train_rooms)):
        path = out / f"{split}.jsonl"
        jobs = _jobs(data_dir, split, count, _done(path))
        with multiprocessing.get_context("spawn").Pool(workers, maxtasksperchild=8) as pool, path.open("a") as handle:
            for finished, row in enumerate(pool.imap_unordered(_run, jobs), start=1):
                handle.write(json.dumps(row or {"variant": None}) + "\n")
                handle.flush()
                print(f"{split} {finished}/{len(jobs)}", flush=True)


# --- loading, for serverless_train.py and the local trainer ---------------------


def _menu_of(row: dict) -> Menu:
    options = [Option(item["number"], item["wording"], TrainingEdits.model_validate_json(item["edits"]),
                      {"clashes_with": item.get("clashes_with", [])}) for item in row["options"]]
    return Menu(problems={}, options=options)


def assistant_turn(row: dict) -> dict:
    return {"role": "assistant", "content": json.dumps(row["target"], separators=(",", ":"))}


@dataclass
class MenuData:
    """Menu rows over the rooms they came from; `score` resolves a reply against the row's own menu."""

    rooms: dict[str, MultiroomData]
    rows: dict[str, dict]
    sft: list[dict] = field(default_factory=list)
    rl: list[dict] = field(default_factory=list)
    heldout: list[dict] = field(default_factory=list)

    def score(self, completion: str, variant: str) -> Verdict:
        row = self.rows[variant]
        data = self.rooms[row["source"]]
        checker, room = data.checker(data.variants[variant]["window_id"]), data.graph(variant)
        menu = _menu_of(row)
        menu = Menu(problems={}, options=menu.options, veto=checker.directive_veto(room))
        return score_completion(resolve(completion, room, menu, checker.pinned).completion, room, checker)


def _usable(path: pathlib.Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    return [row for row in rows if row.get("variant") and "error" not in row]


def load(menu_dir: pathlib.Path, train_data: pathlib.Path | None = None,
         heldout_data: pathlib.Path | None = None) -> MenuData:
    """The rows under `menu_dir`; room datasets default to the paths recorded in `menu_dir/sources.json`."""
    sources = json.loads((menu_dir / "sources.json").read_text())
    rooms = {"train": load_multiroom(train_data or pathlib.Path(sources["train"])),
             "heldout": load_multiroom(heldout_data or pathlib.Path(sources["heldout"]))}
    train, heldout = _usable(menu_dir / "train.jsonl"), _usable(menu_dir / "heldout.jsonl")
    sft = [{**row, "messages": [*row["messages"], assistant_turn(row)]} for row in train if row["best_reward"] > 0]
    return MenuData(rooms=rooms, rows={row["variant"]: row for row in [*train, *heldout]},
                    sft=sft, rl=[row for row in train if row["best_reward"] > 0], heldout=heldout)


def export_mlx(menu_dir: pathlib.Path, out: pathlib.Path, valid_share: float = 0.1) -> None:
    """The SFT rows as mlx_lm chat JSONL: train.jsonl and valid.jsonl."""
    train = [row for row in _usable(menu_dir / "train.jsonl") if row["best_reward"] > 0]
    cut = max(1, int(len(train) * valid_share))
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("valid", train[:cut]), ("train", train[cut:])):
        (out / f"{name}.jsonl").write_text("".join(
            json.dumps({"messages": [*row["messages"], assistant_turn(row)]}) + "\n" for row in rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--train-data", type=pathlib.Path, required=True)
    make.add_argument("--heldout-data", type=pathlib.Path, required=True)
    make.add_argument("--out", type=pathlib.Path, required=True)
    make.add_argument("--train-rooms", type=int, default=240)
    make.add_argument("--workers", type=int, default=6)
    mlx = commands.add_parser("export-mlx")
    mlx.add_argument("--menu", type=pathlib.Path, required=True)
    mlx.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.command == "export-mlx":
        return export_mlx(args.menu, args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sources.json").write_text(json.dumps({"train": str(args.train_data.resolve()),
                                                       "heldout": str(args.heldout_data.resolve())}))
    build(args.train_data, args.heldout_data, args.out, args.train_rooms, args.workers)


if __name__ == "__main__":
    main()
