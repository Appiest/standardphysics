from __future__ import annotations

import json

import numpy as np
import pytest
from standardphysics_agents.cli import main
from standardphysics_agents.evaluation import run_accessibility_sweep
from standardphysics_contracts import to_inches, to_meters
from standardphysics_pipeline import Grid, clearance_map, widest_path


def test_sweep_runs_the_exact_requested_number_of_deterministic_cases():
    first = run_accessibility_sweep(evaluations=101, seed=7)
    second = run_accessibility_sweep(evaluations=101, seed=7)

    assert first.passed is True
    assert first.evaluations == first.requested_evaluations == 101
    assert sum(first.profile_evaluations.values()) == 101
    assert first.failures == 0
    assert first.digest == second.digest


@pytest.mark.parametrize("evaluations", [0, -1])
def test_sweep_rejects_a_non_positive_case_count(evaluations):
    with pytest.raises(ValueError, match="positive"):
        run_accessibility_sweep(evaluations=evaluations)


def test_cli_saves_the_reproducible_sweep_result(tmp_path, capsys):
    output = tmp_path / "sweep.json"

    assert main(
        [
            "accessibility-sweep",
            "--evaluations",
            "17",
            "--seed",
            "11",
            "--record-runs",
            "10",
            "--out",
            str(output),
        ]
    ) == 0

    saved = json.loads(output.read_text())
    assert saved["passed"] is True
    assert saved["evaluations"] == 17
    assert saved["seed"] == 11
    assert len(saved["recorded_runs"]) == 1
    assert "17 evaluations" in capsys.readouterr().out


def test_recording_preserves_cases_and_replays_real_results():
    baseline = run_accessibility_sweep(evaluations=2000, seed=7)
    recorded = run_accessibility_sweep(evaluations=2000, seed=7, record_runs=10)
    assert recorded.digest == baseline.digest
    assert recorded.profile_route_fits == baseline.profile_route_fits
    assert recorded.failures == baseline.failures
    assert len(recorded.recorded_runs) == 10
    assert len({run.layout for run in recorded.recorded_runs}) == 10
    assert {run.fits for run in recorded.recorded_runs} == {False, True}
    for run in recorded.recorded_runs:
        occupied = np.array(run.occupied, dtype=bool)
        grid = Grid(0, 0, to_meters(run.cell_size_inches), occupied,
                    np.full(occupied.shape, -1, dtype=np.int32), [])
        clearance = clearance_map(grid)
        replay = widest_path(grid, clearance, run.start, run.goal, endpoint_exemption=0)
        assert tuple(replay.path) == run.path
        assert to_inches(replay.width_meters) == pytest.approx(run.bottleneck_width_inches)
        assert replay.reachable == run.reachable
        assert run.oracle_agrees


@pytest.mark.parametrize("count", [-1, 101, 1.5, True])
def test_recording_rejects_unbounded_or_noninteger_limits(count):
    with pytest.raises(ValueError, match="record_runs"):
        run_accessibility_sweep(evaluations=1, record_runs=count)


def test_recording_stops_with_a_partial_sweep():
    result = run_accessibility_sweep(evaluations=1, record_runs=10)
    assert len(result.recorded_runs) == 1
    assert result.recorded_runs[0].evaluation == 1
