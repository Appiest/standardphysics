"""The ask box: any question about the shop, answered from the measured model.

The line these tests hold is that the model picks the question and the code
supplies the facts. A resolver can be wrong about what somebody meant. Nothing
in here lets it be wrong about how tall the counter is.
"""

from __future__ import annotations

import json

import pytest
from standardphysics_agents.ask import (
    EXECUTORS,
    KINDS,
    Answer,
    AskContext,
    KeywordResolver,
    ModelResolver,
    Query,
    arrangement,
    ask,
    catalogue,
    parse_query,
    query_schema,
    resolver,
)
from standardphysics_agents.ask.shapes import group
from standardphysics_agents.evaluation import variants as v
from standardphysics_agents.fix import violations
from standardphysics_agents.models import PROVIDER_ROUTING, OpenRouter
from standardphysics_contracts import to_inches
from standardphysics_fixtures.shop import node_id

COUNTER = node_id("counter")
TABLE_1 = node_id("table_1")
CHAIR_1 = node_id("chair_1")


@pytest.fixture
def answer(graph, scenario, pipeline, pack, ledger):
    def run(text: str) -> Answer:
        return ask(
            text, graph, scenario, pipeline,
            rules=pack, ledger=ledger, with_resolver=KeywordResolver(),
        )

    return run


@pytest.fixture
def context(graph, scenario, pipeline, pack, ledger):
    return AskContext(
        graph=graph, scenario=scenario, measure=pipeline, rules=pack, ledger=ledger
    )


class FakeModel:
    """An OpenRouter client that returns whatever the test wrote."""

    def __init__(self, content) -> None:
        self.content = content
        self.requests: list[dict] = []
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.content, Exception):
            raise self.content
        message = type("M", (), {"content": self.content})()
        choice = type("C", (), {"message": message})()
        return type(
            "R", (), {"choices": [choice], "provider": "OpenAI", "model": "test-model"}
        )()


def test_there_is_an_executor_for_every_kind_of_question():
    """KINDS became a registry keyed by name, so it is compared by its keys."""
    assert set(EXECUTORS) == set(KINDS)


class TestTheQuestionShape:
    def test_the_schema_comes_from_the_model(self):
        """Generated, never written by hand, so it cannot drift from Query."""
        assert set(query_schema()["properties"]) == set(Query.model_fields)

    def test_prose_instead_of_a_question(self, graph):
        assert parse_query("how many chairs", graph).reason == (
            "question_not_an_object"
        )

    def test_a_kind_outside_the_set(self, graph):
        """Still refused, now for the reason that names it.

        A closed type used to throw this out while checking the shape, before the
        check written for exactly this case could run. With kinds registered by
        their executors, that check is what catches it.
        """
        payload = {"kind": "PAINT_IT", "restated": "Paint the walls."}
        assert parse_query(payload, graph).reason == "unknown_question"

    def test_a_count_naming_nothing(self, graph):
        assert parse_query({"kind": "COUNT", "restated": "How many?"}, graph).reason == (
            "question_names_nothing"
        )

    def test_a_measurement_that_does_not_say_which_dimension(self, graph):
        payload = {
            "kind": "MEASURE",
            "subject_labels": ["table"],
            "restated": "Tell me about the table.",
        }
        assert parse_query(payload, graph).reason == (
            "question_does_not_say_which_dimension"
        )

    def test_a_distance_with_only_one_end(self, graph):
        payload = {
            "kind": "DISTANCE",
            "subject_labels": ["counter"],
            "restated": "How far is the counter?",
        }
        assert parse_query(payload, graph).reason == (
            "question_names_only_one_end"
        )

    def test_a_space_question_with_no_size(self, graph):
        payload = {"kind": "SPACE", "thing": "couch", "restated": "Room for a couch?"}
        assert parse_query(payload, graph).reason == (
            "question_does_not_say_how_big"
        )

    def test_a_rearrangement_with_no_direction(self, graph):
        payload = {
            "kind": "REARRANGE",
            "subject_labels": ["tables"],
            "restated": "Move the tables.",
        }
        assert parse_query(payload, graph).reason == (
            "question_does_not_say_which_way"
        )

    def test_a_rearrangement_of_something_built_in(self, graph):
        payload = {
            "kind": "REARRANGE",
            "subject_node_ids": [str(COUNTER)],
            "direction": "back",
            "restated": "Move the counter back.",
        }
        assert parse_query(payload, graph).reason == (
            "asked_to_move_something_fixed"
        )

    def test_a_question_about_a_piece_that_is_not_here(self, graph):
        payload = {
            "kind": "WHERE",
            "subject_node_ids": ["11111111-2222-3333-4444-555555555555"],
            "restated": "Where is it?",
        }
        assert parse_query(payload, graph).reason == (
            "asked_about_something_that_is_not_here"
        )

    def test_an_absurd_measurement(self, graph):
        payload = {
            "kind": "SPACE",
            "thing": "couch",
            "length_inches": 99999.0,
            "restated": "Room for a couch?",
        }
        assert parse_query(payload, graph).reason == "measurement_out_of_range"

    def test_a_question_nobody_restated(self, graph):
        payload = {"kind": "COUNT", "subject_labels": ["chairs"], "restated": " "}
        assert parse_query(payload, graph).reason == "question_was_not_restated"

    def test_a_question_that_reads_properly(self, graph):
        payload = {
            "kind": "COUNT",
            "subject_labels": ["chairs"],
            "restated": "How many chairs are there?",
        }
        assert isinstance(parse_query(payload, graph), Query)


class TestTheModelCall:
    def test_it_goes_through_openrouter_with_the_provider_pinned(self, graph, scenario):
        payload = json.dumps(
            {"kind": "COUNT", "subject_labels": ["chairs"], "restated": "How many?"}
        )
        client = FakeModel(payload)
        query = ModelResolver(OpenRouter(api_key="k", client=client)).resolve(
            "how many chairs have I got", graph, scenario
        )
        assert isinstance(query, Query)
        sent = client.requests[0]
        assert sent["extra_body"]["provider"] == PROVIDER_ROUTING
        assert sent["response_format"]["json_schema"]["schema"] == query_schema()

    def test_the_prompt_carries_no_key_and_no_transforms(self, graph, scenario):
        client = FakeModel('{"kind": "COUNT", "subject_labels": ["x"], "restated": "y"}')
        ModelResolver(OpenRouter(api_key="secret-key", client=client)).resolve(
            "how many", graph, scenario
        )
        sent = json.dumps(client.requests[0])
        assert "secret-key" not in sent
        assert "transform" not in sent

    def test_the_shop_is_described_in_its_own_terms(self, graph, scenario):
        pieces = catalogue(graph, scenario)
        assert pieces
        assert set(pieces[0]) == {
            "id", "label", "movable", "toward_back_inches", "toward_right_inches",
            "width_inches", "depth_inches", "height_inches",
        }

    def test_every_piece_carries_what_it_measures(self, graph, scenario):
        """So a piece can be picked out by what it is like, not what it is called.

        Given only labels, the one way to answer "the two desks" is to match the
        word, and a scan that called them tables then has no desks in it.
        """
        for piece in catalogue(graph, scenario):
            assert piece["width_inches"] > 0
            assert piece["height_inches"] > 0

    def test_prose_back_from_the_model_answers_nothing(self, graph, scenario):
        client = FakeModel("You have six chairs")
        answer = ModelResolver(OpenRouter(api_key="k", client=client)).resolve(
            "how many chairs", graph, scenario
        )
        assert answer.reason == "model_returned_not_json"

    def test_a_model_that_invents_a_measurement_cannot_get_it_into_an_answer(
        self, graph, scenario, pipeline, pack, ledger
    ):
        """The model picks the question. It does not supply the facts."""
        client = FakeModel(
            json.dumps(
                {
                    "kind": "MEASURE",
                    "subject_labels": ["counter"],
                    "dimension": "height",
                    "height_inches": 12.0,
                    "restated": "How tall is the counter?",
                }
            )
        )
        answer = ask(
            "how tall is the counter", graph, scenario, pipeline,
            rules=pack, ledger=ledger,
            with_resolver=ModelResolver(OpenRouter(api_key="k", client=client)),
        )
        counter = graph.by_id(COUNTER)
        measured = to_inches(
            counter.transform.position.z + counter.dimensions.z / 2
        )
        assert "12 inches" not in answer.text
        assert f"{round(measured)}" in answer.text

    def test_a_model_that_is_down_answers_nothing(self, graph, scenario):
        client = FakeModel(RuntimeError("gateway timeout"))
        answer = ModelResolver(OpenRouter(api_key="k", client=client)).resolve(
            "how many chairs", graph, scenario
        )
        assert answer.reason.startswith("model_error:")

    def test_no_key_falls_back_to_something_labelled(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert resolver().provider == "local_keywords"

    def test_a_key_uses_the_model(self):
        assert resolver(OpenRouter(api_key="k", client=FakeModel("{}"))).provider == (
            "openrouter"
        )


class TestCounting:
    def test_how_many_chairs_do_i_have(self, answer):
        result = answer("how many chairs do I have?")
        assert result.kind == "COUNT"
        assert result.text == "You have six chairs."
        assert result.data["total"] == 6

    def test_it_points_the_camera_at_them(self, answer):
        result = answer("how many chairs do I have?")
        assert result.locus is not None
        assert len(result.locus.node_ids) == 6

    def test_a_count_of_none_says_it_cannot_tell_rather_than_zero(self, answer):
        """Changed on purpose, because what it asserted was the wrong behaviour.

        It used to require "You have no sofas", which states as a fact something
        the scan never looked for: a model names what the scanner recognised, so
        the absence of a word is not the absence of the thing. The same defect
        told a library with fifteen hundred books that it had none.
        """
        result = answer("how many sofas do I have?")
        assert result.rejected == "could_not_establish"
        assert "does not have a name for sofas" in result.text

    def test_it_counts_doors_when_asked_about_doors(self, answer):
        assert answer("how many doors do I have?").data["total"] == 1


class TestMeasuring:
    def test_how_tall_is_my_table(self, answer):
        result = answer("how tall is my table")
        assert result.kind == "MEASURE"
        assert result.text == "The four tables are 29.5 inches tall."

    def test_how_wide_is_my_counter(self, answer):
        assert "126 inches wide" in answer("how wide is my counter").text

    def test_how_big_is_the_room(self, answer):
        result = answer("how big is the room?")
        assert result.text == "The room is 26 feet 3 inches by 19 feet 8 inches of floor."

    def test_height_is_measured_from_the_floor(self, context):
        """A raised shelf is as tall as its top edge, not as its own box."""
        from standardphysics_agents.ask.dimensions import height_inches

        counter = context.graph.by_id(COUNTER)
        top = counter.transform.position.z + counter.dimensions.z / 2
        assert height_inches(counter) == pytest.approx(to_inches(top))

    def test_it_reports_a_range_when_they_differ(
        self, graph, scenario, pipeline, pack, ledger
    ):
        mixed = v.counter_height(graph, 30.0)
        result = ask(
            "how tall is the counter", mixed, scenario, pipeline,
            rules=pack, ledger=ledger, with_resolver=KeywordResolver(),
        )
        assert "30 inches tall" in result.text

    def test_something_the_scan_never_saw(self, answer):
        result = answer("how tall is my sofa")
        assert "no sofas" in result.text
        assert "Point at the piece" in result.text


class TestDistance:
    def test_how_far_from_the_counter_to_the_display_case(self, answer):
        result = answer("how far is it from the counter to the display case?")
        assert result.kind == "DISTANCE"
        assert "feet" in result.text
        assert result.data["inches"] > 0

    def test_it_measures_the_gap_not_the_middles(self, answer, context):
        from standardphysics_pipeline import gap_between_nodes

        result = answer("how far is it from the counter to the display case?")
        first, second = (context.graph.by_id(n) for n in result.subjects)
        assert result.data["inches"] == pytest.approx(
            to_inches(gap_between_nodes(first, second)), abs=0.01
        )


class TestWhere:
    def test_where_is_the_counter(self, answer):
        result = answer("where is the counter?")
        assert result.kind == "WHERE"
        assert "against the back wall" in result.text

    def test_it_says_what_is_next_to_it(self, answer):
        assert "The nearest thing to it is" in answer("where is the counter?").text

    def test_something_out_on_the_floor_says_so(self, answer):
        assert "out on the floor" in answer("where is the table?").text


class TestShape:
    def test_how_would_you_describe_my_table_arrangement(self, answer):
        result = answer("how could I describe my table arrangement as a shape?")
        assert result.kind == "DESCRIBE"
        assert result.data["shape"] == "two_rows"
        assert "two rows of two" in result.text

    def test_the_chairs_make_a_grid(self, answer):
        result = answer("how are my chairs laid out?")
        assert result.data["shape"] == "grid"
        assert "by" in result.text

    def test_one_piece_stands_on_its_own(self, context):
        from standardphysics_agents.ask.shapes import arrangement

        counter = context.graph.by_id(COUNTER)
        assert arrangement([counter], context).shape == "one"

    def test_a_row_is_a_row(self, graph, scenario, pipeline, pack, ledger):
        shelves = v.add(
            v.clear_counter_side(graph),
            *[
                v.box(f"shelf_{i}", "Shelf", (-2.6, -2.0 + i * 1.2, 0.5), (0.4, 0.8, 1.0))
                for i in range(4)
            ],
        )
        result = ask(
            "describe the shelves", shelves, scenario, pipeline,
            rules=pack, ledger=ledger, with_resolver=KeywordResolver(),
        )
        assert result.data["shape"] == "row"
        assert "one row along" in result.text

    def test_grouping_splits_on_a_real_gap(self):
        assert group([0.0, 0.1, 5.0, 5.1], tolerance=1.0) == [[0.0, 0.1], [5.0, 5.1]]

    def test_grouping_keeps_a_steady_run_together(self):
        assert group([0.0, 0.5, 1.0, 1.5], tolerance=1.0) == [[0.0, 0.5, 1.0, 1.5]]

    def test_the_shape_comes_from_geometry_and_not_from_a_model(self, context):
        """Whatever picked the question, the pattern is measured."""
        tables = [
            context.graph.by_id(node_id(f"table_{i}")) for i in range(1, 5)
        ]
        found = arrangement(tables, context)
        assert found.rows == 2
        assert found.columns == 2
        assert found.row_spacing_inches == pytest.approx(181.1, abs=1.0)


class TestStandards:
    def test_is_my_front_door_wide_enough(self, answer):
        result = answer("is my front door wide enough?")
        assert result.kind == "CHECK"
        assert "Measure the front doorway" in result.text
        assert result.data["citation"].endswith("404.2.3")

    def test_it_gives_the_same_sentence_as_the_report(self, answer, context):
        result = answer("is my front door wide enough?")
        finding = next(
            f for f in context.baseline().findings if f.check_id == "door_clear_width"
        )
        assert finding.title in result.text
        assert finding.detail in result.text

    def test_something_nothing_covers_says_what_is_covered(self, answer):
        result = answer("is my sofa accessible?")
        assert "We measure" in result.text


class TestSpace:
    def test_a_length_on_its_own_asks_for_the_depth(self, answer):
        result = answer("do I have space for a 97 inch couch?")
        assert result.kind == "SPACE"
        assert result.data["missing"] == ["depth_inches", "height_inches"]
        assert "how deep" in result.text

    def test_something_small_finds_a_place(
        self, graph, scenario, pipeline, pack, ledger
    ):
        result = ask(
            "do I have room for a 36 by 18 by 18 inch bench?",
            graph, scenario, pipeline,
            rules=pack, ledger=ledger, with_resolver=KeywordResolver(),
        )
        assert result.data["fits"]
        assert "goes against the wall" in result.text

    def test_the_placement_it_offers_breaks_nothing(
        self, graph, scenario, pipeline, pack, ledger
    ):
        result = ask(
            "do I have room for a 36 by 18 by 18 inch bench?",
            graph, scenario, pipeline,
            rules=pack, ledger=ledger, with_resolver=KeywordResolver(),
        )
        added = {n.id for n in result.graph.nodes} - {n.id for n in graph.nodes}
        assert violations(graph, result.graph, added=frozenset(added)) == []

    def test_it_never_shrinks_what_was_asked_for(
        self, graph, scenario, pipeline, pack, ledger
    ):
        result = ask(
            "do I have room for a 300 by 38 by 33 inch couch?",
            graph, scenario, pipeline,
            rules=pack, ledger=ledger, with_resolver=KeywordResolver(),
        )
        assert not result.data["fits"]
        assert result.data["length_inches"] == 300.0
        assert result.graph is None


class TestRearranging:
    def test_it_keeps_every_piece_of_furniture(self, answer):
        result = answer("spread the display cases apart")
        assert result.kind == "REARRANGE"
        assert result.proposal is not None
        assert result.data["inventory_before"] == result.data["inventory_after"]

    def test_it_breaks_no_hard_constraint(self, answer, graph):
        result = answer("spread the display cases apart")
        assert violations(graph, result.graph) == []

    def test_asking_to_move_a_built_in_says_which_to_move_instead(self, answer):
        result = answer("move the ordering counter to the left")
        assert "built in" in result.text
        assert result.proposal is None

    def test_a_request_with_nowhere_to_go_says_what_is_in_the_way(self, answer):
        """Each display case has about six inches of room to its wall. Sixty
        inches apart puts both of them through the plaster."""
        result = answer("spread the display cases 60 inches apart")
        assert result.proposal is None
        assert "Try a shorter move" in result.text
        assert result.text[0].isupper()

    def test_a_stated_distance_is_read_off_the_words(self, graph, scenario):
        query = KeywordResolver().resolve(
            "spread the display cases 60 inches apart", graph, scenario
        )
        assert query.kind == "REARRANGE"
        assert query.direction == "apart"
        assert query.distance_inches == 60.0


class TestQuestionsItCannotRead:
    def test_a_question_about_paint(self, answer):
        result = answer("what colour should I paint the walls")
        assert not result.understood
        assert "You can ask how many" in result.text

    def test_a_greeting(self, answer):
        assert not answer("hello there").understood

    def test_it_never_guesses_an_answer(self, answer):
        result = answer("what colour should I paint the walls")
        assert result.data == {}
        assert result.locus is None


class TestWhichCheckAQuestionIsAbout:
    def test_the_earliest_topic_wins(self):
        from standardphysics_agents.ask.standards import topics_in

        """The path is the subject; the counter is where the path goes."""
        assert "route_clear_width" in topics_in("is the path to the counter wide enough")
        assert "service_counter_height" not in topics_in(
            "is the path to the counter wide enough"
        )

    def test_a_question_about_the_counter_finds_the_counter_checks(self):
        from standardphysics_agents.ask.standards import topics_in

        assert topics_in("is my counter up to code") == {
            "service_counter_height",
            "service_counter_approach",
        }

    def test_words_with_no_topic_find_nothing(self):
        from standardphysics_agents.ask.standards import topics_in

        assert topics_in("what colour are the walls") == frozenset()

    def test_a_question_about_a_path_answers_about_a_path(self, answer):
        result = answer("can a wheelchair get past the display cases")
        assert "route_clear_width" in result.data["checks"]
        assert "service_counter_height" not in result.data["checks"]
        assert "path" in result.text

    def test_a_question_about_the_door_leads_with_the_width(self, answer):
        """Not with the photograph we also want of its handle."""
        result = answer("is my front door wide enough")
        assert result.data["citation"].endswith("404.2.3")
        assert "Measure the front doorway" in result.text

    def test_turning_round_is_measured_even_where_the_rule_is_silent(self, answer):
        """304.3 asks for a turning space where one is required, which is at a
        dead end. Somebody standing at the counter has still asked a question."""
        result = answer("can someone turn around at the counter")
        assert result.data["required_inches"] == 60.0
        assert result.data["measured_inches"] > 0
        assert "Turning a wheelchair round needs 60 inches" in result.text

    def test_it_claims_no_failure_when_it_measures_on_request(self, answer):
        result = answer("can someone turn around at the counter")
        assert "too" not in result.text
        assert result.findings == ()
