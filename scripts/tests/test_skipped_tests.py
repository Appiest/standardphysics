"""The step summary lists how many tests a run skipped, and why."""

from __future__ import annotations

import pathlib

from scripts.skipped_tests import main

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4" skipped="3">
  <testcase classname="tests.test_a" name="test_passes" time="0.01"/>
  <testcase classname="tests.test_a" name="test_export" time="0.0">
    <skipped type="pytest.skip" message="Blender is not installed">skipped</skipped>
  </testcase>
  <testcase classname="tests.test_a" name="test_render" time="0.0">
    <skipped type="pytest.skip" message="Blender is not installed">skipped</skipped>
  </testcase>
  <testcase classname="tests.test_b" name="test_dataset" time="0.0">
    <skipped type="pytest.skip" message="needs datasets/moffett | local only">skipped</skipped>
  </testcase>
</testsuite></testsuites>
"""


def test_groups_skips_by_reason_with_the_most_common_first(tmp_path: pathlib.Path, capsys):
    report = tmp_path / "junit.xml"
    report.write_text(JUNIT)
    assert main(["Root suite", str(report)]) == 0
    output = capsys.readouterr().out
    assert "### Root suite" in output
    assert "3 of 4 tests skipped." in output
    assert output.index("| 2 | Blender is not installed |") < output.index("| 1 | needs datasets/moffett \\| local only |")


def test_a_run_without_skips_says_so_without_a_table(tmp_path: pathlib.Path, capsys):
    report = tmp_path / "junit.xml"
    report.write_text('<testsuites><testsuite><testcase name="test_one"/></testsuite></testsuites>')
    main(["API suite", str(report)])
    output = capsys.readouterr().out
    assert "0 of 1 tests skipped." in output
    assert "| Tests | Reason |" not in output
