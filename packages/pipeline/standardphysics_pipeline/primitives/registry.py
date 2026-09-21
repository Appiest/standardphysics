"""The vocabulary. Every primitive registers here, and nothing else is callable.

Two things read this registry. A rule names a primitive and its arguments
instead of carrying a hand-written check, and a planner is shown the whole list
and composes an answer out of it. Adding a primitive widens both at once, which
is the reason the registry exists rather than two parallel lists.

A call is fail-closed. An unknown name, an argument that does not validate, or
a node id that is not in this room is refused before any geometry runs, so a
model cannot reach past the vocabulary it was given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, TypeVar

from pydantic import BaseModel, ValidationError
from standardphysics_contracts import PrimitiveResult, PrimitiveSpec, SceneGraph

Arguments = TypeVar("Arguments", bound=BaseModel)
Returns = Literal["quantity", "nodes", "texts", "truth"]


class UnknownPrimitive(LookupError):
    """A name that is not in the vocabulary."""


class BadArguments(ValueError):
    """Arguments that do not fit the primitive's schema."""


@dataclass(frozen=True)
class Context:
    """Everything a primitive may read.

    `measure` is optional because many primitives only need the graph, and a
    caller with no measurement provider should still get those rather than
    nothing.
    """

    graph: SceneGraph
    measure: object | None = None
    scenario: object | None = None


@dataclass(frozen=True)
class Primitive:
    name: str
    summary: str
    returns: Returns
    arguments: type[BaseModel]
    run: Callable[[BaseModel, Context], PrimitiveResult]

    def spec(self) -> PrimitiveSpec:
        return PrimitiveSpec(
            name=self.name,
            summary=self.summary,
            arguments=self.arguments.model_json_schema(),
            returns=self.returns,
        )


REGISTRY: dict[str, Primitive] = {}


def primitive(name: str, summary: str, returns: Returns, arguments: type[BaseModel]):
    """Register one primitive under a name a rule or a plan can use."""

    def register(run: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"{name} is already a primitive")
        REGISTRY[name] = Primitive(name=name, summary=summary, returns=returns, arguments=arguments, run=run)
        return run

    return register


def vocabulary() -> list[PrimitiveSpec]:
    """Every primitive, as a planner is shown them."""
    return [REGISTRY[name].spec() for name in sorted(REGISTRY)]


def call(name: str, arguments: dict, context: Context) -> PrimitiveResult:
    """Run one primitive, or refuse for a reason the caller can act on."""
    found = REGISTRY.get(name)
    if found is None:
        raise UnknownPrimitive(name)
    try:
        parsed = found.arguments.model_validate(arguments)
    except ValidationError as exc:
        raise BadArguments(f"{name}: {exc.error_count()} argument problem(s)") from exc
    return found.run(parsed, context)
