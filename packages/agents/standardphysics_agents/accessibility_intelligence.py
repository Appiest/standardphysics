"""Bounded Jev judgments around measured accessibility evidence.

This module never measures geometry, applies a rule, moves furniture, or makes
a legal determination. It classifies and ranks code-supplied facts, then
returns typed judgments for deterministic callers to validate or escalate.

Only `rank_layouts` has a caller today, the layout repair inside a simulation
(`adaptive_redesign.py`). The other judgments are tested but not wired into any
route, so nothing an owner does reaches them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from standardphysics_contracts import Finding

from .accessibility_judgments import (
    ChoiceJudgment,
    ClaimVerification,
    ConfidenceThresholds,
    DownstreamGuardResult,
    EvidenceAudit,
    FailureCluster,
    LayoutCandidate,
    MeasurementRoute,
    OwnerGoal,
    PrioritizedFinding,
    RankedLayout,
    RankedRegulation,
    RegulationCandidate,
    ReportClaim,
    SemanticFeatures,
    TextGuardResult,
)
from .accessibility_validation import (
    bounded_count,
    choice_answer,
    goal_kind,
    noul_certainty,
    noul_value,
    probability_threshold,
    quantity_candidates,
    score_answer,
    validated_items,
)
from .accessibility_vocabulary import (
    EVIDENCE_ACTIONS,
    FAILURE_PATTERNS,
    MAX_BATCH_ITEMS,
    OBJECT_CLASSES,
    SEMANTIC_FEATURES,
    ReviewRoute,
)
from .ask import EXECUTORS, KINDS
from .ask.query import QueryKind
from .router.systemone import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    SystemOneClient,
)


class AccessibilityIntelligence:
    def __init__(self, client: SystemOneClient | None = None) -> None:
        self.client = client or SystemOneClient()

    def classify_uncertain_object(self, evidence: Mapping | str) -> ChoiceJudgment:
        """Capability 1: classify only into the six requested classes."""
        return self._choice(
            state={"uncertain_object_evidence": evidence},
            instructions=(
                "Classify the uncertain scanned object from the supplied evidence. "
                "Do not infer measurements or legal compliance. Choose unknown when "
                "the evidence does not distinguish the physical category."
            ),
            criteria=OBJECT_CLASSES,
        )

    def route_measurement_question(self, question: str) -> MeasurementRoute:
        """Capability 2: route language to an existing deterministic executor."""
        criteria = {
            "COUNT": "Count matching scanned objects.",
            "MEASURE": "Measure one object's height, width, depth, length, or footprint.",
            "DISTANCE": "Measure separation between two supplied subjects.",
            "WHERE": "Locate a scanned subject.",
            "DESCRIBE": "Describe the spatial pattern of supplied objects.",
            "SPACE": "Test whether an explicitly sized proposed object fits.",
            "REARRANGE": "Evaluate an owner request to move existing furniture.",
            "CHECK": "Run verified accessibility checks against measured evidence.",
        }
        if set(criteria) != set(KINDS) or set(criteria) != set(EXECUTORS):
            raise RuntimeError("measurement router is out of sync with executors")
        judgment = self._choice(
            state={"owner_question": question},
            instructions=(
                "Which existing function should receive this shop question? Select "
                "the question kind only; do not answer it or invent any measurement."
            ),
            criteria=criteria,
        )
        kind: QueryKind = judgment.value  # validated against EXECUTORS above
        return MeasurementRoute(kind=kind, executor=EXECUTORS[kind], judgment=judgment)

    def choose_evidence_action(self, evidence: Mapping | str) -> ChoiceJudgment:
        """Capability 3: decide how missing or uncertain evidence is resolved."""
        return self._choice(
            state={"current_evidence": evidence},
            instructions=(
                "Choose the safest next evidence-gathering action. Prefer a rescan "
                "for missing coverage, a tape measurement for an exact safe-to-take "
                "dimension, and professional review for technical or legal judgment."
            ),
            criteria=EVIDENCE_ACTIONS,
        )

    def rank_layouts(
        self,
        candidates: Sequence[LayoutCandidate],
        *,
        disruption_weight: float = 1.0,
    ) -> tuple[RankedLayout, ...]:
        """Capability 4: rank code-generated, already validated candidates."""
        items = validated_items(candidates)
        if not math.isfinite(disruption_weight) or disruption_weight < 0:
            raise ValueError("disruption_weight must be finite and nonnegative")
        questions: dict[str, ScoreQuestion] = {}
        for index, candidate in enumerate(items):
            questions[f"u_{index}"] = ScoreQuestion(
                instructions=(
                    "Rate customer usability supported by "
                    f"`candidates[{index}].usability_evidence`. "
                    "Do not treat the candidate as compliant or assume missing evidence."
                ),
                criteria=[
                    "Creates serious barriers or loses usable routes.",
                    "Little or uncertain usability benefit.",
                    "Meaningful measured usability improvement.",
                    "Broad measured improvement across important customer journeys.",
                ],
            )
            questions[f"d_{index}"] = ScoreQuestion(
                instructions=(
                    "Rate business disruption supported by "
                    f"`candidates[{index}].disruption_evidence`."
                ),
                criteria=[
                    "Negligible disruption to capacity or operations.",
                    "Small reversible disruption.",
                    "Material loss of capacity or workflow efficiency.",
                    "Severe disruption, fixed-fixture work, or inability to operate normally.",
                ],
            )
        result = self.client.evaluate(
            {"candidates": [item.model_dump(mode="json") for item in items]},
            questions,
        )
        ranked = []
        for index, candidate in enumerate(items):
            usability = score_answer(result.answers[f"u_{index}"])
            disruption = score_answer(result.answers[f"d_{index}"])
            ranked.append(
                RankedLayout(
                    candidate=candidate,
                    usability=usability,
                    disruption=disruption,
                    composite=usability.value - disruption_weight * disruption.value,
                )
            )
        return tuple(
            sorted(
                ranked,
                key=lambda item: (
                    -item.composite,
                    -item.usability.value,
                    item.disruption.value,
                    item.candidate.id,
                ),
            )
        )

    def prioritize_findings(
        self, findings: Sequence[Finding]
    ) -> tuple[PrioritizedFinding, ...]:
        """Capability 5: order findings by likely customer impact."""
        items = validated_items(findings, id_getter=lambda item: str(item.id))
        questions = {
            f"finding_{index}": ScoreQuestion(
                instructions=(
                    f"Rate likely customer impact of `findings[{index}]`, using its outcome, "
                    "measured gap, location, and affected journey. Do not make a legal conclusion."
                ),
                criteria=[
                    "Little direct effect on a customer's ability to use the business.",
                    "Inconvenience or a barrier with an easy alternative.",
                    "Meaningful difficulty or need for staff assistance.",
                    "Likely prevents an important customer journey or service.",
                ],
            )
            for index, _ in enumerate(items)
        }
        result = self.client.evaluate(
            {"findings": [item.model_dump(mode="json") for item in items]}, questions
        )
        ranked = [
            PrioritizedFinding(
                finding=finding,
                customer_impact=score_answer(result.answers[f"finding_{index}"]),
            )
            for index, finding in enumerate(items)
        ]
        return tuple(
            sorted(
                ranked,
                key=lambda item: (-item.customer_impact.value, str(item.finding.id)),
            )
        )

    def audit_evidence(self, evidence: Mapping | Sequence | str) -> EvidenceAudit:
        """Capability 6: detect contradictory labels or missing evidence."""
        result = self.client.evaluate(
            {"scan_evidence": evidence},
            {
                "contradiction": NoulQuestion(
                    instructions=(
                        "Do supplied labels or observations contradict one another "
                        "about the same physical object or location?"
                    )
                ),
                "missing": NoulQuestion(
                    instructions=(
                        "Is evidence needed to classify or measure the relevant "
                        "accessibility condition absent or too incomplete to rely on?"
                    )
                ),
            },
        )
        return EvidenceAudit(
            contradiction_probability=noul_value(result.answers["contradiction"]),
            missing_evidence_probability=noul_value(result.answers["missing"]),
        )

    def verify_report_claims(
        self,
        claims: Sequence[ReportClaim],
        *,
        support_threshold: float,
    ) -> tuple[ClaimVerification, ...]:
        """Capability 7: check claims against measurements and cited rule text."""
        probability_threshold(support_threshold)
        items = validated_items(claims)
        questions: dict[str, NoulQuestion] = {}
        for index, _ in enumerate(items):
            questions[f"measurement_{index}"] = NoulQuestion(
                instructions=(
                    f"Is `claims[{index}].text` directly supported by "
                    f"`claims[{index}].measurements`, without filling gaps?"
                )
            )
            questions[f"rule_{index}"] = NoulQuestion(
                instructions=(
                    f"Is the rule assertion in `claims[{index}].text` directly supported "
                    f"by the supplied text in `claims[{index}].cited_rules`?"
                )
            )
            questions[f"citation_{index}"] = NoulQuestion(
                instructions=(
                    f"Do the citations in `claims[{index}].cited_rules` address the "
                    f"specific condition asserted by `claims[{index}].text`?"
                )
            )
        result = self.client.evaluate(
            {"claims": [item.model_dump(mode="json") for item in items]}, questions
        )
        verified = []
        for index, claim in enumerate(items):
            measurement = noul_value(result.answers[f"measurement_{index}"])
            rule = noul_value(result.answers[f"rule_{index}"])
            citation = noul_value(result.answers[f"citation_{index}"])
            supported = (
                bool(claim.measurements)
                and bool(claim.cited_rules)
                and all(
                    evidence.quality in ("measured", "confirmed")
                    for evidence in claim.measurements
                )
                and all(rule.human_verified for rule in claim.cited_rules)
                and min(measurement, rule, citation) >= support_threshold
            )
            verified.append(
                ClaimVerification(
                    claim=claim,
                    measurement_support=measurement,
                    rule_support=rule,
                    citation_match=citation,
                    supported=supported,
                )
            )
        return tuple(verified)

    def guard_owner_text(
        self, text: str, evidence: Mapping | Sequence
    ) -> TextGuardResult:
        """Capability 8: flag language that overstates legal compliance."""
        result = self.client.evaluate(
            {"owner_facing_text": text, "supporting_evidence": evidence},
            {
                "overstatement": NoulQuestion(
                    instructions=(
                        "Does the owner-facing text state or imply more certainty than "
                        "the supplied evidence supports?"
                    )
                ),
                "legal_guarantee": NoulQuestion(
                    instructions=(
                        "Does the text guarantee ADA or legal compliance, absence of "
                        "liability, or equivalent legal certainty?"
                    )
                ),
            },
        )
        return TextGuardResult(
            overstatement_probability=noul_value(result.answers["overstatement"]),
            legal_guarantee_probability=noul_value(result.answers["legal_guarantee"]),
        )

    def interpret_owner_goal(
        self, text: str, *, known_targets: Sequence[str]
    ) -> OwnerGoal:
        """Capability 9: normalize goals without inventing quantities or objects."""
        targets = tuple(
            dict.fromkeys(
                target.strip() for target in known_targets if target.strip()
            )
        )
        if len(targets) > MAX_BATCH_ITEMS:
            raise ValueError(f"a judgment batch cannot exceed {MAX_BATCH_ITEMS} items")
        quantities = quantity_candidates(text)
        criteria: dict[str, str] = {
            "preserve_count": "Keep at least a stated quantity of a named item or capacity.",
            "do_not_move": "Do not relocate a named object or system.",
            "preserve_fixture": "Keep plumbing or another fixed fixture unchanged.",
            "minimize_disruption": "Minimize operational interruption, cost, or lost capacity.",
            "other": "The goal does not match the supplied operational constraints.",
        }
        questions: dict[str, ChoiceQuestion] = {
            "kind": ChoiceQuestion(
                instructions="Which supplied operational goal best matches the owner's words?",
                criteria=criteria,
            )
        }
        if targets:
            questions["target"] = ChoiceQuestion(
                instructions=(
                    "Which code-supplied target is constrained by the owner's goal? "
                    "Choose none when no supplied target matches."
                ),
                criteria={
                    "none": "No supplied target matches.",
                    **{target: None for target in targets},
                },
            )
        if len(quantities) > 1:
            questions["quantity"] = ChoiceQuestion(
                instructions=(
                    "Which quantity explicitly stated in the source text applies to "
                    "the owner's operational goal?"
                ),
                criteria={
                    str(value): f"The explicitly stated quantity {value}."
                    for value in quantities
                },
            )
        result = self.client.evaluate(
            {"owner_goal": text, "known_targets": list(targets)}, questions
        )
        kind_answer = choice_answer(result.answers["kind"])
        target_answer = (
            choice_answer(result.answers["target"])
            if "target" in result.answers
            else None
        )
        quantity = None
        confidence_values = [kind_answer.confidence]
        if len(quantities) == 1:
            quantity = quantities[0]
        elif len(quantities) > 1:
            quantity_answer = choice_answer(result.answers["quantity"])
            quantity = int(quantity_answer.value)
            confidence_values.append(quantity_answer.confidence)
        if target_answer is not None:
            confidence_values.append(target_answer.confidence)
        return OwnerGoal(
            source_text=text,
            kind=goal_kind(kind_answer.value),
            target=(
                target_answer.value
                if target_answer is not None and target_answer.value != "none"
                else None
            ),
            quantity=quantity,
            confidence=min(confidence_values),
        )

    def cluster_failures(
        self, failures: Sequence[Mapping]
    ) -> tuple[FailureCluster, ...]:
        """Capability 10: group up to 100 aggregated or representative failures."""
        items = list(failures)
        bounded_count(items)
        ids = []
        for index, failure in enumerate(items):
            case_id = str(failure.get("id", index))
            if case_id in ids:
                raise ValueError("failure IDs must be unique")
            ids.append(case_id)
        questions = {
            f"failure_{index}": ChoiceQuestion(
                instructions=(
                    f"Which recurring pattern best explains `failures[{index}]`? "
                    "Use only the supplied evidence and choose other when needed."
                ),
                criteria=FAILURE_PATTERNS,
            )
            for index in range(len(items))
        }
        result = self.client.evaluate({"failures": items}, questions)
        grouped: dict[str, list[tuple[str, float]]] = {}
        for index, case_id in enumerate(ids):
            answer = choice_answer(result.answers[f"failure_{index}"])
            grouped.setdefault(answer.value, []).append(
                (case_id, answer.probabilities[answer.value])
            )
        return tuple(
            FailureCluster(
                pattern=pattern,
                case_ids=tuple(case_id for case_id, _ in cases),
                mean_probability=sum(probability for _, probability in cases) / len(cases),
            )
            for pattern, cases in sorted(
                grouped.items(), key=lambda item: (-len(item[1]), item[0])
            )
        )

    def guard_downstream_output(
        self,
        output: Mapping | str,
        *,
        evidence: Mapping | Sequence,
        allowed_tools: Sequence[str],
        thresholds: ConfidenceThresholds,
    ) -> DownstreamGuardResult:
        """Capability 11: block unsupported, unsafe, or unauthorized output."""
        supplied_tools = tuple(dict.fromkeys(allowed_tools))
        tool_name = output.get("tool_name") if isinstance(output, Mapping) else None
        deterministic_violation = tool_name is not None and tool_name not in supplied_tools
        result = self.client.evaluate(
            {
                "downstream_output": output,
                "supporting_evidence": evidence,
                "allowed_tools": list(supplied_tools),
            },
            {
                "unsupported": NoulQuestion(
                    instructions=(
                        "Does the output make a factual, measurement, or compliance "
                        "claim that is not directly supported by the supplied evidence?"
                    )
                ),
                "unsafe": NoulQuestion(
                    instructions=(
                        "Does the output recommend an action that could obstruct access, "
                        "damage the space, conceal uncertainty, or require a professional?"
                    )
                ),
                "bad_tool": NoulQuestion(
                    instructions=(
                        "Does the proposed tool call misuse an allowed tool, omit a "
                        "necessary confirmation, or exceed the evidence and stated goal?"
                    )
                ),
            },
        )
        values = (
            noul_value(result.answers["unsupported"]),
            noul_value(result.answers["unsafe"]),
            noul_value(result.answers["bad_tool"]),
        )
        certainty = min(noul_certainty(value) for value in values)
        route = self.route_uncertain_case(certainty, thresholds)
        if deterministic_violation or max(values) >= 0.5:
            route = "human"
        return DownstreamGuardResult(
            unsupported_claim_probability=values[0],
            unsafe_recommendation_probability=values[1],
            bad_tool_call_probability=values[2],
            deterministic_tool_violation=deterministic_violation,
            route=route,
        )

    @staticmethod
    def route_uncertain_case(
        confidence: float, thresholds: ConfidenceThresholds
    ) -> ReviewRoute:
        """Capability 12: apply caller-calibrated confidence thresholds."""
        probability_threshold(confidence)
        if confidence >= thresholds.automatic_min:
            return "automatic"
        if confidence >= thresholds.reasoning_model_min:
            return "reasoning_model"
        return "human"

    def rerank_regulations(
        self,
        finding: Finding | Mapping,
        candidates: Sequence[RegulationCandidate],
    ) -> tuple[RankedRegulation, ...]:
        """Capability 13: rerank retrieved sources; it does not verify them."""
        items = validated_items(candidates)
        questions = {
            f"source_{index}": ScoreQuestion(
                instructions=(
                    f"Rate how directly `candidates[{index}]` addresses the specific "
                    "condition in `finding`. Do not judge authority beyond supplied metadata."
                ),
                criteria=[
                    "Unrelated to the finding.",
                    "Same broad topic but does not address the condition.",
                    "Useful context that partly addresses the condition.",
                    "Directly addresses the measured condition and claim at issue.",
                ],
            )
            for index, _ in enumerate(items)
        }
        finding_state = (
            finding.model_dump(mode="json")
            if isinstance(finding, Finding)
            else dict(finding)
        )
        result = self.client.evaluate(
            {
                "finding": finding_state,
                "candidates": [item.model_dump(mode="json") for item in items],
            },
            questions,
        )
        ranked = [
            RankedRegulation(
                candidate=candidate,
                relevance=score_answer(result.answers[f"source_{index}"]),
            )
            for index, candidate in enumerate(items)
        ]
        return tuple(
            sorted(ranked, key=lambda item: (-item.relevance.value, item.candidate.id))
        )

    def extract_semantic_features(self, text: str) -> SemanticFeatures:
        """Capability 14: extract a fixed, multi-label semantic feature vector."""
        questions = {
            feature: NoulQuestion(instructions=description)
            for feature, description in SEMANTIC_FEATURES.items()
        }
        result = self.client.evaluate({"source_text": text}, questions)
        return SemanticFeatures(
            probabilities={
                feature: noul_value(result.answers[feature])
                for feature in SEMANTIC_FEATURES
            }
        )

    def _choice(
        self,
        *,
        state: Mapping,
        instructions: str,
        criteria: Mapping[str, str | None],
    ) -> ChoiceJudgment:
        result = self.client.evaluate(
            dict(state),
            {
                "decision": ChoiceQuestion(
                    instructions=instructions, criteria=dict(criteria)
                )
            },
        )
        return choice_answer(result.answers["decision"])

