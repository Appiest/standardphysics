"""The experiment grid, tested without an account.

CI has no keys and no wandb install, so the wiring is proved against a stand-in
module: that one run is logged per configuration, that the config is flat
enough to plot, that the metrics are the ones the evaluation already computed,
and that a key W&B turns away leaves the numbers on disk rather than raising.
"""

from __future__ import annotations

import json

import pytest
from standardphysics_agents.evaluation import dataset
from standardphysics_agents.evaluation.configuration import (
    Setup,
    measurements,
    new_measurements,
    setup,
)
from standardphysics_agents.evaluation.experiments import (
    DEFAULT_GRID,
    GRID_AXES,
    NO_PROJECT,
    NOT_INSTALLED,
    NOT_SIGNED_IN,
    TABLE_COLUMNS,
    Counted,
    blocker,
    grid,
    log_experiments,
    run_experiment,
    run_grid,
    save_experiments,
)
from standardphysics_agents.evaluation.scorers import LOWER_IS_BETTER, SCORERS
from standardphysics_fixtures import (
    FixtureMeasurements,
    build_graph,
    build_scenario,
)

TWO_CASES = dataset()[:2]


class FakeTable:
    def __init__(self, columns, data):
        self.columns, self.data = columns, data


class FakeRun:
    url = "https://wandb.test/team/project/runs/one"

    def __init__(self, **arguments):
        self.arguments = arguments
        self.logged: dict = {}
        self.finished = False

    def log(self, values):
        self.logged.update(values)

    def finish(self):
        self.finished = True


class FakeWandb:
    def __init__(self):
        self.runs: list[FakeRun] = []
        self.refuse = False
        self.key: str | None = None

    def login(self, prompt=True, verify=True):
        """What wandb answers when asked whether it already holds a key."""
        return self.key is not None

    def init(self, **arguments):
        if self.refuse:
            raise RuntimeError("that key is not valid")
        run = FakeRun(**arguments)
        self.runs.append(run)
        return run

    def Table(self, columns, data):  # noqa: N802 - matches wandb.Table
        return FakeTable(columns, data)


@pytest.fixture
def wandb(monkeypatch):
    import sys

    fake = FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake)
    monkeypatch.setenv("WANDB_API_KEY", "not-a-real-key")
    monkeypatch.setenv("WANDB_ENTITY", "team")
    monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
    return fake


@pytest.fixture
def experiment():
    """A real run, kept cheap: the stand-in measurements and two cases."""
    return run_experiment(
        setup(measurements="stub", preview_unverified=True), TWO_CASES
    )


class TestTheGrid:
    def test_every_combination_gets_a_configuration(self):
        built = grid(cell_size=(0.025, 0.05), fix_candidates=(4, 8))
        assert {(s.cell_size, s.fix_candidates) for s in built} == {
            (0.025, 4), (0.025, 8), (0.05, 4), (0.05, 8),
        }

    def test_a_label_says_what_the_configuration_sets(self):
        assert setup(cell_size=0.05, fix_candidates=4).label == (
            "50 mm cells, 4 candidates"
        )

    def test_a_label_leaves_the_defaults_out(self):
        assert setup(measurements="pipeline").label == "pipeline measurements"
        assert setup(run_fixes=True).label == "defaults"

    def test_the_default_grid_crosses_both_axes(self):
        assert list(GRID_AXES) == ["cell_size", "fix_candidates"]
        swept = [s for s in DEFAULT_GRID if s.measurements == "pipeline"]
        assert len(swept) == len(GRID_AXES["cell_size"]) * len(
            GRID_AXES["fix_candidates"]
        )

    def test_it_carries_one_control_against_the_stub_measurements(self):
        assert [s.measurements for s in DEFAULT_GRID].count("stub") == 1

    def test_no_two_configurations_are_the_same(self):
        fields = [tuple(sorted(s.fields().items())) for s in DEFAULT_GRID]
        assert len(set(fields)) == len(fields)

    def test_the_ladder_spans_where_it_binds(self):
        """Four against sixteen. The dataset resolves a third fewer fixes at
        four candidates once the occupancy grid is 20 or 30 mm, which is what
        made the axis look inert on a grid that only held 15, 25 and 50."""
        assert set(GRID_AXES["fix_candidates"]) == {4, 16}

    def test_the_grid_runs_one_experiment_per_configuration(self):
        run = run_grid(
            [setup(measurements="stub", preview_unverified=True)], cases=TWO_CASES
        )
        assert [e.setup.label for e in run] == ["stub measurements, unverified preview"]


class TestTheConfig:
    def test_it_carries_every_knob(self, experiment):
        assert set(Setup("").fields()) <= set(experiment.config())

    def test_it_says_which_rule_pack_and_how_many_cases(self, experiment):
        config = experiment.config()
        assert config["rulepack_version"]
        assert config["cases"] == len(TWO_CASES)

    def test_every_value_is_a_flat_scalar(self, experiment):
        """A nested config is a config ARIA cannot plot."""
        for name, value in experiment.config().items():
            assert isinstance(value, (str, int, float, bool)), name


class TestTheMetrics:
    def test_every_scorer_that_had_something_to_say_appears(self, experiment):
        assert set(experiment.metrics()) >= set(experiment.result.scores)
        assert set(experiment.result.scores) <= set(SCORERS)

    def test_the_scores_are_the_ones_already_computed(self, experiment):
        metrics = experiment.metrics()
        assert all(
            metrics[name] == value for name, value in experiment.result.scores.items()
        )

    def test_it_says_what_the_run_spent(self, experiment):
        metrics = experiment.metrics()
        assert metrics["cost/wall_seconds"] >= 0
        assert metrics["cost/seconds_per_case"] >= 0
        assert metrics["cost/candidates_measured"] >= 0

    def test_candidates_measured_sums_the_fix_searches(self, experiment):
        expected = sum(
            o.fix.measured for o in experiment.result.outcomes if o.fix is not None
        )
        assert experiment.metrics()["cost/candidates_measured"] == expected

    def test_it_counts_every_measurement_asked_for(self, experiment):
        metrics = experiment.metrics()
        asked = {
            name: value
            for name, value in metrics.items()
            if name.startswith("measurements/")
        }
        assert asked
        assert sum(asked.values()) == metrics["cost/measurements_taken"]

    def test_it_says_whether_the_run_completed(self, experiment):
        assert experiment.metrics()["cost/completed"] is True
        assert experiment.metrics()["cost/cases_failed"] == 0


class TestTheCaseTable:
    def test_one_row_per_case(self, experiment):
        assert len(experiment.table_rows()) == len(TWO_CASES)

    def test_every_row_fills_every_column(self, experiment):
        assert all(len(row) == len(TABLE_COLUMNS) for row in experiment.table_rows())

    def test_a_cell_is_a_number_a_string_or_nothing(self, experiment):
        for row in experiment.table_rows():
            for cell in row:
                assert isinstance(cell, (str, int, float, bool, type(None))), cell

    def test_the_scores_are_columns(self):
        assert set(SCORERS) <= set(TABLE_COLUMNS)


class TestLogging:
    def test_one_run_per_configuration(self, wandb, experiment):
        log_experiments([experiment, experiment])
        assert len(wandb.runs) == 2

    def test_the_run_is_named_after_the_configuration(self, wandb, experiment):
        log_experiments([experiment])
        assert wandb.runs[0].arguments["name"] == experiment.setup.label

    def test_the_knobs_arrive_as_the_config(self, wandb, experiment):
        log_experiments([experiment])
        assert wandb.runs[0].arguments["config"] == experiment.config()

    def test_the_metrics_are_logged(self, wandb, experiment):
        log_experiments([experiment])
        logged = wandb.runs[0].logged
        assert all(logged[name] == value for name, value in experiment.metrics().items())

    def test_the_per_case_table_is_logged(self, wandb, experiment):
        log_experiments([experiment])
        table = wandb.runs[0].logged["cases"]
        assert table.columns == list(TABLE_COLUMNS)
        assert len(table.data) == len(TWO_CASES)

    def test_the_run_is_closed(self, wandb, experiment):
        log_experiments([experiment])
        assert wandb.runs[0].finished

    def test_it_returns_where_to_look(self, wandb, experiment):
        assert log_experiments([experiment]) == [FakeRun.url]

    def test_the_project_and_the_team_come_from_the_environment(self, wandb, experiment):
        log_experiments([experiment])
        assert wandb.runs[0].arguments["project"] == "standardphysics"
        assert wandb.runs[0].arguments["entity"] == "team"

    def test_one_project_string_holding_both_is_split(self, wandb, experiment, monkeypatch):
        monkeypatch.setenv("WANDB_PROJECT", "another-team/standardphysics")
        log_experiments([experiment])
        assert wandb.runs[0].arguments["entity"] == "another-team"
        assert wandb.runs[0].arguments["project"] == "standardphysics"


class TestWithoutAnAccount:
    def test_no_project_logs_nothing(self, wandb, experiment, monkeypatch):
        monkeypatch.delenv("WANDB_PROJECT")
        assert log_experiments([experiment]) == []
        assert wandb.runs == []
        assert blocker() == NO_PROJECT

    def test_no_key_and_no_login_logs_nothing(self, wandb, experiment, monkeypatch):
        monkeypatch.delenv("WANDB_API_KEY")
        assert blocker() == NOT_SIGNED_IN
        assert log_experiments([experiment]) == []
        assert wandb.runs == []

    def test_a_refused_key_leaves_the_numbers_alone(self, wandb, experiment):
        wandb.refuse = True
        assert log_experiments([experiment]) == []
        assert experiment.metrics()["cost/completed"] is True

    def test_it_says_what_to_install(self, monkeypatch, experiment):
        import sys

        monkeypatch.setitem(sys.modules, "wandb", None)
        monkeypatch.setenv("WANDB_API_KEY", "not-a-real-key")
        monkeypatch.setenv("WANDB_PROJECT", "standardphysics")
        assert blocker() == NOT_INSTALLED
        assert log_experiments([experiment]) == []


class TestSigningIn:
    """The gate this module keeps: a run is only attempted where wandb can
    authenticate on its own. Asking for a key in the environment and nowhere
    else is what left a project reading zero runs after a `wandb login`."""

    def test_a_login_counts_without_a_key_in_the_environment(
        self, wandb, experiment, monkeypatch
    ):
        monkeypatch.delenv("WANDB_API_KEY")
        wandb.key = "from-netrc"
        assert blocker() is None
        assert log_experiments([experiment]) == [FakeRun.url]

    def test_a_key_in_the_environment_counts_on_its_own(self, wandb, experiment):
        assert wandb.key is None
        assert blocker() is None

    def test_writing_to_a_directory_needs_neither(
        self, wandb, experiment, monkeypatch
    ):
        monkeypatch.delenv("WANDB_API_KEY")
        monkeypatch.setenv("WANDB_MODE", "offline")
        assert blocker() is None

    def test_a_project_is_still_required(self, wandb, monkeypatch):
        monkeypatch.delenv("WANDB_PROJECT")
        wandb.key = "from-netrc"
        assert blocker() == NO_PROJECT

    def test_the_team_may_be_left_to_wandb(self, wandb, experiment, monkeypatch):
        """wandb falls back to the default entity, which is personal, and ARIA
        only answers in a team project. The command prints where they went."""
        monkeypatch.delenv("WANDB_ENTITY")
        log_experiments([experiment])
        assert wandb.runs[0].arguments["entity"] is None


class TestTheGridOnDisk:
    def test_it_writes_one_object_per_configuration(self, experiment, tmp_path):
        path = save_experiments([experiment], tmp_path / "runs" / "grid.json")
        written = json.loads(path.read_text())
        assert [run["label"] for run in written["runs"]] == [experiment.setup.label]

    def test_it_says_which_metric_is_an_error(self, experiment, tmp_path):
        written = json.loads(
            save_experiments([experiment], tmp_path / "grid.json").read_text()
        )
        assert written["lower_is_better"] == sorted(LOWER_IS_BETTER)

    def test_a_configuration_keeps_its_config_and_its_metrics(self, experiment, tmp_path):
        written = json.loads(
            save_experiments([experiment], tmp_path / "grid.json").read_text()
        )
        assert written["runs"][0]["config"] == experiment.config()
        assert written["runs"][0]["metrics"] == experiment.metrics()


class TestTheLadderKnob:
    """`fix_candidates` has to reach the fix search, or the axis measures nothing."""

    def test_the_configuration_sets_the_search_limit(self, monkeypatch):
        from standardphysics_agents.evaluation import runner

        limits = []
        original = runner.propose_fix

        def spy(*args, **kwargs):
            limits.append(kwargs["limit"])
            return original(*args, **kwargs)

        monkeypatch.setattr(runner, "propose_fix", spy)
        fixable = [case for case in dataset() if case.fix_should_resolve][:1]
        run_experiment(
            setup(measurements="stub", fix_candidates=3, preview_unverified=True),
            fixable,
        )
        assert limits == [3]


class TestCountingWhatWasMeasured:
    """A count is the same number every run. Wall seconds are not."""

    def test_it_counts_each_question(self):
        counted = Counted(FixtureMeasurements())
        graph, scenario = build_graph(), build_scenario()
        counted.route_clear_width(graph, scenario, 0)
        counted.route_clear_width(graph, scenario, 1)
        counted.turning_space(graph, scenario.stops[0].position)
        assert counted.calls == {"route_clear_width": 2, "turning_space": 1}
        assert counted.total == 3

    def test_it_returns_the_provider_s_answer(self):
        graph, scenario = build_graph(), build_scenario()
        provider = FixtureMeasurements()
        assert Counted(provider).route_clear_width(
            graph, scenario, 0
        ).inches == provider.route_clear_width(graph, scenario, 0).inches

    def test_it_leaves_a_plain_attribute_alone(self):
        assert Counted(new_measurements("pipeline", 0.05)).cell_size == 0.05


class TestTheCellSizeKnob:
    def test_the_configuration_sets_the_grid_the_room_is_measured_on(self):
        assert new_measurements("pipeline", 0.04).cell_size == 0.04

    def test_each_configuration_gets_its_own_provider(self):
        """Two configurations sharing one provider means the second rides the
        first one's cache and its cost says nothing."""
        assert new_measurements("pipeline") is not new_measurements("pipeline")

    def test_scoring_one_configuration_reuses_its_provider(self):
        assert measurements("pipeline") is measurements("pipeline")

    def test_the_stub_ignores_it(self):
        assert not hasattr(new_measurements("stub", 0.05), "cell_size")


class TestAgainstTheRealSdk:
    """The stand-in proves our wiring. This proves the SDK still takes it.

    Offline mode writes the run to a directory instead of sending it, so the
    real `init`, `log`, `Table` and `finish` all run with no account. Skipped
    where wandb is not installed, which is CI and any clone that did not ask
    for the observability extra.
    """

    @pytest.fixture
    def offline(self, monkeypatch, tmp_path):
        module = pytest.importorskip("wandb")
        monkeypatch.setenv("WANDB_MODE", "offline")
        monkeypatch.setenv("WANDB_DIR", str(tmp_path))
        monkeypatch.setenv("WANDB_SILENT", "true")
        monkeypatch.delenv("WANDB_API_KEY", raising=False)
        monkeypatch.setenv("WANDB_ENTITY", "team")
        monkeypatch.setenv("WANDB_PROJECT", "shop-review")
        yield tmp_path
        module.teardown()

    def test_a_run_lands_with_its_metrics_and_its_table(self, offline, experiment):
        """One run, one assertion pass: wandb reads WANDB_DIR once a process."""
        log_experiments([experiment])
        runs = list((offline / "wandb").glob("offline-run-*"))
        assert len(runs) == 1

        table = list((runs[0] / "files" / "media" / "table").glob("*.json"))
        assert len(table) == 1
        assert json.loads(table[0].read_text())["columns"] == list(TABLE_COLUMNS)

        written = next(runs[0].glob("run-*.wandb")).read_bytes()
        for name in (*experiment.metrics(), *experiment.config()):
            assert name.encode() in written, name
