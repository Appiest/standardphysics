"""Putting a piece back where the scan found it restores the scan, questions and all, and the gate lets it."""

from standardphysics_agents.evaluation.gate import GateResult
from standardphysics_agents.training.edits import TrainingEdits
from standardphysics_agents.training.menu import _Guess, _restores_the_scan


def _verdict(*reasons: str, before: int = 2, after: int = 0) -> GateResult:
    return GateResult(False, reasons, before, after, 70.0, 0.0)


def test_a_put_back_that_only_returns_the_scans_own_questions_passes():
    back = _Guess(TrainingEdits(), "put Table back where it was", restores_scan=True)
    assert _restores_the_scan(back, _verdict("passing_space lost its measured answer"))


def test_any_other_objection_still_stops_it():
    back = _Guess(TrainingEdits(), "put Table back where it was", restores_scan=True)
    assert not _restores_the_scan(back, _verdict("route_clear_width got worse"))
    assert not _restores_the_scan(back, _verdict("passing_space lost its measured answer", before=1, after=2))


def test_an_ordinary_move_gets_no_such_pass():
    slide = _Guess(TrainingEdits(), "slide Table 3 in further from the Bag")
    assert not _restores_the_scan(slide, _verdict("passing_space lost its measured answer"))
