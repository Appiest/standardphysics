"""The schema handed to a model, against the rules a strict endpoint enforces.

Every model call in this lane asks the endpoint to enforce the schema rather
than hoping the answer fits. OpenAI rejected the raw Pydantic output with a 400
on every call, which the ask box swallowed into "we did not follow that one",
so the rules are tested here rather than discovered against a live key. None of
this needs an account.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel
from standardphysics_agents.ask.query import Query, parse_query, query_schema
from standardphysics_agents.router.decision import action_schema
from standardphysics_agents.strict_schema import UNSUPPORTED, strict_schema
from standardphysics_contracts import Decision

SCHEMAS = {"query": query_schema, "action": action_schema}


def objects(node) -> list[dict]:
    """Every object schema in the tree, including the ones under `$defs`."""
    if isinstance(node, list):
        return [found for item in node for found in objects(item)]
    if not isinstance(node, dict):
        return []
    here = [node] if "properties" in node else []
    return here + [found for value in node.values() for found in objects(value)]


@pytest.fixture(params=sorted(SCHEMAS))
def schema(request) -> dict:
    return SCHEMAS[request.param]()


class TestWhatStrictModeDemands:
    def test_every_property_is_required(self, schema):
        for obj in objects(schema):
            assert set(obj["required"]) == set(obj["properties"]), obj.get("title")

    def test_no_object_takes_extra_keys(self, schema):
        for obj in objects(schema):
            assert obj["additionalProperties"] is False, obj.get("title")

    def test_no_keyword_it_refuses_survives(self, schema):
        for keyword in UNSUPPORTED:
            assert f'"{keyword}"' not in json.dumps(schema)

    def test_there_is_an_object_to_check(self, schema):
        assert objects(schema)


class TestWhatOptionalStillMeans:
    def test_a_field_that_may_be_absent_is_nullable(self):
        """Required and nullable is how strict mode spells optional."""
        dimension = query_schema()["properties"]["dimension"]
        assert {"type": "null"} in dimension["anyOf"]

    def test_a_field_that_must_have_a_value_is_not(self):
        assert query_schema()["properties"]["kind"]["type"] == "string"

    def test_the_closed_sets_survive(self):
        assert set(query_schema()["properties"]["kind"]["enum"]) == {
            "COUNT", "MEASURE", "DISTANCE", "WHERE",
            "DESCRIBE", "SPACE", "REARRANGE", "CHECK",
        }


class TestTheParseIsUnchanged:
    """Requiring a key of the model is not requiring it of the parse.

    The local keyword resolver sends the two fields it can fill and leaves the
    rest out, so a schema change that made Pydantic stricter would break the
    fallback that runs when there is no key.
    """

    def test_a_payload_with_only_the_named_fields_still_parses(self, graph):
        parsed = parse_query({"kind": "COUNT", "subject_labels": ["table"], "restated": "how many tables"}, graph)
        assert parsed.kind == "COUNT"

    def test_a_kind_outside_the_set_is_still_rejected(self, graph):
        assert parse_query({"kind": "DEMOLISH", "restated": "knock it down"}, graph).reason

    def test_a_measurement_past_what_a_shop_can_be_is_still_rejected(self, graph):
        rejected = parse_query(
            {"kind": "SPACE", "thing": "case", "length_inches": 10000.0, "restated": "would it fit"},
            graph,
        )
        assert rejected.reason == "measurement_out_of_range"

    def test_a_kind_missing_its_argument_is_still_rejected(self, graph):
        rejected = parse_query(
            {"kind": "MEASURE", "subject_labels": ["table"], "restated": "how big"}, graph
        )
        assert rejected.reason == "question_does_not_say_which_dimension"


class TestItComesFromTheModel:
    def test_the_fields_are_the_model_fields(self):
        assert set(query_schema()["properties"]) == set(Query.model_fields)
        assert set(action_schema()["properties"]) == set(Decision.model_fields)

    def test_a_new_field_appears_without_touching_the_generator(self):
        class Sample(BaseModel):
            named: str
            optional: int | None = None

        schema = strict_schema(Sample)
        assert set(schema["required"]) == {"named", "optional"}

    def test_a_nested_model_is_strict_too(self):
        class Inner(BaseModel):
            deep: str | None = None

        class Outer(BaseModel):
            inner: Inner

        for obj in objects(strict_schema(Outer)):
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])
