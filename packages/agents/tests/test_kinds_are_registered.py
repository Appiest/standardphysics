"""Question kinds come from the executors that answer them.

They were a closed type written apart from the code, so the list of things a
person could ask lived somewhere nothing answered it. These check the registry
is the only source, in both directions.
"""

from __future__ import annotations

from importlib import import_module

ask = import_module("standardphysics_agents.ask")
query = import_module("standardphysics_agents.ask.query")
dimensions = import_module("standardphysics_agents.ask.dimensions")


def test_every_registered_kind_has_an_executor_and_every_executor_a_kind():
    assert set(query.KINDS) == set(ask.EXECUTORS)


def test_the_schema_offers_exactly_what_registered():
    kind = query.query_schema()["properties"]["kind"]
    assert kind["enum"] == sorted(query.KINDS)


def test_the_dimensions_offered_are_the_ones_a_reader_can_take():
    assert query.DIMENSIONS == set(dimensions.READERS) | set(dimensions.AREAS)


def test_a_new_executor_plugs_in_without_touching_a_type():
    """The point of the change: adding a kind is registering it."""
    name = "TEST_ONLY_KIND"
    try:
        ask.answers(name, lambda q, c: None)
        assert name in query.KINDS
        assert name in query.query_schema()["properties"]["kind"]["enum"]
    finally:
        ask.EXECUTORS.pop(name, None)
        query.KINDS.pop(name, None)


def test_a_kind_still_refuses_a_question_missing_what_it_needs():
    measure = query.KINDS["MEASURE"]
    missing = query.Query(kind="MEASURE", subject_labels=["table"], restated="x")
    assert measure.needs(missing) == "question_does_not_say_which_dimension"
