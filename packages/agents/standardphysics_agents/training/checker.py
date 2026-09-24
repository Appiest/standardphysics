"""The measured checker, run in training mode.

Production turns any answer that rests on geometry marked `needs_another_look`
into a question, which is right for an owner and useless as a reward: a room
full of questions has nothing a rearrangement can improve. Training treats the
scanned geometry as correct instead. That happens here, on a copy of the graph,
and nowhere in the production path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from standardphysics_contracts import Finding, MeasurementProvider, Scenario, SceneGraph
from standardphysics_contracts.rules import Tier
from standardphysics_pipeline import PipelineMeasurements

from ..assess import Pass, assess
from ..rules import AgentRulePack, VerificationLedger, load_ledger, load_pack

UNSURE_QUALITY = "needs_another_look"
TRUSTED_QUALITY = "measured"


def trusted_geometry(graph: SceneGraph) -> SceneGraph:
    """The same room with every scanned node treated as measured."""
    return graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"quality": TRUSTED_QUALITY}) if node.quality == UNSURE_QUALITY else node
                for node in graph.nodes
            ]
        }
    )


@dataclass
class TrainingChecker:
    scenario: Scenario
    rules: AgentRulePack = field(default_factory=load_pack)
    ledger: VerificationLedger = field(default_factory=load_ledger)
    measure: MeasurementProvider = field(default_factory=PipelineMeasurements)
    max_tier: Tier = 1
    pinned: frozenset[UUID] = frozenset()
    """Pieces the phantom filter holds still; moving one scores zero."""
    owner_layout: SceneGraph | None = None
    """The room as its owner has it, before any scramble, for judging how a layout looks."""

    def assess(self, graph: SceneGraph) -> Pass:
        return assess(
            trusted_geometry(graph),
            self.scenario,
            self.measure,
            rules=self.rules,
            ledger=self.ledger,
            max_tier=self.max_tier,
        )

    def fixable_problems(self, result: Pass) -> list[Finding]:
        """Problems a rearrangement is allowed to address."""
        return [finding for finding in result.problems if self.rules.by_id(finding.check_id).rearrangeable]
