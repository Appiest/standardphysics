"""A provision written as data, run by one evaluator.

This is the mechanism that has to replace a hand-written check per rule. It is
proved here on a real provision: ADA 2010 904.4.1, a counter surface 36 inches
high at most.
"""

import uuid

import pytest
from standardphysics_agents.rules import RuleSpec
from standardphysics_agents.rules.expression import (
    ExpressionError,
    RuleExpression,
    Step,
    evaluate,
)
from standardphysics_contracts import Mat4, PrimitiveResult, SceneGraph, SceneNode, Truth, Vec3, to_meters
from standardphysics_pipeline.primitives import Context, call


def node(name, at, size, kind="object"):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"rule-{name}"),
        kind=kind,
        label=name.capitalize(),
        raw_category=name.replace(" ", "_"),
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*at),
    )


COUNTER_HEIGHT = RuleSpec(
    id="service_counter_height",
    title="The counter is too high to order from",
    citation={
        "authority": "ADA_2010",
        "edition": "2010 ADA Standards for Accessible Design",
        "section": "904.4.1",
    },
    threshold=36.0,
    unit="in",
    comparison="at_most",
    source_text="A portion of the counter surface 36 inches (915 mm) high maximum above the finish floor.",
)

COUNTER_EXPRESSION = RuleExpression(
    select=Step(primitive="find_objects", arguments={"words": "counter"}),
    measure=Step(primitive="height_of", arguments={"node_id": "$element"}),
)


def counter(name, height_inches):
    """A counter whose top sits this many inches above the floor."""
    height = to_meters(height_inches)
    return node(name, at=(0.0, 0.0, height / 2), size=(1.5, 0.6, height))


@pytest.fixture
def shop():
    floor = node("floor", at=(0, 0, 0), size=(6, 6, 0.01), kind="floor")
    return SceneGraph(
        scan_id=uuid.uuid4(),
        nodes=[floor, counter("high counter", 47.0), counter("low counter", 34.0), node("stool", (2, 2, 0.3), (0.4, 0.4, 0.6))],
    )


@pytest.fixture
def context(shop):
    return Context(graph=shop)


class TestOneEvaluatorRunsAProvision:
    def test_it_picks_out_only_the_elements_the_provision_is_about(self, context):
        verdicts = evaluate(COUNTER_HEIGHT, COUNTER_EXPRESSION, context, call)
        assert len(verdicts) == 2

    def test_a_counter_over_the_threshold_fails(self, shop, context):
        verdicts = {v.element_id: v for v in evaluate(COUNTER_HEIGHT, COUNTER_EXPRESSION, context, call)}
        high = next(n for n in shop.nodes if n.label == "High counter")
        assert verdicts[high.id].satisfied is False
        assert verdicts[high.id].measured == pytest.approx(47.0, abs=0.1)

    def test_a_counter_under_the_threshold_passes(self, shop, context):
        verdicts = {v.element_id: v for v in evaluate(COUNTER_HEIGHT, COUNTER_EXPRESSION, context, call)}
        low = next(n for n in shop.nodes if n.label == "Low counter")
        assert verdicts[low.id].satisfied is True

    def test_each_verdict_can_point_a_camera_at_what_it_measured(self, context):
        for verdict in evaluate(COUNTER_HEIGHT, COUNTER_EXPRESSION, context, call):
            assert verdict.result.evidence.at is not None
            assert verdict.element_id in verdict.result.evidence.subjects


class TestTheSameEvaluatorTakesADifferentNumber:
    def test_changing_only_the_threshold_changes_the_verdict(self, shop, context):
        """The next provision about a height is this one with a different number,
        which is the whole reason a rule is data."""
        stricter = COUNTER_HEIGHT.model_copy(update={"threshold": 30.0})
        verdicts = {v.element_id: v for v in evaluate(stricter, COUNTER_EXPRESSION, context, call)}
        low = next(n for n in shop.nodes if n.label == "Low counter")
        assert verdicts[low.id].satisfied is False


class TestWhenTheProvisionOnlySometimesApplies:
    def test_an_element_the_condition_rejects_is_not_judged(self, context):
        """`when` is how a provision that applies only in some cases is written
        without a bespoke check."""
        conditional = COUNTER_EXPRESSION.model_copy(
            update={"when": Step(primitive="never_true", arguments={})}
        )

        def answers_no(name, arguments, ctx):
            if name == "never_true":
                return PrimitiveResult(primitive=name, payload=Truth(value=False))
            return call(name, arguments, ctx)

        assert evaluate(COUNTER_HEIGHT, conditional, context, answers_no) == []


class TestWhatCannotBeMeasured:
    def test_an_unmeasurable_element_is_a_question_not_a_pass(self, context):
        """A verdict of None is what becomes a request for a photo or a number."""

        def unmeasurable(name, arguments, ctx):
            if name == "height_of":
                return PrimitiveResult(primitive=name, quality="not_measurable", note="The scan never saw the top.")
            return call(name, arguments, ctx)

        verdicts = evaluate(COUNTER_HEIGHT, COUNTER_EXPRESSION, context, unmeasurable)
        assert [v.satisfied for v in verdicts] == [None, None]
        assert all(v.measured is None for v in verdicts)

    def test_a_primitive_answering_in_the_wrong_unit_is_refused(self, shop, context):
        """Comparing square inches against a rule written in inches would pass
        or fail a counter for no reason anyone could explain."""
        area = COUNTER_EXPRESSION.model_copy(
            update={"measure": Step(primitive="floor_area_of", arguments={"node_id": "$element"})}
        )
        with pytest.raises(ExpressionError, match="answers in sq in"):
            evaluate(COUNTER_HEIGHT, area, context, call)


class TestBindingReachesNoFurtherThanTheElement:
    def test_only_the_element_placeholder_is_substituted(self):
        step = Step(primitive="height_of", arguments={"node_id": "$element", "axis": "$something_else"})
        element = uuid.uuid4()
        assert step.bind(element) == {"node_id": str(element), "axis": "$something_else"}
