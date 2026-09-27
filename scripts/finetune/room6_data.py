"""Load the built room 6 dataset back: the checker, the variants, the prompt rows."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from standardphysics_agents.training import TrainingChecker, Verdict, score_completion
from standardphysics_contracts import Scenario, SceneGraph


@dataclass
class Room6Data:
    checker: TrainingChecker
    variants: dict[str, SceneGraph]
    sft: list[dict]
    rl: list[dict]
    heldout: list[dict]

    def score(self, completion: str, variant: str) -> Verdict:
        return score_completion(completion, self.variants[variant], self.checker)


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load(data_dir: pathlib.Path) -> Room6Data:
    context = json.loads((data_dir / "context.json").read_text())
    variants = {row["variant"]: SceneGraph.model_validate(row["graph"]) for row in _rows(data_dir / "variants.jsonl")}
    return Room6Data(
        checker=TrainingChecker(Scenario.model_validate(context["scenario"])),
        variants=variants,
        sft=_rows(data_dir / "sft.jsonl"),
        rl=_rows(data_dir / "rl.jsonl"),
        heldout=_rows(data_dir / "heldout.jsonl"),
    )
