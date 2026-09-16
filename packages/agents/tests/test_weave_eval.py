"""The Weave evaluation, tested without an account.

CI has no keys and no Weave install, so the wiring is proved against a stand-in
module: that one model is built per configuration, that the dataset holds the
labels and not the geometry, and that each scorer reports the number the
evaluation's own scorers computed rather than a second opinion.
"""

from __future__ import annotations

import pytest
from standardphysics_agents.evaluation import dataset
from standardphysics_agents.evaluation.configuration import (
    DEFAULT_SETUPS,
    Setup,
    previewing,
    review,
    summary,
)
from standardphysics_agents.evaluation.scorers import SCORERS
from standardphysics_agents.evaluation.weave_eval import (
    evaluate_in_weave,
    rows,
    scorer,
)


class FakeModel:
    """Enough of `weave.Model`: fields arrive as keyword arguments."""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


class FakeEvaluation:
    def __init__(self, **config):
        self.config = config
        self.models = []

    def evaluate(self, model):
        self.models.append(model)
        return {name: {"mean": 1.0} for name in SCORERS}


class FakeDataset:
    def __init__(self, name, rows):
        self.name, self.rows = name, rows


class FakeWeave:
    """The three pieces of the Weave surface an evaluation touches."""

    Model = FakeModel

    def __init__(self):
        self.evaluations: list[FakeEvaluation] = []

    def op(self, fn):
        return fn

    def Dataset(self, name, rows):  # noqa: N802 - matches weave.Dataset
        return FakeDataset(name, rows)

    def Evaluation(self, **config):  # noqa: N802 - matches weave.Evaluation
        evaluation = FakeEvaluation(**config)
        self.evaluations.append(evaluation)
        return evaluation


@pytest.fixture
def weave(monkeypatch):
    import sys

    fake = FakeWeave()
    monkeypatch.setitem(sys.modules, "weave", fake)
    from standardphysics_agents.evaluation import weave_eval

    weave_eval._model_class.cache_clear()
    yield fake
    weave_eval._model_class.cache_clear()


class TestTheDataset:
    def test_one_row_per_case(self):
        assert len(rows()) == len(dataset())

    def test_a_row_says_what_a_correct_answer_is(self):
        row = next(r for r in rows() if r["case"] == "fixture_as_shipped")
        assert row["expected_problems"]
        assert row["expected_questions"]
        assert row["tier"] in (1, 2, 3)

    def test_the_geometry_stays_out_of_the_row(self):
        assert not {"graph", "scenario"} & set(rows()[0])

    def test_a_row_is_json_shaped(self):
        for value in rows()[0].values():
            assert isinstance(value, (str, int, bool, list, type(None))), value


class TestTheScorers:
    def test_there_is_one_per_scorer(self, weave):
        evaluate_in_weave([], cases=dataset()[:1])
        assert [s.__name__ for s in weave.evaluations[0].config["scorers"]] == list(SCORERS)

    def test_a_scorer_reports_the_number_already_computed(self, weave):
        score = scorer(weave, "finding_recall")
        assert score({"scores": {"finding_recall": 0.25}}) == 0.25

    def test_a_case_with_nothing_to_say_scores_nothing(self, weave):
        score = scorer(weave, "finding_recall")
        assert score({"scores": {"finding_recall": None}}) is None
        assert score({}) is None
        assert score(None) is None

    def test_no_scorer_is_defined_twice(self, weave):
        """The numbers come from scorers.py, so this module defines none."""
        names = [s.__name__ for s in [scorer(weave, name) for name in SCORERS]]
        assert sorted(names) == sorted(SCORERS)


class TestTheConfigurations:
    def test_one_model_per_configuration(self, weave):
        evaluate_in_weave(DEFAULT_SETUPS, cases=dataset()[:1])
        assert len(weave.evaluations[0].models) == len(DEFAULT_SETUPS)

    def test_the_model_carries_the_configuration(self, weave):
        evaluate_in_weave([Setup("exact", measurements="stub", run_fixes=False)],
                          cases=dataset()[:1])
        model = weave.evaluations[0].models[0]
        assert (model.measurements, model.run_fixes) == ("stub", False)

    def test_the_result_comes_back_under_its_label(self, weave):
        result = evaluate_in_weave([Setup("just this one")], cases=dataset()[:1])
        assert list(result) == ["just this one"]

    def test_the_default_configurations_differ(self):
        assert len({(s.measurements, s.run_fixes, s.router) for s in DEFAULT_SETUPS}) == len(
            DEFAULT_SETUPS
        )

    def test_previewing_leaves_the_labels_alone(self):
        assert [s.label for s in previewing(DEFAULT_SETUPS)] == [
            s.label for s in DEFAULT_SETUPS
        ]

    def test_previewing_verifies_every_rule(self):
        assert all(setup.preview_unverified for setup in previewing(DEFAULT_SETUPS))


class TestOneCaseThroughTheSystem:
    """`predict` on a real case, so the summary is proved against real output."""

    @pytest.fixture
    def reviewed(self):
        setup = Setup("stub", measurements="stub", preview_unverified=True)
        return review(setup, "fixture_as_shipped")

    def test_it_reports_what_the_checks_found(self, reviewed):
        assert reviewed["problems"]

    def test_it_reports_every_score(self, reviewed):
        assert set(reviewed["scores"]) == set(SCORERS)

    def test_it_says_what_the_router_chose(self, reviewed):
        assert reviewed["action"]

    def test_it_carries_no_error(self, reviewed):
        assert reviewed["error"] is None

    def test_an_unknown_case_is_an_error_not_an_empty_score(self):
        with pytest.raises(KeyError):
            review(Setup("stub", measurements="stub"), "no_such_case")


class TestEitherWeaveApi:
    """`Evaluation.evaluate` is a coroutine now and was a plain call before."""

    def test_a_coroutine_result_is_awaited(self, weave, monkeypatch):
        async def evaluate(self, model):
            return {name: {"mean": 0.5} for name in SCORERS}

        monkeypatch.setattr(FakeEvaluation, "evaluate", evaluate)
        scored = evaluate_in_weave([Setup("async")], cases=dataset()[:1])
        assert scored["async"]["finding_recall"] == {"mean": 0.5}

    def test_a_plain_result_is_taken_as_it_is(self, weave):
        scored = evaluate_in_weave([Setup("sync")], cases=dataset()[:1])
        assert scored["sync"]["finding_recall"] == {"mean": 1.0}


class TestWithoutWeave:
    def test_it_says_what_to_install(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "weave", None)
        with pytest.raises(RuntimeError, match="observability"):
            evaluate_in_weave([], cases=dataset()[:1])


class TestTheSummary:
    def test_every_scorer_appears_even_when_it_has_nothing_to_say(self):
        from standardphysics_agents.evaluation import run_case
        from standardphysics_agents.router import LocalPolicyRouter
        from standardphysics_agents.rules import VerificationLedger, load_pack
        from standardphysics_fixtures import FixtureMeasurements

        case = dataset()[0]
        outcome = run_case(
            case, FixtureMeasurements(), load_pack(), VerificationLedger(),
            LocalPolicyRouter(), False,
        )
        assert set(summary(outcome)["scores"]) == set(SCORERS)
