"""Budget, room coverage, and held-out aggregation for the overnight training run."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from multiroom_results import build, composition, metrics, search_records
from multiroom_train_data import MultiroomData
from progress import Progress, Spend
from serverless_train import Plan, expected_cost, promote_optional, rl_rows_for_step, run_rl_phase, run_sft_phase


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_pessimistic_estimate_uses_uncached_maximum_lengths():
    data = SimpleNamespace(sft=[{}] * 3, rl=[{}] * 8, heldout=[{}] * 2)
    plan = Plan(sft_epochs=2, rl_steps=3, rl_prompts_per_step=2, rl_group_size=4,
                eval_samples=2, max_sample_tokens=512)
    estimate = expected_cost(plan, data, prompt_tokens=3000, sft_tokens=3400)
    assert estimate["sft_rows"] == 6
    assert estimate["rl_rollouts"] == 24
    assert estimate["eval_samples"] == 12
    assert estimate["prefill_tokens"] == 36 * 3000
    assert estimate["sample_tokens"] == 36 * 512
    assert estimate["train_tokens"] == 6 * 3400 + 24 * 3512
    assert estimate["estimated_dollars"] == Spend(prefill_tokens=108000, sample_tokens=18432,
                                                  train_tokens=104688).as_dict()["estimated_dollars"]


def test_rl_batches_cover_each_room_before_repeating():
    rows = [{"window": f"room-{index}", "variant": f"variant-{index}"} for index in range(19)]
    picked = [row for step in range(4) for row in rl_rows_for_step(rows, step, 6)]
    assert {row["window"] for row in picked} == {row["window"] for row in rows}


def test_failed_sft_promotion_does_not_block_rl_resume(tmp_path):
    progress = Progress(tmp_path / "progress.json")
    progress.record("sft", status="done", state_ref="account/run/sft-state")
    progress.record("eval_sft", status="done", summary={})
    progress.record("promote_sft", status="running")
    client = SimpleNamespace(save_weights_for_sampler=Mock(return_value=SimpleNamespace(result=Mock())))
    trainer = SimpleNamespace(progress=progress, plan=Plan(), client=None, connect=Mock(),
                              evaluate=Mock(), promote=Mock(side_effect=RuntimeError("HTTP 409: model already exists")),
                              rl=Mock())
    trainer.connect.side_effect = lambda *args, **kwargs: setattr(trainer, "client", client)

    run_sft_phase(trainer)
    run_rl_phase(trainer)

    assert progress.get("promote_sft")["status"] == "failed"
    assert "HTTP 409" in progress.get("promote_sft")["error"]
    trainer.rl.assert_called_once_with(first_step=0)
    assert progress.get("promote_rl")["status"] == "failed"
    run_sft_phase(trainer)
    assert trainer.promote.call_count == 2


def test_sft_starts_from_initial_state_when_no_local_checkpoint(tmp_path):
    progress = Progress(tmp_path / "progress.json")
    trainer = SimpleNamespace(progress=progress, plan=Plan(initial_state="account/run/rl-state"),
                              connect=Mock(), evaluate=Mock(), sft=Mock(), promote=Mock(return_value="model"))

    run_sft_phase(trainer)

    trainer.connect.assert_called_once_with("account/run/rl-state", with_optimizer=False)
    trainer.sft.assert_called_once_with()


def test_optional_promotion_records_success(tmp_path):
    progress = Progress(tmp_path / "progress.json")
    trainer = SimpleNamespace(progress=progress, promote=Mock(return_value="account/model"))
    promote_optional(trainer, "promote_rl", "rl-final", "multiroom-rl")
    assert progress.get("promote_rl")["model"] == "account/model"
    assert progress.done("promote_rl")


def test_metrics_count_rejected_samples_and_log_u_and_q_only_when_accepted():
    records = [
        {"parsed": True, "hard_constraints_pass": True, "gate_accepts": True, "fixable_left": 0,
         "shortfall_recovered": 0.8, "usability": 0.7, "quality": {"q": 0.4}, "reward": 0.6},
        {"parsed": False, "hard_constraints_pass": False, "gate_accepts": False,
         "shortfall_recovered": 0.0, "reward": 0.0},
    ]
    result = metrics(records)
    assert result == {"samples": 2, "parse_rate": 0.5, "hard_constraint_pass_rate": 0.5,
                      "gate_acceptance": 0.5, "mean_shortfall_recovered": 0.4,
                      "share_clearing_every_fixable": 0.5, "mean_usability_accepted": 0.7,
                      "mean_quality_accepted_logged_only": 0.4, "mean_reward": 0.3}
    assert metrics([])["parse_rate"] is None


def test_search_reference_uses_search_not_put_back(tmp_path):
    write_rows(tmp_path / "targets.jsonl", [{"variant_id": "v", "source": "put_back",
        "verdict": {"reward": 0.9}, "search": {"verdict": {"reward": 0.2}}}])
    assert search_records(tmp_path, ["v"])[0]["reward"] == 0.2
    assert search_records(tmp_path, ["missing"])[0]["reward"] == 0.0


def test_results_group_ravida_by_window_not_scan_prefix(tmp_path):
    data, run = tmp_path / "data", tmp_path / "run"
    data.mkdir()
    write_rows(data / "variants.jsonl", [
        {"variant_id": "v1", "window_id": "ravida-scan:whole", "scan_id": "ravida-scan"},
        {"variant_id": "v2", "window_id": "library:w01", "scan_id": "library-scan"},
    ])
    write_rows(data / "windows_log.jsonl", [{"kept": True}, {"kept": False, "why": "too small"}])
    write_rows(data / "targets.jsonl", [])
    for name in ("sft", "rl"):
        write_rows(data / "dataset" / f"{name}.jsonl", [])
    write_rows(data / "dataset" / "heldout.jsonl", [{"variant": "v1"}, {"variant": "v2"}])
    (data / "report.json").write_text(json.dumps({
        "windows": [{"window_id": "ravida-scan:whole", "scan": "ravida"},
                    {"window_id": "library:w01", "scan": "A-102"}],
        "split": {"totals": {"heldout": 2}, "by_scan": {}, "held_out": {}},
        "variants": {"total": 2}, "targets": {},
    }))
    write_rows(run / "eval" / "base.jsonl", [
        {"variant": "v1", "reward": 1.0, "gate_accepts": True, "parsed": True,
         "hard_constraints_pass": True, "shortfall_recovered": 1.0, "fixable_left": 0},
        {"variant": "v2", "reward": 0.0, "gate_accepts": False, "parsed": False,
         "hard_constraints_pass": False, "shortfall_recovered": 0.0},
    ])
    assert composition(data)["windows_not_kept_by_reason"] == {"too small": 1}
    result = build(data, run, tmp_path / "progress.json")
    assert result["models"]["base"]["ravida"]["samples"] == 1
    assert result["models"]["base"]["ravida"]["mean_reward"] == 1.0
    assert result["models"]["base"]["heldout"]["mean_reward"] == 0.5
    assert (run / "answers" / "base.jsonl").exists()


def test_multiroom_score_uses_variant_window_checker():
    data = MultiroomData(windows={}, variants={"v": {"window_id": "w"}}, sft=[], rl=[], heldout=[])
    data.graph = lambda variant: "graph"
    data.checker = lambda window: "checker"
    from unittest.mock import patch

    with patch("multiroom_train_data.score_completion", return_value="verdict") as score:
        assert data.score("answer", "v") == "verdict"
    score.assert_called_once_with("answer", "graph", "checker")
