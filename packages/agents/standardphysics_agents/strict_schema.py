"""JSON Schema in the subset a strict structured-output endpoint accepts.

Pydantic writes the schema this lane hands to a model, and `model_json_schema`
leaves any field with a default out of `required`. That is correct JSON Schema
and a 400 from OpenAI, whose strict mode wants every property of every object
listed in `required`, `additionalProperties: false` on every object, and none
of the keywords it does not implement. A field that is genuinely optional says
so by being nullable, which Pydantic already writes for `X | None`, so the only
thing missing is the requirement.

The schema still comes from the model. A second one written by hand is how a
valid answer stops parsing the first time either side changes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema

UNSUPPORTED = frozenset({"default", "format"})
"""Keywords strict mode refuses.

A default says nothing to a model that is being told it must supply the field,
and `format` is not in the accepted subset. Neither is a loss: the parse behind
the call is Pydantic's, which applies every format and every default itself.
"""


class EveryFieldRequired(GenerateJsonSchema):
    """Pydantic's generator, with optional fields still named as required.

    The endpoint reads `required` as "the model must emit this key", not as
    "the caller must have a value", so a nullable field belongs there too.
    """

    def field_is_required(self, field: Any, total: bool) -> bool:
        return True


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s own schema, in the shape a strict endpoint will take."""
    return _strict(model.model_json_schema(schema_generator=EveryFieldRequired))


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    shaped = {
        key: _strict(value) for key, value in node.items() if key not in UNSUPPORTED
    }
    if "properties" in shaped:
        shaped["required"] = list(shaped["properties"])
        shaped["additionalProperties"] = False
    return shaped


__all__ = ["UNSUPPORTED", "EveryFieldRequired", "strict_schema"]
