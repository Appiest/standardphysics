from __future__ import annotations

from .contracts import LoopConfig, LoopResult, SubmissionTask
from .models import DraftModel
from .quality import SectionQualityGate
from .tracing import TraceSink


class LoopEngine:
    def __init__(self, model: DraftModel, gate: SectionQualityGate, traces: TraceSink) -> None:
        self.model = model
        self.gate = gate
        self.traces = traces

    def run(self, task: SubmissionTask, config: LoopConfig) -> LoopResult:
        best_draft = ""
        best_report = self.gate.evaluate(task, best_draft, config)
        feedback = ""
        self.traces.record("loop_started", objective=task.objective, max_iterations=config.max_iterations)

        for attempt in range(1, config.max_iterations + 1):
            prompt = self._prompt(task, best_draft, feedback)
            self.traces.record("draft_requested", attempt=attempt, prompt=prompt)
            draft = self.model.complete(prompt)
            report = self.gate.evaluate(task, draft, config)
            self.traces.record(
                "candidate_evaluated",
                attempt=attempt,
                draft=draft,
                score=report.score,
                maximum_score=report.maximum_score,
                feedback=report.feedback,
            )

            if report.score > best_report.score:
                best_draft, best_report = draft, report
                self.traces.record("candidate_accepted", attempt=attempt, score=report.score)
            else:
                self.traces.record("candidate_rejected", attempt=attempt, score=report.score, retained_score=best_report.score)

            if best_report.passed:
                self.traces.record("loop_completed", attempt=attempt, reason="quality_gate_passed")
                return LoopResult(best_draft, best_report, attempt)
            feedback = report.feedback

        self.traces.record("loop_completed", attempt=config.max_iterations, reason="iteration_budget_exhausted")
        return LoopResult(best_draft, best_report, config.max_iterations)

    @staticmethod
    def _prompt(task: SubmissionTask, previous_draft: str, feedback: str) -> str:
        headings = "\n".join(f"- {section}" for section in task.required_sections)
        if previous_draft:
            return f"""REVISION REQUEST
Objective: {task.objective}
Required Markdown headings:\n{headings}
Quality feedback: {feedback}
Previous draft:\n{previous_draft}

Return only the complete revised Markdown draft. Preserve and improve good content."""
        return f"""Create a concise hackathon submission in Markdown.
Objective: {task.objective}
Use every required Markdown heading exactly once:\n{headings}
Each section must contain concrete, substantive detail. Return only the draft."""
