"""The suite's own mechanics, checked against the scans in the repository.

Everything here reads a real room. The only things built by hand are the answers
being scored, which is what a test of a scorer is for; no scene in this file was
invented, because a suite that can be fooled by a made-up room proves nothing
about a suite whose whole job is to refuse made-up rooms.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

import pytest
from standardphysics_agents.evaluation import held_out
from standardphysics_agents.evaluation.held_out import questions as writing
from standardphysics_agents.evaluation.held_out import scenes as loading

ROOT = pathlib.Path(__file__).resolve().parents[3]


@dataclass
class Spoken:
    """Only the part of an answer the scorers read."""

    text: str
    data: dict[str, Any] = field(default_factory=dict)


@pytest.fixture(scope="module")
def scanned() -> list[held_out.Scene]:
    return held_out.real_scenes(ROOT)


class TestScenesAreReal:
    def test_the_repository_carries_scanned_rooms(self, scanned):
        assert scanned, "datasets/phone should hold at least one scan"
        assert all(scene.graph.nodes for scene in scanned)

    def test_a_room_that_arrived_twice_is_one_room(self, scanned):
        fingerprints = [scene.fingerprint() for scene in scanned]
        assert len(fingerprints) == len(set(fingerprints))

    def test_nothing_is_invented_when_there_is_nothing(self, tmp_path):
        with pytest.raises(held_out.NoRealScenes):
            held_out.real_scenes(tmp_path)

    def test_holding_out_more_rooms_than_exist_is_refused(self, scanned):
        with pytest.raises(held_out.NoRealScenes):
            held_out.split(scanned, seed=1, held_out=len(scanned))

    def test_the_two_halves_never_share_a_room(self, scanned):
        for seed in range(8):
            built, scored = held_out.split(scanned, seed=seed, held_out=1)
            assert not {scene.fingerprint() for scene in built} & {
                scene.fingerprint() for scene in scored
            }

    def test_a_region_shows_no_ontology(self, scanned):
        for region in scanned[0].regions():
            assert set(region) == {"id", "name", "width_m", "height_m", "depth_m", "centre_m"}


class TestScrambling:
    def test_every_name_changes(self, scanned):
        scene = scanned[0]
        scrambled = held_out.scramble(scene, seed=3)
        for before, after in zip(scene.graph.nodes, scrambled.graph.nodes):
            assert after.label != before.label
            assert after.raw_category != before.raw_category

    def test_no_measurement_changes(self, scanned):
        scene = scanned[0]
        scrambled = held_out.scramble(scene, seed=3)
        for before, after in zip(scene.graph.nodes, scrambled.graph.nodes):
            assert after.dimensions == before.dimensions
            assert after.transform == before.transform

    def test_two_regions_never_share_a_token(self, scanned):
        scrambled = held_out.scramble(scanned[0], seed=3)
        labels = [node.label for node in scrambled.graph.nodes]
        assert len(labels) == len(set(labels))

    def test_the_same_seed_scrambles_the_same_way(self, scanned):
        first = held_out.scramble(scanned[0], seed=11)
        second = held_out.scramble(scanned[0], seed=11)
        assert [node.label for node in first.graph.nodes] == [
            node.label for node in second.graph.nodes
        ]


class TestNumbersMustComeFromTheRoom:
    def test_a_figure_from_nowhere_is_caught(self, scanned):
        answer = Spoken(text="The aisle is 1.42 metres across.", data={})
        assert not held_out.numbers_traced(answer, scanned[0])

    def test_a_figure_the_answer_measured_is_allowed(self, scanned):
        answer = Spoken(text="The aisle is 1.42 metres across.", data={"clearance_m": 1.42})
        assert held_out.numbers_traced(answer, scanned[0])

    def test_rounding_for_a_reader_is_allowed(self, scanned):
        answer = Spoken(text="About 1.4 metres.", data={"clearance_m": 1.4149})
        assert held_out.numbers_traced(answer, scanned[0])

    def test_a_room_measurement_the_answer_did_not_work_out_is_not_allowed(self, scanned):
        """Tightened on purpose, and it used to assert the opposite.

        A figure that matches some measurement in the room but appears nowhere in
        the answer's own data cannot be told apart from a model guessing a
        plausible room-sized number: two dozen regions, six figures each, offered
        in five units with a tolerance on every one, cover the number line
        densely. What the engine worked out is in the data, so that is what a
        figure has to trace to.
        """
        node = scanned[0].graph.nodes[0]
        answer = Spoken(text=f"It is {node.dimensions.x:.2f} metres wide.", data={})
        assert not held_out.numbers_traced(answer, scanned[0])

    def test_the_same_measurement_is_allowed_once_the_answer_carries_it(self, scanned):
        node = scanned[0].graph.nodes[0]
        answer = Spoken(
            text=f"It is {node.dimensions.x:.2f} metres wide.",
            data={"width_m": node.dimensions.x},
        )
        assert held_out.numbers_traced(answer, scanned[0])

    def test_a_span_read_out_in_feet_and_inches_is_one_figure(self, scanned):
        """Counting the feet and the inches separately called a correct answer
        two inventions, which was the reader misreading the sentence."""
        answer = Spoken(
            text="The table and the storage are 3 feet 6 inches apart.",
            data={"clearance_inches": 42.0},
        )
        assert held_out.numbers_traced(answer, scanned[0])

    def test_a_span_whose_total_is_not_in_the_data_still_fails(self, scanned):
        answer = Spoken(
            text="The table and the storage are 3 feet 6 inches apart.",
            data={"clearance_inches": 90.0},
        )
        assert not held_out.numbers_traced(answer, scanned[0])

    def test_a_sentence_with_no_figures_passes(self, scanned):
        assert held_out.numbers_traced(Spoken(text="I could not tell."), scanned[0])


class TestWhatCountsAsRight:
    def _verdict(self, *, answerable, refused, supported, traced=True, confident=True):
        return held_out.Verdict(
            question=held_out.Question(
                text="?", approach="a", answerable=answerable, scene_id="s"
            ),
            refused=refused,
            supported=supported,
            confident=confident,
            numbers_traced=traced,
            reason="",
        )

    def test_saying_so_passes_an_unanswerable_question(self):
        assert self._verdict(answerable=False, refused=True, supported=False).passed

    def test_answering_an_unanswerable_question_fails(self):
        verdict = self._verdict(answerable=False, refused=False, supported=True)
        assert not verdict.passed
        assert verdict.hallucinated

    def test_refusing_an_answerable_question_fails(self):
        assert not self._verdict(answerable=True, refused=True, supported=False).passed

    def test_an_unsupported_answer_fails(self):
        assert not self._verdict(answerable=True, refused=False, supported=False).passed

    def test_a_supported_answer_with_an_invented_figure_fails(self):
        assert not self._verdict(
            answerable=True, refused=False, supported=True, traced=False
        ).passed

    def test_a_supported_and_traced_answer_passes(self):
        assert self._verdict(answerable=True, refused=False, supported=True).passed


class TestQuestionsAreDiverse:
    def test_a_repeated_approach_is_dropped(self, scanned):
        written = [
            {"text": "How many chairs?", "approach": "count them", "answerable": True},
            {"text": "How many tables?", "approach": "Count  them", "answerable": True},
            {"text": "How wide is the aisle?", "approach": "measure a span", "answerable": True},
        ]
        kept = writing._distinct(written, scanned[0])
        assert [question.approach for question in kept] == ["count them", "measure a span"]

    def test_the_same_question_asked_twice_takes_one_slot(self, scanned):
        written = [
            {"text": "How many chairs are here?", "approach": "count", "answerable": True},
            {"text": "How many chairs are in here?", "approach": "tally them", "answerable": True},
        ]
        assert len(writing._distinct(written, scanned[0])) == 1

    def test_two_questions_about_different_things_both_stand(self, scanned):
        written = [
            {"text": "Which table is widest?", "approach": "compare widths", "answerable": True},
            {"text": "How tall are the walls?", "approach": "read a height", "answerable": True},
        ]
        assert len(writing._distinct(written, scanned[0])) == 2

    def test_nothing_written_is_refused_rather_than_scored(self, scanned):
        with pytest.raises(held_out.CouldNotWriteQuestions):
            writing._distinct([], scanned[0])


class TestTheSuiteCannotBeAnswerKeyed:
    """A string to match is a string to special-case, so there are none."""

    def test_no_module_carries_an_expected_answer(self):
        directory = pathlib.Path(loading.__file__).parent
        for path in directory.glob("*.py"):
            source = path.read_text()
            assert "expected_answer" not in source
            assert "EXPECTED" not in source

    def test_a_model_is_required_rather_than_assumed(self, monkeypatch, scanned):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(held_out.CouldNotWriteQuestions):
            held_out.write(scanned[0], count=4, seed=1)
