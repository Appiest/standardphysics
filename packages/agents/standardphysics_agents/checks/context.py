"""Everything a check is allowed to look at.

A check gets the measured model, the routine being screened, something that can
measure it, and the rules. It gets no way to write a dimension and no way to
enable itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from standardphysics_contracts import MeasurementProvider, Scenario, SceneGraph

from ..rules import AgentRulePack, RuleSpec, VerificationLedger


@dataclass(frozen=True)
class CheckContext:
    graph: SceneGraph
    scenario: Scenario
    measure: MeasurementProvider
    rules: AgentRulePack
    ledger: VerificationLedger

    def rule(self, rule_id: str) -> RuleSpec:
        return self.rules.by_id(rule_id)

    def legs(self) -> range:
        return range(len(self.scenario.stops) - 1)

    def stop_name(self, index: int) -> str:
        return self.scenario.stops[index].name

    def label(self, node_id) -> str:
        try:
            return self.graph.by_id(node_id).label
        except KeyError:
            return "object"

    def movable(self, node_id) -> bool:
        """Whether a fix may propose moving this. A built-in counter may not."""
        try:
            return self.graph.by_id(node_id).movable
        except KeyError:
            return False

    def labels_by_movability(self, node_ids) -> tuple[list[str], list[str]]:
        """(what can be moved, what cannot), so a fix only asks for the possible."""
        movable: list[str] = []
        fixed: list[str] = []
        for node_id in node_ids:
            (movable if self.movable(node_id) else fixed).append(self.label(node_id))
        return movable, fixed
