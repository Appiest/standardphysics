"""Before the owner confirms a route, every rule that needs none still runs."""

from standardphysics_agents import load_pack
from standardphysics_fixtures import build_lawsuit_graph

from conftest import REPO, create_scan, drain, no_blender_stages, put_artifact
from standardphysics_api.stages import ROUTE_SUBJECTS, preview_ledger, without_route_rules

ROUTE_RULES = {rule.id for rule in load_pack().rules if ROUTE_SUBJECTS.intersection(rule.applies_to)}


def test_the_route_rules_are_the_ones_about_paths():
    assert {"route_clear_width", "turn_clear_width", "passing_space", "turning_space", "exit_path"} == ROUTE_RULES


def test_without_route_rules_keeps_every_other_verification():
    kept = {entry.rule_id for entry in without_route_rules(preview_ledger()).entries}
    assert kept == {rule.id for rule in load_pack().rules} - ROUTE_RULES


def test_the_high_counter_is_found_before_any_route_exists():
    assessment = no_blender_stages().assess(build_lawsuit_graph(), None, pass_number=1)
    problems = {finding.check_id for finding in assessment.findings if finding.outcome == "problem"}
    assert "point_of_sale_height" in problems
    assert not {finding.check_id for finding in assessment.findings} & ROUTE_RULES
    assert assessment.rules_checked == len([rule for rule in load_pack().within_tier(1) if rule.id not in ROUTE_RULES])


def test_a_drag_before_a_route_is_checked_without_the_route_rules(client):
    scan_id = _phone_scan(client, "ravida")
    response = client.post(f"/api/scans/{scan_id}/layout-checks", json={"sequence": 1, "base_revision": 0, "moves": []})
    assert response.status_code == 200
    assert not {finding["check_id"] for finding in response.json()["findings"]} & ROUTE_RULES


def _phone_scan(client, name: str) -> str:
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", (REPO / "datasets/phone" / name / "room.json").read_bytes(), "room_json")
    put_artifact(client, scan_id, "room-usdz", (REPO / "datasets/phone" / name / "room.usdz").read_bytes(), "room_usdz")
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)
    return scan_id
