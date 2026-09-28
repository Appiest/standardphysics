"""The dataset, the scorers, and the run that keeps its per-case results."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from standardphysics_agents.evaluation import (
    LOWER_IS_BETTER,
    SCORERS,
    dataset,
    evaluate,
    run_case,
    save,
)
from standardphysics_agents.evaluation.dataset import SCAN_CANNOT_SEE, by_id
from standardphysics_agents.evaluation.scorers import (
    CaseOutcome,
    finding_precision,
    finding_recall,
    loop_trajectory_ok,
    measurement_error_in,
    router_action_match,
    trajectory_ok,
)
from standardphysics_agents.router import LocalPolicyRouter, Rejected, state_for
from standardphysics_agents.router.state import REPEATED_ACTION
from standardphysics_fixtures.shop import node_id

EXPECTED_SCORERS = {
    "finding_precision",
    "finding_recall",
    "measurement_error_in",
    "label_accuracy",
    "router_action_match",
    "fix_resolves_finding",
}


@pytest.fixture(scope="module")
def evaluation(pack, ledger, pipeline):
    return evaluate(measure=pipeline, rules=pack, ledger=ledger, publish=False)


class TestTheDataset:
    def test_it_is_about_twenty_five_cases(self):
        assert 25 <= len(dataset()) <= 40

    def test_every_case_says_what_it_is_for(self):
        for case in dataset():
            assert len(case.description) > 40, case.id

    def test_case_ids_are_unique(self):
        ids = [case.id for case in dataset()]
        assert len(ids) == len(set(ids))

    def test_it_covers_clean_passes(self):
        clean = [c for c in dataset() if not c.expected_problems]
        assert len(clean) >= 5

    def test_it_covers_real_violations(self):
        broken = [c for c in dataset() if c.expected_problems]
        assert len(broken) >= 10

    def test_it_covers_thin_coverage(self):
        thin = [c for c in dataset() if c.expected_questions > SCAN_CANNOT_SEE]
        assert len(thin) >= 3

    def test_it_covers_ambiguous_objects(self):
        """A counter Astra called a cabinet, and a bar that is one anyway."""
        assert by_id("counter_mislabelled").expected_roles["service_counter"] == frozenset()
        assert by_id("counter_labelled_bar").expected_problems

    def test_it_covers_cases_where_asking_is_the_right_answer(self):
        asking = [c for c in dataset() if c.expected_action == "ASK_OWNER"]
        assert len(asking) >= 5

    def test_it_covers_every_router_action(self):
        wanted = {c.expected_action for c in dataset() if c.expected_action}
        assert wanted == {"FIX", "RESCAN_AREA", "ASK_OWNER", "ESCALATE", "DONE"}

    def test_every_case_asks_the_five_things_a_scan_cannot_see(self):
        for case in dataset():
            assert SCAN_CANNOT_SEE <= case.expected_questions, case.id

    def test_no_case_both_expects_and_forbids_the_same_check(self):
        for case in dataset():
            assert not (case.expected_problems & case.forbidden_problems), case.id


class TestBlockedRouteRouting:
    @pytest.mark.parametrize(
        ("case_id", "expected_action"),
        [("blocked_but_movable", "FIX"), ("blocked_solid", "ESCALATE")],
    )
    def test_blockers_reach_the_router(
        self, case_id, expected_action, pack, ledger, pipeline
    ):
        case = by_id(case_id)
        width = pipeline.route_clear_width(case.graph, case.scenario, 0)
        assert not width.reachable
        assert width.blocking_node_ids

        outcome = run_case(case, pipeline, pack, ledger, LocalPolicyRouter(), False)
        assert outcome.error is None
        state = state_for(outcome.result.findings, case.graph, pack)
        decision = outcome.decision
        assert decision is not None
        assert not isinstance(decision, Rejected)
        assert decision.action == expected_action

        if expected_action == "FIX":
            barriers = {node_id("bar_west"), node_id("bar_east")}
            assert set(width.blocking_node_ids) == barriers
            targets = [
                f for f in outcome.result.problems
                if f.id in decision.target_finding_ids
            ]
            assert targets
            for finding in targets:
                assert finding.locus is not None
                assert set(finding.locus.node_ids) == barriers
            assert set(decision.target_finding_ids) <= set(state.fixable_finding_ids)
        else:
            assert not state.fixable_finding_ids

        assert case.expected_action == expected_action
        assert router_action_match(outcome) == 1.0


class TestTheScorers:
    def test_the_plan_names_all_six_and_they_are_all_here(self):
        assert EXPECTED_SCORERS <= set(SCORERS)

    def test_measurement_error_is_the_one_where_lower_is_better(self):
        assert LOWER_IS_BETTER == {"measurement_error_in"}

    def test_a_case_with_nothing_to_say_scores_nothing(self, pack, ledger, pipeline):
        """A shop with no doorway must not drag down door accuracy."""
        case = by_id("clean_shop")
        outcome = run_case(case, pipeline, pack, ledger, LocalPolicyRouter(), False)
        assert finding_recall(outcome) is None
        assert measurement_error_in(outcome) is None

    def test_precision_counts_what_was_reported(self, pack, ledger, pipeline):
        case = by_id("aisle_31")
        outcome = run_case(case, pipeline, pack, ledger, LocalPolicyRouter(), False)
        assert finding_precision(outcome) == 1.0

    def test_a_rejected_decision_scores_zero_rather_than_nothing(
        self, pack, ledger, pipeline
    ):
        """An unusable answer is a wrong answer, not an absent one."""
        case = by_id("aisle_31")
        outcome = run_case(case, pipeline, pack, ledger, LocalPolicyRouter(), False)
        broken = CaseOutcome(
            case=outcome.case, result=outcome.result, decision=Rejected("not_json")
        )
        assert router_action_match(broken) == 0.0


class TestTheRun:
    def test_it_completes(self, evaluation):
        assert evaluation.completed
        assert evaluation.failures == []

    def test_every_case_is_scored(self, evaluation):
        assert set(evaluation.per_case) == {case.id for case in dataset()}

    def test_it_records_which_rule_pack_it_ran_against(self, evaluation, pack):
        assert evaluation.rulepack_version == pack.version

    def test_no_case_reports_a_check_it_forbids(self, evaluation):
        for outcome in evaluation.outcomes:
            offending = outcome.reported_problems & outcome.case.forbidden_problems
            assert not offending, f"{outcome.case.id} reported {sorted(offending)}"

    def test_every_expected_problem_is_reported(self, evaluation):
        for outcome in evaluation.outcomes:
            missing = outcome.case.expected_problems - outcome.reported_problems
            assert not missing, f"{outcome.case.id} missed {sorted(missing)}"

    def test_every_expected_question_is_asked(self, evaluation):
        for outcome in evaluation.outcomes:
            missing = outcome.case.expected_questions - outcome.reported_questions
            assert not missing, f"{outcome.case.id} missed {sorted(missing)}"

    def test_measurements_land_within_a_tenth_of_an_inch(self, evaluation):
        assert evaluation.score("measurement_error_in") < 0.1

    def test_the_router_picks_the_right_action_every_time(self, evaluation):
        assert evaluation.score("router_action_match") == 1.0

    def test_every_rearrangement_it_proposed_cleared_its_finding(self, evaluation):
        assert evaluation.score("fix_resolves_finding") == 1.0

    def test_a_check_never_attaches_to_the_wrong_thing(self, evaluation):
        assert evaluation.score("label_accuracy") == 1.0

    def test_a_case_that_raises_does_not_take_the_run_down(
        self, pack, ledger, pipeline
    ):
        class Exploding:
            provider = "exploding"

            def decide(self, state):
                raise RuntimeError("the router caught fire")

        result = evaluate(
            measure=pipeline, rules=pack, ledger=ledger, router=Exploding(),
            cases=[by_id("aisle_31")], run_fixes=False, publish=False,
        )
        assert not result.completed
        assert result.failures == ["aisle_31"]


class TestRetrievableResults:
    def test_per_case_results_go_to_disk(self, evaluation, tmp_path):
        path = save(evaluation, tmp_path / "runs" / "evaluation.json")
        saved = json.loads(path.read_text())
        assert saved["completed"]
        assert len(saved["cases"]) == len(dataset())

    def test_each_saved_row_carries_its_scores(self, evaluation, tmp_path):
        saved = json.loads(save(evaluation, tmp_path / "e.json").read_text())
        row = next(r for r in saved["cases"] if r["case"] == "aisle_31")
        assert row["finding_recall"] == 1.0
        assert row["expected_action"] == "FIX"
        assert row["problems"]

    def test_it_says_which_score_wants_to_be_small(self, evaluation, tmp_path):
        saved = json.loads(save(evaluation, tmp_path / "e.json").read_text())
        assert saved["lower_is_better"] == ["measurement_error_in"]

    def test_a_run_that_did_not_publish_says_so(self, evaluation):
        assert evaluation.evaluation_url is None

    def test_the_saved_record_says_where_the_evaluation_went(self, evaluation, tmp_path):
        saved = json.loads(save(evaluation, tmp_path / "e.json").read_text())
        assert "evaluation_url" in saved


class FakePrediction:
    def __init__(self, inputs, output) -> None:
        self.inputs, self.output = inputs, output
        self.scores: dict[str, float] = {}
        self.finished = False

    def log_score(self, scorer, score):
        assert not self.finished, "Weave refuses a score after finish"
        self.scores[scorer] = score

    def finish(self):
        self.finished = True


class FakeEvaluationLogger:
    ui_url = "https://wandb.ai/acme/physics/weave/calls/evaluation"

    def __init__(self, **config) -> None:
        self.config = config
        self.predictions: list[FakePrediction] = []
        self.summary: dict | None = None

    def log_prediction(self, inputs, output=None):
        prediction = FakePrediction(inputs, output)
        self.predictions.append(prediction)
        return prediction

    def log_summary(self, summary):
        self.summary = summary


class FakeWeave:
    """Enough of `weave.EvaluationLogger`, including a third party refusing."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.loggers: list[FakeEvaluationLogger] = []

    def EvaluationLogger(self, **config):  # noqa: N802 - matches weave.EvaluationLogger
        if self.failure is not None:
            raise self.failure
        logger = FakeEvaluationLogger(**config)
        self.loggers.append(logger)
        return logger


@pytest.fixture
def weave(monkeypatch):
    import sys

    from standardphysics_agents.evaluation import runner

    fake = FakeWeave()
    monkeypatch.setitem(sys.modules, "weave", fake)
    monkeypatch.setattr(runner, "is_live", lambda: True)
    return fake


def _one_case(pack, ledger, pipeline, **options):
    return evaluate(
        measure=pipeline, rules=pack, ledger=ledger,
        cases=[by_id("aisle_31")], run_fixes=False, **options,
    )


class TestTheEvaluationInWeave:
    """One eval per run, labelled with the router, so two routers compare."""

    def test_it_is_labelled_with_the_router_that_answered(self, pack, ledger, pipeline, weave):
        _one_case(pack, ledger, pipeline)
        assert weave.loggers[0].config == {
            "name": "standardphysics-loop",
            "model": "local_policy",
            "dataset": "standardphysics-cases",
        }

    def test_a_version_names_it_instead(self, pack, ledger, pipeline, weave):
        _one_case(pack, ledger, pipeline, version="typesafe-last-search")
        assert weave.loggers[0].config["model"] == "typesafe-last-search"

    def test_every_case_is_one_finished_prediction(self, pack, ledger, pipeline, weave):
        result = _one_case(pack, ledger, pipeline)
        predictions = weave.loggers[0].predictions
        assert [p.inputs for p in predictions] == [{"case": "aisle_31"}]
        assert predictions[0].output == result.rows()[0]["action"]
        assert all(p.finished for p in predictions)

    def test_every_score_the_case_has_is_logged(self, pack, ledger, pipeline, weave):
        result = _one_case(pack, ledger, pipeline)
        expected = {
            name: value for name, value in result.per_case["aisle_31"].items()
            if value is not None
        }
        assert weave.loggers[0].predictions[0].scores == expected

    def test_the_summary_is_the_means(self, pack, ledger, pipeline, weave):
        result = _one_case(pack, ledger, pipeline)
        assert weave.loggers[0].summary == result.scores

    def test_the_run_says_where_it_landed(self, pack, ledger, pipeline, weave):
        assert _one_case(pack, ledger, pipeline).evaluation_url == FakeEvaluationLogger.ui_url

    def test_a_run_told_not_to_publish_does_not(self, pack, ledger, pipeline, weave):
        result = _one_case(pack, ledger, pipeline, publish=False)
        assert result.evaluation_url is None
        assert weave.loggers == []

    def test_a_refused_logger_still_returns_the_run(self, pack, ledger, pipeline, weave, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "weave", FakeWeave(failure=ValueError("no")))
        result = _one_case(pack, ledger, pipeline)
        assert result.completed
        assert result.evaluation_url is None

    def test_the_command_line_picks_the_router_and_the_label(self):
        from standardphysics_agents.cli import build_parser

        args = build_parser().parse_args(["evaluate", "--router", "local", "--version", "v2"])
        assert (args.router, args.version) == ("local", "v2")

    def test_the_command_line_defaults_to_the_local_policy(self):
        from standardphysics_agents.cli import build_parser

        assert build_parser().parse_args(["evaluate"]).router == "local"


KEPT_WITH_A_PROBLEM_LEFT = SimpleNamespace(accepted=True, problems_after=1)
KEPT_WITH_NOTHING_LEFT = SimpleNamespace(accepted=True, problems_after=0)


def _step(action, layout, *, gate=None, rejected=None, findings=(), targets=()):
    decision = (
        SimpleNamespace(action=action, target_finding_ids=list(targets)) if action else None
    )
    return SimpleNamespace(
        decision=decision,
        rejected=rejected,
        assessment=SimpleNamespace(graph_hash=layout, findings=list(findings)),
        result=SimpleNamespace(gate=gate),
    )


class TestTheTrajectory:
    """A control-flow score: what the loop did with its passes, not what it measured."""

    def test_fix_then_escalate_twice_wastes_a_pass(self, pack):
        steps = [
            _step("FIX", "before", gate=KEPT_WITH_A_PROBLEM_LEFT),
            _step("ESCALATE", "after"),
            _step("ESCALATE", "after"),
        ]
        assert loop_trajectory_ok(steps, pack) == 0.0

    def test_fix_then_one_escalation_then_done_is_right(self, pack):
        steps = [
            _step("FIX", "before", gate=KEPT_WITH_A_PROBLEM_LEFT),
            _step("ESCALATE", "after"),
            _step("DONE", "after"),
        ]
        assert loop_trajectory_ok(steps, pack) == 1.0

    def test_a_repeat_the_loop_refused_is_still_waste(self, pack):
        steps = [
            _step("FIX", "before", gate=KEPT_WITH_A_PROBLEM_LEFT),
            _step("ESCALATE", "after"),
            _step(None, "after", rejected=REPEATED_ACTION),
        ]
        assert loop_trajectory_ok(steps, pack) == 0.0

    def test_asking_and_escalating_once_each_is_right(self, pack):
        steps = [
            _step("FIX", "before", gate=KEPT_WITH_A_PROBLEM_LEFT),
            _step("ASK_OWNER", "after"),
            _step("ESCALATE", "after"),
            _step("DONE", "after"),
        ]
        assert loop_trajectory_ok(steps, pack) == 1.0

    def test_stopping_with_a_problem_left_and_nobody_told_is_wrong(self, pack):
        steps = [_step("FIX", "before", gate=KEPT_WITH_A_PROBLEM_LEFT), _step("DONE", "after")]
        assert loop_trajectory_ok(steps, pack) == 0.0

    def test_stopping_with_nothing_left_is_right(self, pack):
        steps = [_step("FIX", "before", gate=KEPT_WITH_NOTHING_LEFT), _step("DONE", "after")]
        assert loop_trajectory_ok(steps, pack) == 1.0

    def test_a_fix_aimed_at_counter_height_is_wrong(self, pack):
        counter = SimpleNamespace(id="counter", check_id="service_counter_height")
        steps = [
            _step("FIX", "before", gate=KEPT_WITH_A_PROBLEM_LEFT, findings=[counter], targets=["counter"]),
            _step("ESCALATE", "after"),
        ]
        assert loop_trajectory_ok(steps, pack) == 0.0

    def test_a_run_with_no_kept_fix_has_nothing_to_say(self, pack):
        steps = [_step("ASK_OWNER", "before"), _step("DONE", "before")]
        assert loop_trajectory_ok(steps, pack) is None

    def test_a_case_that_asks_again_scores_zero(self):
        outcome = SimpleNamespace(
            case=SimpleNamespace(actions_taken=("ASK_OWNER",)),
            decision=SimpleNamespace(action="ASK_OWNER"),
        )
        assert trajectory_ok(outcome) == 0.0

    def test_a_case_that_moves_on_scores_one(self):
        outcome = SimpleNamespace(
            case=SimpleNamespace(actions_taken=("ASK_OWNER",)),
            decision=SimpleNamespace(action="ESCALATE"),
        )
        assert trajectory_ok(outcome) == 1.0

    def test_a_case_with_no_history_scores_nothing(self):
        outcome = SimpleNamespace(
            case=SimpleNamespace(actions_taken=()), decision=SimpleNamespace(action="FIX")
        )
        assert trajectory_ok(outcome) is None

    def test_it_is_a_share_not_an_error(self):
        assert "trajectory_ok" in SCORERS
        assert "trajectory_ok" not in LOWER_IS_BETTER

    def test_the_local_policy_never_repeats_itself_on_the_dataset(self, evaluation):
        assert evaluation.score("trajectory_ok") == 1.0
