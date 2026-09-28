"""The fine-tuning story notebook runs, and every figure it shows adds up from the ledger.

`notebooks/finetune_story.py` is exported to a static page for demos, so a figure
that drifts from `notebooks/public/finetune_ledger.json` would be shown to people as fact.
These tests run every cell and check the totals against the ledger directly.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "finetune_story.py"
LEDGER = json.loads((ROOT / "notebooks" / "public" / "finetune_ledger.json").read_text())


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ran():
    pytest.importorskip("marimo", reason='needs the notebook extra: pip install -e "packages/agents[notebook]"')
    return load(NOTEBOOK, "finetune_story").app.run()[1]


@pytest.fixture(scope="module")
def collector():
    return load(ROOT / "scripts" / "finetune_ledger.py", "finetune_ledger")


class TestTheTotals:
    def test_adapters_are_the_fireworks_list(self, ran):
        assert ran["TOTALS"]["adapters"] == len(LEDGER["adapters"])

    def test_spend_and_tokens_sum_every_run(self, ran):
        spend = [run["spend"] for run in LEDGER["runs"]]
        assert ran["TOTALS"]["dollars"] == pytest.approx(sum(entry["estimated_dollars"] for entry in spend))
        assert ran["TOTALS"]["train_tokens"] == sum(entry["train_tokens"] for entry in spend)

    def test_rl_steps_count_only_steps_that_ran(self, ran):
        assert ran["TOTALS"]["rl_steps"] == sum(run["training"].get("rl_steps_done") or 0 for run in LEDGER["runs"])

    def test_the_acceptance_sentence_counts_the_runs_it_describes(self, ran):
        moves = ran["ACCEPTANCE"]
        assert moves["rose"] + moves["fell"] <= moves["compared"]


class TestThePage:
    def test_every_run_has_a_card_and_a_panel(self, ran):
        page = ran["run_picker"](ran["RUNS"])
        for run in LEDGER["runs"]:
            assert f'id="pick-{run["key"]}"' in page
            assert f'data-run="{run["key"]}"' in page

    def test_only_the_first_run_starts_selected(self, ran):
        assert ran["run_picker"](ran["RUNS"]).count(" checked") == 1

    def test_the_charts_name_themselves_for_a_screen_reader(self, ran):
        for chart in (ran["lineage"](ran["RUNS"]), ran["REWARD_CHART"], ran["CLEARANCE_CHART"], ran["LOOP_CHART"]):
            assert "<title" in chart and 'role="img"' in chart


class TestTheCollector:
    def test_a_fraction_reads_from_a_report_line(self, collector):
        assert collector.cleared_fraction("39/51 when stopped") == {"cleared": 39, "of": 51}

    def test_an_rl_checkpoint_label_names_its_step(self, collector):
        assert collector.eval_label("eval_a-step8") == "After RL step 8"
        assert collector.eval_label("eval_sft") == "After SFT"

    def test_a_resumed_run_grows_from_the_run_that_saved_its_state(self, collector):
        parent_id, child_id = "run-" + "a" * 32, "run-" + "b" * 32
        runs = [
            {"key": "first", "sessions": [{"run_id": parent_id, "from_state": None}]},
            {"key": "second", "sessions": [{"run_id": child_id, "from_state": f"team/{parent_id}/rl-state-0024"}]},
        ]
        collector.link_parents(runs)
        assert [run["parent"] for run in runs] == [None, "first"]
