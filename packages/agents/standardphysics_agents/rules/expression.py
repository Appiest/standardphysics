"""A rule that names its measurement instead of carrying a hand-written check.

Eighteen rules exist because each one needs a function in `checks/` that knows
which surfaces to measure. A rule written as an expression names primitives
from the shared vocabulary instead, and one evaluator runs any of them, so a
provision is added by writing down what it requires.

The shape is deliberately not "one primitive and a threshold". Real provisions
pick out elements, sometimes apply only under a condition, and only then
measure:

    select    which nodes this provision is about
    when      an optional condition, asked per element
    measure   the number to take from each element that survives

`$element` in an argument is the node being considered. Nothing else is
substituted, so an expression can reach no further than the element it was
handed.

Provisions too tangled for this shape keep a hand-written check, declared as
such on the rule. That is an escape hatch with a name, not the default.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from standardphysics_contracts import PrimitiveResult, Quantity

ELEMENT = "$element"


class Step(BaseModel):
    """One primitive call, with the element bound where `$element` appears."""

    model_config = ConfigDict(extra="forbid")
    primitive: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def bind(self, element: UUID | None) -> dict[str, Any]:
        if element is None:
            return dict(self.arguments)
        return {key: str(element) if value == ELEMENT else value for key, value in self.arguments.items()}


class RuleExpression(BaseModel):
    """What a provision asks, in terms the primitive vocabulary can run."""

    model_config = ConfigDict(extra="forbid")
    select: Step
    """Returns the nodes the provision is about."""
    when: Step | None = None
    """Asked per element. A false answer means the provision does not apply here."""
    measure: Step
    """Returns the number to compare against the threshold, per element."""


class Verdict(BaseModel):
    """What the evaluator found for one element."""

    model_config = ConfigDict(extra="forbid")
    rule_id: str
    element_id: UUID
    satisfied: bool | None
    """None when the measurement could not be taken, which is a question rather
    than a pass or a failure."""
    measured: float | None = None
    unit: str | None = None
    threshold: float
    result: PrimitiveResult


class ExpressionError(ValueError):
    """An expression that cannot run: a bad primitive, or the wrong return type."""


def _quantity(result: PrimitiveResult) -> Quantity | None:
    return result.payload if isinstance(result.payload, Quantity) else None


def evaluate(rule, expression: RuleExpression, context, call) -> list[Verdict]:
    """Run one provision over every element it picks out.

    `call` is the primitive runner, passed in so this stays testable without a
    room and so the evaluator never reaches for a registry of its own.
    """
    selected = call(expression.select.primitive, expression.select.bind(None), context)
    verdicts: list[Verdict] = []
    for element in selected.nodes():
        if not _applies(expression, element, context, call):
            continue
        verdicts.append(_verdict(rule, expression, element, context, call))
    return verdicts


def _applies(expression: RuleExpression, element: UUID, context, call) -> bool:
    if expression.when is None:
        return True
    answer = call(expression.when.primitive, expression.when.bind(element), context)
    return bool(getattr(answer.payload, "value", False))


def _verdict(rule, expression: RuleExpression, element: UUID, context, call) -> Verdict:
    result = call(expression.measure.primitive, expression.measure.bind(element), context)
    quantity = _quantity(result)
    if quantity is None or not result.measured:
        return Verdict(
            rule_id=rule.id,
            element_id=element,
            satisfied=None,
            threshold=rule.threshold,
            result=result,
        )
    if quantity.unit != rule.unit:
        raise ExpressionError(
            f"{rule.id} is written in {rule.unit} and {expression.measure.primitive} answers in {quantity.unit}"
        )
    return Verdict(
        rule_id=rule.id,
        element_id=element,
        satisfied=rule.satisfied_by(quantity.value),
        measured=quantity.value,
        unit=quantity.unit,
        threshold=rule.threshold,
        result=result,
    )
