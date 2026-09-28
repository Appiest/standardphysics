from __future__ import annotations

import re

from .contracts import LoopConfig, QualityReport, SubmissionTask


class SectionQualityGate:
    """A deterministic judge: section requirements are checked, never inferred."""

    def evaluate(self, task: SubmissionTask, draft: str, config: LoopConfig) -> QualityReport:
        headings = self._sections(draft)
        missing = tuple(section for section in task.required_sections if section.casefold() not in headings)
        thin = tuple(
            section
            for section in task.required_sections
            if section.casefold() in headings
            and len(headings[section.casefold()].strip()) < config.minimum_section_characters
        )
        maximum = len(task.required_sections)
        return QualityReport(
            score=maximum - len(missing) - len(thin),
            maximum_score=maximum,
            missing_sections=missing,
            thin_sections=thin,
        )

    @staticmethod
    def _sections(draft: str) -> dict[str, str]:
        matches = list(re.finditer(r"^#{1,6}\s+(.+?)\s*$", draft, flags=re.MULTILINE))
        sections: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(draft)
            sections[match.group(1).strip().casefold()] = draft[match.end() : end]
        return sections

