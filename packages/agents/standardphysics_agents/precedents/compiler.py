"""Directive compiler: binds room typology and scene nodes to ADA layout directives.

Matches scan entities against the directive corpus and produces two things: the
measurements a MeasurementProvider has to take, and the constraint section of
the layout model's prompt. Only case references a person has verified reach the
prompt.
"""

from __future__ import annotations

from standardphysics_contracts import SceneNode
from standardphysics_contracts.precedents import (
    PrecedentDirective,
    PrecedentQuerySpec,
    SpaceTypology,
)


class PrecedentCompiler:
    def __init__(self, directives: list[PrecedentDirective]):
        self.directives = list(directives)

    def match(
        self,
        typology: SpaceTypology | str,
        nodes: list[SceneNode],
    ) -> list[PrecedentDirective]:
        """Return directives whose typology and required entities match the scan."""
        typology_str = typology.value if isinstance(typology, SpaceTypology) else str(typology)
        
        # Collect lowercase tokens from scene graph nodes
        scene_tokens = set()
        for node in nodes:
            scene_tokens.add(node.label.lower())
            scene_tokens.add(node.label.lower().replace(" ", "_"))
            if hasattr(node, "raw_category") and node.raw_category:
                scene_tokens.add(str(node.raw_category).lower())
                scene_tokens.add(str(node.raw_category).lower().replace("_", " "))
            if hasattr(node, "role") and node.role:
                scene_tokens.add(str(node.role).lower())
                scene_tokens.add(str(node.role).lower().replace(" ", "_"))

        matched: list[PrecedentDirective] = []
        for directive in self.directives:
            # 1. Check typology alignment
            typology_match = any(
                typology_str == t.value or typology_str in t.value or t.value in typology_str
                for t in directive.trigger.space_typologies
            )
            if not typology_match:
                continue

            # 2. Check entity presence (case activates if any required entity is in the scene)
            entity_present = any(
                any(
                    req.lower() in token or req.lower().replace("_", " ") in token
                    for token in scene_tokens
                )
                for req in directive.trigger.required_entities
            )
            if entity_present:
                matched.append(directive)

        return matched

    def compile_inspection_manifest(
        self, matched: list[PrecedentDirective]
    ) -> list[PrecedentQuerySpec]:
        """Every measurement the matched directives need, once each."""
        seen = set()
        queries = []
        for d in matched:
            for q in d.inspection_queries:
                if q.query_id not in seen:
                    seen.add(q.query_id)
                    queries.append(q)
        return queries

    def format_qwen_precedent_prompt(
        self, matched: list[PrecedentDirective]
    ) -> str:
        """Format the matched directives as layout constraints for the model's prompt."""
        if not matched:
            return ""
        sections = [
            "\n[ADA LAYOUT CONSTRAINTS]",
            "The room layout must satisfy these 2010 ADA Standards requirements:",
        ]
        for directive in matched:
            sections.extend(_directive_lines(directive))
        return "\n".join(sections)


def _section_list(authority: list[str]) -> str:
    return ", ".join(section.removeprefix("ADA_2010_") for section in authority)


def _directive_lines(directive: PrecedentDirective) -> list[str]:
    lines = [
        f"- {directive.title} (ADA 2010 {_section_list(directive.authority)})",
        f"  Requirement: {directive.plain_english_warning}",
        f"  Required pattern: {directive.constraints.solution_pattern}",
    ]
    if directive.constraints.fixed_roles:
        lines.append(f"  Do not move: {', '.join(directive.constraints.fixed_roles)}")
    lines.extend(
        f"  Enforced in: {case.case_name}, {case.citation}: {case.holding}"
        for case in directive.verified_cases
    )
    return lines
