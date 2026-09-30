"""A customer restroom's fixtures and its door sign: 603.3, 604.2, 604.4, 604.5, 606.2, 606.3, 609.4 and 703.4.

Each rule is off until a person verifies it, so every test here builds its own
ledger in a temporary file and switches on only the rule it is about.
"""

from __future__ import annotations

import uuid

import pytest
from standardphysics_agents import evaluate_candidate_room
from standardphysics_agents.checks import REGISTRY, run_checks
from standardphysics_agents.checks.context import CheckContext
from standardphysics_agents.findings import to_findings
from standardphysics_agents.rules import VerificationLedger, load_ledger, load_pack, save_ledger
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, SurfaceHeight, Vec3, to_meters
from standardphysics_contracts.precedents import SpaceTypology
from standardphysics_pipeline.measure import PipelineMeasurements

NEW_RULES = (
    "water_closet_location", "water_closet_seat_height", "water_closet_grab_bars", "grab_bar_height",
    "lavatory_height", "lavatory_knee_clearance", "mirror_height", "sign_tactile_height", "sign_location",
)

TOILET_DEPTH = 0.70
TOILET_WIDTH = 0.45


def _node(label, centre, size, kind="object", **extra):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"{label}{centre}{size}"), kind=kind, label=label,
        raw_category=label.casefold(), dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*centre), **extra,
    )


def _toilet():
    return _node("Toilet", (0.0, 0.0, 0.38), (TOILET_WIDTH, TOILET_DEPTH, 0.76))


def _side_wall(inches_from_centre: float):
    """A wall running alongside the toilet, its face this far from the toilet's centreline."""
    face = to_meters(inches_from_centre)
    return _node("Wall", (-face - 0.05, 0.0, 1.5), (0.1, 4.0, 3.0), kind="wall")


def _rear_wall():
    return _node("Wall", (0.0, TOILET_DEPTH / 2 + 0.05, 1.5), (4.0, 0.1, 3.0), kind="wall")


def _grab_bar(centre_xy, length_inches, top_inches, along_y):
    length = to_meters(length_inches)
    size = (0.04, length, 0.04) if along_y else (length, 0.04, 0.04)
    return _node("Grab bar", (*centre_xy, to_meters(top_inches) - 0.02), size)


def _side_bar(length_inches=42.0, top_inches=34.0):
    return _grab_bar((-0.40, -0.1), length_inches, top_inches, along_y=True)


def _rear_bar(length_inches=36.0, top_inches=34.0):
    return _grab_bar((0.0, TOILET_DEPTH / 2 - 0.02), length_inches, top_inches, along_y=False)


def _sink(top_inches=33.0, **extra):
    height = to_meters(top_inches)
    return _node("Sink", (2.0, 0.0, height / 2), (0.5, 0.45, height), **extra)


def _mirror(bottom_inches):
    bottom = to_meters(bottom_inches)
    return _node("Mirror", (2.0, 0.24, bottom + 0.4), (0.6, 0.02, 0.8))


def _graph(*nodes):
    floor = _node("Floor", (0, 0, 0), (10, 10, 0.02), kind="floor")
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, *nodes])


def _ledger_with(tmp_path, *rule_ids):
    """A person's ledger for these rules, written to disk and read back the way the server reads it."""
    pack = load_pack()
    book = VerificationLedger()
    for rule_id in rule_ids:
        book = book.record(pack.by_id(rule_id), verified_by="test reader, not a person")
    path = tmp_path / "verification.json"
    save_ledger(book, path)
    return load_ledger(path)


def _findings(graph, ledger):
    stop = Stop(name="Here", position=Vec3(x=0, y=-2, z=0))
    ctx = CheckContext(graph, Scenario(name="Test", stops=[stop, stop]), PipelineMeasurements(), load_pack(), ledger)
    return to_findings(run_checks(ctx).observations, ctx.rules, graph, graph.scan_id)


def _one(findings, rule_id):
    found = [finding for finding in findings if finding.check_id == rule_id]
    assert len(found) == 1, found
    return found[0]


def test_every_new_rule_quotes_its_section_and_is_answered_by_a_check():
    pack = load_pack()
    covered = frozenset().union(*[rule_ids for rule_ids, _ in REGISTRY])
    for rule_id in NEW_RULES:
        rule = pack.by_id(rule_id)
        assert rule.citation.authority == "ADA_2010", rule_id
        assert rule.source_text.startswith(rule.citation.section.split(" ")[0]), rule_id
        assert rule_id in covered, rule_id


@pytest.mark.parametrize("rule_id", NEW_RULES)
def test_a_new_rule_stays_off_without_a_ledger_entry(rule_id):
    graph = _graph(_toilet(), _side_wall(24.0), _rear_wall(), _side_bar(30.0, 38.0), _rear_bar(),
                   _sink(38.0), _mirror(44.0))
    assert load_pack().by_id(rule_id) not in load_pack().enabled(VerificationLedger(), max_tier=3)
    assert not [finding for finding in _findings(graph, VerificationLedger()) if finding.check_id == rule_id]


def test_a_toilet_16_to_18_inches_from_the_side_wall_passes(tmp_path):
    graph = _graph(_toilet(), _side_wall(17.0), _rear_wall())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_location")), "water_closet_location")
    assert finding.outcome == "passes"
    assert finding.measured_inches == pytest.approx(17.0, abs=0.05)


def test_a_toilet_too_far_from_the_side_wall_is_a_problem_with_the_distance(tmp_path):
    graph = _graph(_toilet(), _side_wall(24.0), _rear_wall())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_location")), "water_closet_location")
    assert finding.outcome == "problem"
    assert finding.measured_inches == pytest.approx(24.0, abs=0.05)
    assert finding.required_inches == 18.0
    assert "24 inches" in finding.detail
    assert finding.title == "The toilet sits too far from the side wall"


def test_a_toilet_too_close_to_the_side_wall_is_held_to_the_minimum(tmp_path):
    graph = _graph(_toilet(), _side_wall(14.0), _rear_wall())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_location")), "water_closet_location")
    assert finding.outcome == "problem"
    assert finding.required_inches == 16.0
    assert finding.title == "The toilet sits too close to the side wall"


def test_a_toilet_with_no_side_wall_in_the_scan_asks_for_the_tape_measure(tmp_path):
    graph = _graph(_toilet(), _rear_wall())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_location")), "water_closet_location")
    assert finding.outcome == "question"
    assert finding.asks == "measurement"
    assert finding.title == "Measure how far the middle of the toilet is from the side wall"


def test_a_restroom_the_scan_did_not_reach_asks_for_a_photo_of_the_toilet(tmp_path):
    finding = _one(_findings(_graph(), _ledger_with(tmp_path, "water_closet_location")), "water_closet_location")
    assert finding.outcome == "question"
    assert finding.asks == "photo"
    assert "16 to 18 inches" in finding.detail


def test_the_seat_height_is_always_a_photo_because_a_box_hides_the_seat(tmp_path):
    graph = _graph(_toilet(), _side_wall(17.0), _rear_wall())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_seat_height")), "water_closet_seat_height")
    assert finding.outcome == "question"
    assert finding.asks == "photo"
    assert "17 to 19 inches" in finding.detail


def test_grab_bars_long_enough_on_both_walls_pass(tmp_path):
    graph = _graph(_toilet(), _side_wall(17.0), _rear_wall(), _side_bar(), _rear_bar())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_grab_bars")), "water_closet_grab_bars")
    assert finding.outcome == "passes"
    assert finding.measured_inches == pytest.approx(42.0, abs=0.1)


def test_a_short_side_grab_bar_is_a_problem_with_its_length(tmp_path):
    graph = _graph(_toilet(), _side_wall(17.0), _rear_wall(), _side_bar(30.0), _rear_bar())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_grab_bars")), "water_closet_grab_bars")
    assert finding.outcome == "problem"
    assert finding.measured_inches == pytest.approx(30.0, abs=0.1)
    assert finding.required_inches == 42.0
    assert "30 inches" in finding.detail


def test_a_short_rear_grab_bar_is_held_to_36_inches(tmp_path):
    graph = _graph(_toilet(), _side_wall(17.0), _rear_wall(), _side_bar(), _rear_bar(24.0))
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_grab_bars")), "water_closet_grab_bars")
    assert finding.outcome == "problem"
    assert finding.measured_inches == pytest.approx(24.0, abs=0.1)
    assert finding.required_inches == 36.0


def test_a_toilet_with_no_grab_bars_found_asks_for_a_photo_rather_than_failing(tmp_path):
    graph = _graph(_toilet(), _side_wall(17.0), _rear_wall())
    finding = _one(_findings(graph, _ledger_with(tmp_path, "water_closet_grab_bars")), "water_closet_grab_bars")
    assert finding.outcome == "question"
    assert finding.asks == "photo"
    assert finding.title == "Send a photo of the grab bars around the toilet"


def test_a_grab_bar_33_to_36_inches_up_passes(tmp_path):
    graph = _graph(_toilet(), _side_bar(top_inches=34.0))
    finding = _one(_findings(graph, _ledger_with(tmp_path, "grab_bar_height")), "grab_bar_height")
    assert finding.outcome == "passes"
    assert finding.measured_inches == pytest.approx(34.0, abs=0.05)


def test_a_grab_bar_mounted_too_high_is_a_problem_with_its_height(tmp_path):
    graph = _graph(_toilet(), _side_bar(top_inches=38.0))
    finding = _one(_findings(graph, _ledger_with(tmp_path, "grab_bar_height")), "grab_bar_height")
    assert finding.outcome == "problem"
    assert finding.measured_inches == pytest.approx(38.0, abs=0.05)
    assert finding.title == "The grab bar is too high"


def test_no_grab_bar_in_the_scan_asks_for_a_photo_of_one(tmp_path):
    finding = _one(_findings(_graph(_toilet()), _ledger_with(tmp_path, "grab_bar_height")), "grab_bar_height")
    assert finding.outcome == "question"
    assert finding.asks == "photo"


def test_a_sink_34_inches_or_lower_passes(tmp_path):
    finding = _one(_findings(_graph(_sink(33.0)), _ledger_with(tmp_path, "lavatory_height")), "lavatory_height")
    assert finding.outcome == "passes"
    assert finding.measured_inches == pytest.approx(33.0, abs=0.05)


def test_a_sink_too_high_is_a_problem_with_its_height(tmp_path):
    finding = _one(_findings(_graph(_sink(38.0)), _ledger_with(tmp_path, "lavatory_height")), "lavatory_height")
    assert finding.outcome == "problem"
    assert finding.measured_inches == pytest.approx(38.0, abs=0.05)
    assert "38 inches" in finding.detail


def test_a_measured_rim_height_wins_over_the_box_top(tmp_path):
    rim = SurfaceHeight(height_m=to_meters(33.5), uncertainty_m=0.005)
    graph = _graph(_sink(40.0, top_surface=rim))
    finding = _one(_findings(graph, _ledger_with(tmp_path, "lavatory_height")), "lavatory_height")
    assert finding.outcome == "passes"
    assert finding.measured_inches == pytest.approx(33.5, abs=0.05)


def test_no_sink_in_the_scan_asks_for_a_photo_of_it(tmp_path):
    finding = _one(_findings(_graph(), _ledger_with(tmp_path, "lavatory_height")), "lavatory_height")
    assert finding.outcome == "question"
    assert finding.asks == "photo"
    assert "34 inches" in finding.detail


def test_knee_clearance_under_the_sink_is_a_photo(tmp_path):
    finding = _one(_findings(_graph(_sink()), _ledger_with(tmp_path, "lavatory_knee_clearance")),
                   "lavatory_knee_clearance")
    assert finding.outcome == "question"
    assert finding.asks == "photo"


def test_a_mirror_over_the_sink_with_a_low_enough_edge_passes(tmp_path):
    graph = _graph(_sink(), _mirror(39.0))
    finding = _one(_findings(graph, _ledger_with(tmp_path, "mirror_height")), "mirror_height")
    assert finding.outcome == "passes"
    assert finding.measured_inches == pytest.approx(39.0, abs=0.05)


def test_a_mirror_over_the_sink_hung_too_high_is_a_problem_with_its_edge_height(tmp_path):
    graph = _graph(_sink(), _mirror(44.0))
    finding = _one(_findings(graph, _ledger_with(tmp_path, "mirror_height")), "mirror_height")
    assert finding.outcome == "problem"
    assert finding.measured_inches == pytest.approx(44.0, abs=0.05)
    assert "44 inches" in finding.detail


def test_a_mirror_nowhere_near_a_sink_is_not_measured_and_the_restroom_mirror_is_asked_about(tmp_path):
    far_mirror = _node("Mirror", (-3.0, 0.0, 1.5), (0.6, 0.02, 0.8))
    finding = _one(_findings(_graph(_sink(), far_mirror), _ledger_with(tmp_path, "mirror_height")), "mirror_height")
    assert finding.outcome == "question"
    assert finding.asks == "photo"


@pytest.mark.parametrize("rule_id", ["sign_tactile_height", "sign_location"])
def test_the_restroom_sign_is_a_photo_because_a_scan_cannot_read_braille(tmp_path, rule_id):
    finding = _one(_findings(_graph(), _ledger_with(tmp_path, rule_id)), rule_id)
    assert finding.outcome == "question"
    assert finding.asks == "photo"
    assert finding.fix is None


def test_the_room_record_applies_a_fixture_rule_only_where_the_scan_shows_the_fixture(tmp_path):
    def applicable(graph):
        stop = Stop(name="Here", position=Vec3(x=0, y=-2, z=0))
        result = evaluate_candidate_room(graph, Scenario(name="Test", stops=[stop, stop]), PipelineMeasurements(),
                                         SpaceTypology.RESTROOM_FACILITY, rule_ledger=_ledger_with(tmp_path),
                                         directives=[])
        return {entry.id for entry in result.entries if entry.applicable}

    empty = applicable(_graph())
    fitted = applicable(_graph(_toilet(), _side_wall(17.0), _rear_wall(), _sink(), _mirror(39.0)))
    for rule_id in ("water_closet_location", "water_closet_grab_bars", "lavatory_height", "mirror_height"):
        assert f"rule:{rule_id}" not in empty
        assert f"rule:{rule_id}" in fitted
