"""Precedent Compiler: binds room typology and scene nodes to actionable case law.

Matches active scan entities against the precedent corpus, generating both
deterministic inspection tasks for MeasurementProvider and strict prompt/constraint
specifications for the neural layout model (Qwen 3.8 27B).
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
        
        # Collect lowercase labels and roles from scene graph nodes
        scene_tokens = set()
        for node in nodes:
            scene_tokens.add(node.label.lower())
            if hasattr(node, "role") and node.role:
                scene_tokens.add(str(node.role).lower())

        matched: list[PrecedentDirective] = []
        for directive in self.directives:
            # 1. Check typology alignment
            typology_match = any(
                typology_str == t.value or typology_str in t.value or t.value in typology_str
                for t in directive.trigger.space_typologies
            )
            if not typology_match:
                continue

            # 2. Check entity presence (at least one or all required entities present)
            # A case activates if any of its primary target entities are in the scene
            entity_present = any(
                any(req.lower() in token for token in scene_tokens)
                for req in directive.trigger.required_entities
            )
            if entity_present:
                matched.append(directive)

        return matched

    def compile_inspection_manifest(
        self, matched: list[PrecedentDirective]
    ) -> list[PrecedentQuerySpec]:
        """Compile a deduplicated manifest of geometric checks to execute."""
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
        """Format executable spatial constraints to inject into Qwen 3.8's prompt."""
        if not matched:
            return ""

        sections = [
            "\n[ACTIONABLE ADA CASE PRECEDENT CONSTRAINTS]",
            "The room layout must strictly adhere to the following litigation precedent rules:",
        ]
        for d in matched:
            sections.append(f"• Precedent Case: {d.title} ({d.landmark_citation})")
            sections.append(f"  Warning: {d.plain_english_warning}")
            if d.constraints.forbidden_moves:
                sections.append(f"  Forbidden Moves: {', '.join(d.constraints.forbidden_moves)}")
            sections.append(f"  Required Pattern: {d.constraints.solution_pattern}")
            if d.constraints.minimum_accessible_percentage > 0:
                pct = int(d.constraints.minimum_accessible_percentage * 100)
                sections.append(f"  Mandatory Ratio: At least {pct}% of dining/work surfaces must be accessible (28-34 in high, 27 in knee clearance).")
            if d.constraints.anti_isolation:
                sections.append("  Anti-Isolation Rule: Accessible elements MUST be integrated into the main customer seating zone, not isolated near doors or utility areas.")
        
        return "\n".join(sections)
