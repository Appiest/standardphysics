from __future__ import annotations

import json
from uuid import UUID

import pytest
from standardphysics_agents import (
    AccessibilityIntelligence,
    ConfidenceThresholds,
    LayoutCandidate,
    MeasurementEvidence,
    RegulationCandidate,
    ReportClaim,
    RuleEvidence,
)
from standardphysics_agents.ask import EXECUTORS
from standardphysics_agents.router import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    SystemOneClient,
    SystemOneError,
)
from standardphysics_contracts import Citation, Finding


class ScriptedTransport:
    """Build valid provider envelopes from per-call answer scripts."""

    def __init__(self, *scripts: dict[str, object] | bytes) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    def post(self, url, body, headers):
        request = json.loads(body)
        self.calls.append(request)
        script = self.scripts.pop(0) if self.scripts else {}
        if isinstance(script, bytes):
            return script
        answers = {}
        for question_id, question in request["questions"].items():
            value = script.get(question_id)
            if question["type"] == "choice":
                options = list(question["criteria"])
                choice = value if value is not None else options[0]
                answers[question_id] = {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {
                        option: 1.0 if option == choice else 0.0 for option in options
                    },
                    "confidence": 1.0,
                }
            elif question["type"] == "score":
                score = float(value if value is not None else 0)
                legend = {
                    str(index): level
                    for index, level in enumerate(question["criteria"])
                }
                answers[question_id] = {
                    "type": "score",
                    "score": score,
                    "legend": legend,
                    "probabilities": {
                        level: 1.0 if int(score) == int(level) else 0.0
                        for level in legend
                    },
                    "confidence": 1.0,
                }
            else:
                answers[question_id] = {
                    "type": "noul",
                    "noul": float(value if value is not None else 0.0),
                }
        return json.dumps(
            {
                "model": "jev-test",
                "answers": answers,
                "usage": {"input_tokens": 20, "output_tokens": 4},
            }
        ).encode()


def intelligence(*scripts):
    transport = ScriptedTransport(*scripts)
    client = SystemOneClient(
        api_key="test-key",
        base_url="https://typesafe.example",
        transport=transport,
    )
    return AccessibilityIntelligence(client), transport


def finding(value: int, title: str) -> Finding:
    return Finding(
        id=UUID(f"00000000-0000-0000-0000-{value:012d}"),
        check_id=f"check-{value}",
        outcome="problem",
        title=title,
        detail="Measured evidence",
        measured_inches=30,
        required_inches=36,
        citation=Citation(
            authority="ADA_2010", edition="2010 ADA Standards", section="403.5.1"
        ),
    )


class TestStrictSystemOneClient:
    def test_validates_all_three_primitives(self):
        transport = ScriptedTransport(
            {"choice": "b", "score": 1, "noul": 0.75}
        )
        client = SystemOneClient(
            api_key="key", base_url="https://typesafe.example", transport=transport
        )
        result = client.evaluate(
            "state",
            {
                "choice": ChoiceQuestion(
                    instructions="Pick one", criteria={"a": None, "b": None}
                ),
                "score": ScoreQuestion(
                    instructions="Rate it", criteria=["low", "high"]
                ),
                "noul": NoulQuestion(instructions="Is it present?"),
            },
        )
        assert result.answers["choice"].choice == "b"
        assert result.answers["score"].score == 1
        assert result.answers["noul"].noul == 0.75
        assert transport.calls[0]["model"] == "jev-latest"

    @pytest.mark.parametrize(
        "body, reason",
        [
            (b"not json", "response_not_json"),
            (
                json.dumps(
                    {
                        "model": "jev",
                        "answers": {},
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                ).encode(),
                "response_answer_ids_mismatch",
            ),
            (
                json.dumps(
                    {
                        "model": "jev",
                        "answers": {
                            "q": {
                                "type": "choice",
                                "choice": "invented",
                                "probabilities": {"a": 0.5, "b": 0.5},
                                "confidence": 0,
                            }
                        },
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                ).encode(),
                "response_choice_unknown",
            ),
            (
                json.dumps(
                    {
                        "model": "jev",
                        "answers": {
                            "q": {
                                "type": "choice",
                                "choice": "a",
                                "probabilities": {"a": 0.2, "b": 0.2},
                                "confidence": 0,
                            }
                        },
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                ).encode(),
                "response_probabilities_do_not_sum_to_one",
            ),
        ],
    )
    def test_malformed_provider_output_fails_closed(self, body, reason):
        client = SystemOneClient(
            api_key="key",
            base_url="https://typesafe.example",
            transport=ScriptedTransport(body),
        )
        with pytest.raises(SystemOneError, match=reason):
            client.evaluate(
                "state",
                {
                    "q": ChoiceQuestion(
                        instructions="Pick", criteria={"a": None, "b": None}
                    )
                },
            )

    def test_an_inconsistent_weighted_score_fails_closed(self):
        body = json.dumps(
            {
                "model": "jev",
                "answers": {
                    "q": {
                        "type": "score",
                        "score": 0.0,
                        "legend": {"0": "low", "1": "high"},
                        "probabilities": {"0": 0.0, "1": 1.0},
                        "confidence": 1.0,
                    }
                },
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode()
        client = SystemOneClient(
            api_key="key",
            base_url="https://typesafe.example",
            transport=ScriptedTransport(body),
        )
        with pytest.raises(SystemOneError, match="response_score_inconsistent"):
            client.evaluate(
                "state",
                {
                    "q": ScoreQuestion(
                        instructions="Rate", criteria=["low", "high"]
                    )
                },
            )

    def test_independently_rounded_live_score_is_accepted(self):
        body = json.dumps(
            {
                "model": "jev-1.13.0",
                "answers": {
                    "q": {
                        "type": "score",
                        "score": 0.03,
                        "legend": {
                            "0": "barrier",
                            "1": "uncertain",
                            "2": "improvement",
                            "3": "broad improvement",
                        },
                        "probabilities": {
                            "0": 0.98,
                            "1": 0.02,
                            "2": 0.0,
                            "3": 0.0,
                        },
                        "confidence": 0.97,
                    }
                },
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode()
        client = SystemOneClient(
            api_key="key",
            base_url="https://typesafe.example",
            transport=ScriptedTransport(body),
        )
        result = client.evaluate(
            "state",
            {
                "q": ScoreQuestion(
                    instructions="Rate",
                    criteria=[
                        "barrier",
                        "uncertain",
                        "improvement",
                        "broad improvement",
                    ],
                )
            },
        )
        assert result.answers["q"].score == 0.03


class TestPerceptionAndEvidence:
    def test_classifies_only_into_the_closed_object_set(self):
        jev, transport = intelligence({"decision": "ramp"})
        result = jev.classify_uncertain_object({"label": "sloped plane"})
        assert result.value == "ramp"
        assert set(transport.calls[0]["questions"]["decision"]["criteria"]) == {
            "ramp", "stair", "curb", "threshold", "furniture", "unknown"
        }

    def test_selects_an_evidence_action(self):
        jev, _ = intelligence({"decision": "tape_measurement"})
        result = jev.choose_evidence_action("threshold height is visually ambiguous")
        assert result.value == "tape_measurement"

    def test_detects_contradictions_and_missing_evidence_in_one_call(self):
        jev, transport = intelligence({"contradiction": 0.9, "missing": 0.8})
        result = jev.audit_evidence([{"roomplan": "table", "owner": "ramp"}])
        assert result.needs_review
        assert result.contradiction_probability == 0.9
        assert len(transport.calls) == 1

    def test_extracts_multi_label_semantic_features(self):
        jev, _ = intelligence(
            {"route_obstruction": 0.95, "customer_impact": 0.8}
        )
        result = jev.extract_semantic_features(
            "A customer could not pass the boxes in the aisle."
        )
        assert result.present() == ("route_obstruction", "customer_impact")


class TestRoutingAndGoals:
    def test_routes_to_the_existing_measurement_executor(self):
        jev, _ = intelligence({"decision": "MEASURE"})
        result = jev.route_measurement_question("How tall is the service counter?")
        assert result.kind == "MEASURE"
        assert result.executor is EXECUTORS["MEASURE"]

    def test_interprets_preserved_chair_count_without_inventing_the_number(self):
        jev, _ = intelligence(
            {"kind": "preserve_count", "target": "waiting chairs"}
        )
        result = jev.interpret_owner_goal(
            "Keep six waiting chairs", known_targets=["waiting chairs", "plumbing"]
        )
        assert result.kind == "preserve_count"
        assert result.target == "waiting chairs"
        assert result.quantity == 6

    def test_interprets_do_not_move_plumbing(self):
        jev, _ = intelligence(
            {"kind": "preserve_fixture", "target": "plumbing"}
        )
        result = jev.interpret_owner_goal(
            "Don't move plumbing", known_targets=["waiting chairs", "plumbing"]
        )
        assert result.kind == "preserve_fixture"
        assert result.target == "plumbing"
        assert result.quantity is None

    def test_routes_confidence_with_caller_calibrated_thresholds(self):
        thresholds = ConfidenceThresholds(
            reasoning_model_min=0.55, automatic_min=0.85
        )
        assert (
            AccessibilityIntelligence.route_uncertain_case(0.9, thresholds)
            == "automatic"
        )
        assert (
            AccessibilityIntelligence.route_uncertain_case(0.7, thresholds)
            == "reasoning_model"
        )
        assert AccessibilityIntelligence.route_uncertain_case(0.3, thresholds) == "human"


class TestRanking:
    def test_ranks_validated_layout_ids_by_usability_and_disruption(self):
        jev, transport = intelligence(
            {"u_0": 3, "d_0": 0, "u_1": 2, "d_1": 1}
        )
        candidates = [
            LayoutCandidate(
                id="layout-a",
                deterministic_checks_passed=True,
                usability_evidence={"passed_routes": 8},
                disruption_evidence={"chairs_removed": 0},
            ),
            LayoutCandidate(
                id="layout-b",
                deterministic_checks_passed=True,
                usability_evidence={"passed_routes": 6},
                disruption_evidence={"chairs_removed": 1},
            ),
        ]
        result = jev.rank_layouts(candidates)
        assert [item.candidate.id for item in result] == ["layout-a", "layout-b"]
        assert len(transport.calls) == 1

    def test_prioritizes_findings_by_customer_impact(self):
        jev, _ = intelligence({"finding_0": 1, "finding_1": 3})
        result = jev.prioritize_findings(
            [finding(1, "Minor inconvenience"), finding(2, "Entrance blocked")]
        )
        assert result[0].finding.title == "Entrance blocked"

    def test_clusters_each_failure_into_a_recurring_pattern(self):
        jev, _ = intelligence(
            {
                "failure_0": "narrow_route",
                "failure_1": "narrow_route",
                "failure_2": "turning_space",
            }
        )
        result = jev.cluster_failures(
            [
                {"id": "a", "reason": "width"},
                {"id": "b", "reason": "clearance"},
                {"id": "c", "reason": "turn"},
            ]
        )
        assert result[0].pattern == "narrow_route"
        assert result[0].case_ids == ("a", "b")

    def test_reranks_only_the_retrieved_regulation_candidates(self):
        jev, _ = intelligence({"source_0": 1, "source_1": 3})
        candidates = [
            RegulationCandidate(
                id="general",
                citation="Guide intro",
                text="General accessibility context",
                source_kind="guidance",
            ),
            RegulationCandidate(
                id="route-width",
                citation="2010 ADA 403.5.1",
                text="Clear width of walking surfaces",
                source_kind="regulation",
            ),
        ]
        result = jev.rerank_regulations(finding(1, "Route too narrow"), candidates)
        assert [item.candidate.id for item in result] == ["route-width", "general"]


class TestVerificationAndGuardrails:
    def test_verifies_claim_support_and_requires_both_evidence_kinds(self):
        jev, _ = intelligence(
            {"measurement_0": 0.95, "rule_0": 0.9, "citation_0": 0.9}
        )
        claim = ReportClaim(
            id="claim-1",
            text="The route measured 32 inches against a 36-inch requirement.",
            measurements=[
                MeasurementEvidence(
                    id="route-width",
                    description="Tightest clear route width",
                    value=32,
                    unit="in",
                    quality="measured",
                )
            ],
            cited_rules=[
                RuleEvidence(
                    id="route-clear-width",
                    citation="2010 ADA 403.5.1",
                    source_text="The clear width shall be 36 inches minimum.",
                    human_verified=True,
                )
            ],
        )
        result = jev.verify_report_claims([claim], support_threshold=0.85)
        assert result[0].supported

    def test_missing_citation_fails_even_if_the_model_scores_support_high(self):
        jev, _ = intelligence(
            {"measurement_0": 0.99, "rule_0": 0.99, "citation_0": 0.99}
        )
        claim = ReportClaim(
            id="claim-1",
            text="The route is compliant.",
            measurements=[
                MeasurementEvidence(
                    id="route-width",
                    description="Tightest clear route width",
                    value=40,
                    unit="in",
                    quality="measured",
                )
            ],
            cited_rules=[],
        )
        assert not jev.verify_report_claims([claim], support_threshold=0.8)[0].supported

    def test_owner_text_guard_flags_legal_overstatement(self):
        jev, _ = intelligence({"overstatement": 0.9, "legal_guarantee": 0.95})
        result = jev.guard_owner_text(
            "This guarantees your shop is ADA compliant.", {"screening": True}
        )
        assert not result.allowed

    def test_downstream_guard_combines_semantic_and_tool_allowlist_checks(self):
        thresholds = ConfidenceThresholds(
            reasoning_model_min=0.5, automatic_min=0.9
        )
        jev, _ = intelligence({"unsupported": 0.01, "unsafe": 0.01, "bad_tool": 0.01})
        safe = jev.guard_downstream_output(
            {"tool_name": "measure_route", "claim": "screen this route"},
            evidence={"route": "measured"},
            allowed_tools=["measure_route"],
            thresholds=thresholds,
        )
        assert safe.allowed

        jev, _ = intelligence({"unsupported": 0.01, "unsafe": 0.01, "bad_tool": 0.01})
        unknown_tool = jev.guard_downstream_output(
            {"tool_name": "delete_shop"},
            evidence={},
            allowed_tools=["measure_route"],
            thresholds=thresholds,
        )
        assert not unknown_tool.allowed
        assert unknown_tool.deterministic_tool_violation
        assert unknown_tool.route == "human"

    def test_batches_are_capped_before_any_provider_call(self):
        jev, transport = intelligence()
        with pytest.raises(ValueError, match="cannot exceed 100"):
            jev.cluster_failures([{"id": str(index)} for index in range(101)])
        assert transport.calls == []
