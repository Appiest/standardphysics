from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SubmissionTask:
    """The objective and observable requirements for an agent's output."""

    objective: str
    required_sections: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("objective must not be empty")
        if not self.required_sections:
            raise ValueError("at least one required section is needed")


@dataclass(frozen=True)
class LoopConfig:
    max_iterations: int = 4
    minimum_section_characters: int = 40

    def __post_init__(self) -> None:
        if not 1 <= self.max_iterations <= 10:
            raise ValueError("max_iterations must be between 1 and 10")
        if self.minimum_section_characters < 1:
            raise ValueError("minimum_section_characters must be positive")


@dataclass(frozen=True)
class QualityReport:
    score: int
    maximum_score: int
    missing_sections: tuple[str, ...]
    thin_sections: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.score == self.maximum_score

    @property
    def feedback(self) -> str:
        parts: list[str] = []
        if self.missing_sections:
            parts.append("Add these exact headings with substantive content: " + ", ".join(self.missing_sections))
        if self.thin_sections:
            parts.append("Expand these sections beyond the minimum detail: " + ", ".join(self.thin_sections))
        return " ".join(parts) or "All rubric requirements are met."

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"passed": self.passed, "feedback": self.feedback}


@dataclass(frozen=True)
class LoopResult:
    draft: str
    report: QualityReport
    attempts: int

    @property
    def passed(self) -> bool:
        return self.report.passed

