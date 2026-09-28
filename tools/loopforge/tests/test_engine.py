from pathlib import Path

from loopforge.contracts import LoopConfig, SubmissionTask
from loopforge.engine import LoopEngine
from loopforge.models import ScriptedDemoModel
from loopforge.quality import SectionQualityGate
from loopforge.tracing import JsonlTraceSink


def task() -> SubmissionTask:
    return SubmissionTask("Demonstrate an auditable self-improving loop.", ("Problem", "Safety"))


def test_scripted_loop_repairs_missing_requirement(tmp_path: Path) -> None:
    trace = tmp_path / "run.jsonl"
    result = LoopEngine(ScriptedDemoModel(), SectionQualityGate(), JsonlTraceSink(trace)).run(task(), LoopConfig(max_iterations=3))

    assert result.passed
    assert result.attempts == 2
    assert "## Safety" in result.draft
    events = trace.read_text().splitlines()
    assert sum("candidate_accepted" in event for event in events) == 2
    assert any("quality_gate_passed" in event for event in events)


def test_loop_respects_its_iteration_budget(tmp_path: Path) -> None:
    class NeverGoodEnough:
        def complete(self, prompt: str) -> str:
            return "## Problem\nToo short"

    result = LoopEngine(NeverGoodEnough(), SectionQualityGate(), JsonlTraceSink(tmp_path / "trace.jsonl")).run(task(), LoopConfig(max_iterations=2))

    assert not result.passed
    assert result.attempts == 2
