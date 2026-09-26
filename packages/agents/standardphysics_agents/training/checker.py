"""The measured checker, run in training mode.

Production turns any answer that rests on geometry marked `needs_another_look`
into a question, which is right for an owner and useless as a reward: a room
full of questions has nothing a rearrangement can improve. Training treats the
scanned geometry as correct instead. That happens here, on a copy of the graph,
and nowhere in the production path.

The `layout` scope is the furniture-and-construction loop the first models
trained on: tier 1 rules, and only problems moving things can clear. The
`fittings` scope also asks about what a piece is: a counter too high for ADA
2010 904.4.1, the tables short of the 5 percent of 226.1, a mounted control
out of the reach ranges of 308. It promotes those rules into the training pass
and, for reach, takes the scanned heights as the measurement, the same way
`trusted_geometry` takes scanned geometry as measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from standardphysics_contracts import Finding, MeasurementProvider, Scenario, SceneGraph
from standardphysics_contracts.rules import Tier
from standardphysics_pipeline import PipelineMeasurements

from ..assess import Pass, assess
from ..rules import AgentRulePack, RuleSpec, VerificationLedger, load_ledger, load_pack

UNSURE_QUALITY = "needs_another_look"
TRUSTED_QUALITY = "measured"

Scope = Literal["layout", "fittings"]

LAYOUT_EDITS = ("moves", "fixture_moves", "wall_shifts")
FITTING_EDITS: dict[str, tuple[str, ...]] = {
    "service_counter_height": ("add_lowered_section", "height_changes", "replacements"),
    "dining_surface_height": ("replacements", "height_changes"),
    "reach_range": ("height_changes",),
}
"""Rules no rearrangement clears, and the fitting edits that can."""

PROMOTED_TO_TRAINING = frozenset({"dining_surface_height", "reach_range"})
"""Rules above tier 1 that the fittings scope measures, from the heights the scan has."""


def fittings_pack(pack: AgentRulePack) -> AgentRulePack:
    """The pack with the fitting rules above tier 1 run as measured tier 1 rules, for training only."""
    promoted = [rule.model_copy(update={"tier": 1, "evidence": "measured"}) if rule.id in PROMOTED_TO_TRAINING
                else rule for rule in pack.rules]
    return pack.model_copy(update={"rules": promoted})


def edits_that_resolve(rule: RuleSpec, scope: Scope) -> tuple[str, ...]:
    """Which edit types in the answer schema can clear a problem against this rule."""
    if rule.rearrangeable:
        return LAYOUT_EDITS
    return FITTING_EDITS.get(rule.id, ()) if scope == "fittings" else ()


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
    scope: Scope = "layout"

    def __post_init__(self) -> None:
        if self.scope == "fittings":
            self.rules = fittings_pack(self.rules)

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
        """Problems some edit type in the answer schema can clear."""
        return [finding for finding in result.problems if self.resolvable(finding.check_id)]

    def rearrangeable_problems(self, result: Pass) -> list[Finding]:
        """Problems moving furniture can address, which is all the furniture search is given."""
        return [finding for finding in result.problems if self.rules.by_id(finding.check_id).rearrangeable]

    def resolvable(self, rule_id: str) -> bool:
        return bool(edits_that_resolve(self.rules.by_id(rule_id), self.scope))

    def unfixable_rules(self) -> dict[str, str]:
        """Every rule in the pack no edit clears in this scope, and why."""
        evaluated = {rule.id for rule in self.rules.within_tier(self.max_tier)}
        return {rule.id: _why_unfixable(rule, rule.id in evaluated, self.scope) for rule in self.rules.rules
                if not (rule.id in evaluated and rule.measurable and edits_that_resolve(rule, self.scope))}


def _why_unfixable(rule: RuleSpec, evaluated: bool, scope: Scope) -> str:
    if not evaluated:
        return f"tier {rule.tier}, not measured in the {scope} scope"
    if not rule.measurable:
        return f"needs {rule.evidence.replace('_', ' ')}, which no edit can supply"
    return "no edit type changes what it measures"
