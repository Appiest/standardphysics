"""PROGRESS_FINETUNE.json: what has run, what it cost, and how to resume.

Rewritten atomically after every step, so a killed run leaves a readable file
and a rerun skips whatever already finished.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass, field

QWEN3P8_27B_SERVERLESS_RATES = {"prefill": 1.86, "cached": 0.372, "sample": 5.595, "train": 4.103}
"""US dollars per million tokens for Qwen3.8 27B serverless training, from the
Fireworks training cost estimator's published table, read 2026-09-23."""


@dataclass
class Spend:
    """A running, deliberately pessimistic total: every prompt token is billed as uncached."""

    rates: dict = field(default_factory=lambda: dict(QWEN3P8_27B_SERVERLESS_RATES))
    prefill_tokens: int = 0
    sample_tokens: int = 0
    train_tokens: int = 0

    @property
    def dollars(self) -> float:
        return (self.prefill_tokens * self.rates["prefill"] + self.sample_tokens * self.rates["sample"]
                + self.train_tokens * self.rates["train"]) / 1e6

    def as_dict(self) -> dict:
        return {"prefill_tokens": self.prefill_tokens, "sample_tokens": self.sample_tokens,
                "train_tokens": self.train_tokens, "estimated_dollars": round(self.dollars, 4)}


class Progress:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.state = json.loads(path.read_text()) if path.exists() else {"steps": {}, "jobs": []}

    def done(self, step: str) -> bool:
        return self.state["steps"].get(step, {}).get("status") == "done"

    def get(self, step: str) -> dict:
        return self.state["steps"].get(step, {})

    def record(self, step: str, **fields) -> None:
        entry = self.state["steps"].setdefault(step, {})
        entry.update(fields, updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.save()

    def set(self, key: str, value) -> None:
        self.state[key] = value
        self.save()

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2, default=str) + "\n")
        os.replace(temporary, self.path)
