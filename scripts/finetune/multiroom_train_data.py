"""Load the built multi-room dataset back for training: prompt rows and a scorer per window.

Same shape as `room6_data.Room6Data` (`sft`, `rl`, `heldout`, `score`), so the
serverless orchestrator can train on either. Every variant is scored against
its own window's checker: its route, its pinned phantoms and its owner layout.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from multiroom_data import _rows, checker_for
from standardphysics_agents.training import TrainingChecker, Verdict, score_completion
from standardphysics_agents.training.windows import Window
from standardphysics_contracts import SceneGraph


@dataclass
class MultiroomData:
    windows: dict[str, Window]
    variants: dict[str, dict]
    sft: list[dict]
    rl: list[dict]
    heldout: list[dict]
    _checkers: dict[str, TrainingChecker] = field(default_factory=dict)
    _graphs: dict[str, SceneGraph] = field(default_factory=dict)

    def checker(self, window_id: str) -> TrainingChecker:
        if window_id not in self._checkers:
            self._checkers[window_id] = checker_for(self.windows[window_id])
        return self._checkers[window_id]

    def graph(self, variant_id: str) -> SceneGraph:
        if variant_id not in self._graphs:
            self._graphs[variant_id] = SceneGraph.model_validate(self.variants[variant_id]["graph"])
        return self._graphs[variant_id]

    def score(self, completion: str, variant_id: str) -> Verdict:
        window_id = self.variants[variant_id]["window_id"]
        return score_completion(completion, self.graph(variant_id), self.checker(window_id))

    def scan_of(self, variant_id: str) -> str:
        return self.variants[variant_id]["scan_id"]


def load(run: pathlib.Path) -> MultiroomData:
    dataset = run / "dataset"
    return MultiroomData(
        windows={row["window_id"]: Window.from_dict(row) for row in _rows(run / "windows.jsonl")},
        variants={row["variant_id"]: row for row in _rows(run / "variants.jsonl") if row["variant_id"]},
        sft=_rows(dataset / "sft.jsonl"),
        rl=_rows(dataset / "rl.jsonl"),
        heldout=_rows(dataset / "heldout.jsonl"),
    )
