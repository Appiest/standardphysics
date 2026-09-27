"""Photo bakes run in a process of their own, so their arithmetic never holds up a request."""

from __future__ import annotations

import os

import pytest

from standardphysics_api.settings import Settings
from standardphysics_api.worker import in_own_process


def record_process(path: str) -> None:
    with open(path, "w") as handle:
        handle.write(str(os.getpid()))


def fail() -> None:
    raise SystemExit(3)


def test_the_function_runs_in_another_process(tmp_path):
    marker = tmp_path / "pid"
    in_own_process(record_process, str(marker))
    assert int(marker.read_text()) != os.getpid()


def test_a_bake_that_dies_is_reported_as_a_failure():
    with pytest.raises(RuntimeError, match="exited with code 3"):
        in_own_process(fail)


def test_the_server_bakes_out_of_process_unless_told_not_to(monkeypatch):
    monkeypatch.delenv("SP_BAKE_IN_PROCESS", raising=False)
    assert Settings.from_environment().bake_in_own_process
    monkeypatch.setenv("SP_BAKE_IN_PROCESS", "1")
    assert not Settings.from_environment().bake_in_own_process
