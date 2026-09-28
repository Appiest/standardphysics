"""The coverage gate reads coverage.xml the way `coverage report` counts, and
fails on a module that dropped below its floor or vanished from the report."""

from __future__ import annotations

import pathlib

from scripts.coverage_floors import FLOORS, check_floors, main, read_report

REPORT = """<?xml version="1.0" ?>
<coverage branch-rate="0.5" line-rate="0.75" version="7.16.2">
  <packages><package name="api"><classes>
    <class filename="services/api/standardphysics_api/auth.py" name="auth.py">
      <lines>
        <line number="1" hits="1"/>
        <line number="2" hits="1" branch="true" condition-coverage="50% (1/2)" missing-branches="4"/>
        <line number="3" hits="1"/>
        <line number="4" hits="0"/>
      </lines>
    </class>
    <class filename="services/api/standardphysics_api/budgets.py" name="budgets.py">
      <lines><line number="1" hits="1"/></lines>
    </class>
  </classes></package></packages>
</coverage>
"""
AUTH = "services/api/standardphysics_api/auth.py"
BUDGETS = "services/api/standardphysics_api/budgets.py"


def write_report(tmp_path: pathlib.Path) -> pathlib.Path:
    report = tmp_path / "coverage.xml"
    report.write_text(REPORT)
    return report


def test_counts_branches_alongside_lines(tmp_path):
    auth = read_report(write_report(tmp_path))[AUTH]
    assert (auth.covered, auth.measured) == (4, 6)
    assert round(auth.percent, 1) == 66.7


def test_a_module_at_or_above_its_floor_passes(tmp_path):
    [result] = check_floors(read_report(write_report(tmp_path)), {AUTH: 65})
    assert result.passed


def test_a_module_below_its_floor_fails(tmp_path):
    [result] = check_floors(read_report(write_report(tmp_path)), {AUTH: 70})
    assert not result.passed


def test_a_module_missing_from_the_report_fails(tmp_path):
    [result] = check_floors(read_report(write_report(tmp_path)), {"services/api/standardphysics_api/gone.py": 10})
    assert result.coverage is None
    assert not result.passed


def test_the_command_fails_while_any_floor_is_unmet(tmp_path, capsys):
    report = write_report(tmp_path)
    assert main([str(report)]) == 1
    output = capsys.readouterr().out
    assert f"| `{BUDGETS}` | 100.0% |" in output
    assert "not in the report" in output


def test_every_floor_names_a_real_module():
    repo = pathlib.Path(__file__).resolve().parents[2]
    assert FLOORS
    for path, floor in FLOORS.items():
        assert (repo / path).is_file(), path
        assert 0 < floor <= 100, path
