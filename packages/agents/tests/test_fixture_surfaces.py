"""Surface uncertainty and fixture zones are hard constraints for every move path."""

import uuid

from standardphysics_agents.checks.context import CheckContext
from standardphysics_agents.checks.dining import dining_surface_height
from standardphysics_agents.checks.service_counter import point_of_sale_height, service_counter_height
from standardphysics_agents.findings import resolve, to_finding
from standardphysics_agents.fix.constraints import violations
from standardphysics_agents.rules import VerificationLedger, load_pack
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, SurfaceHeight, Vec3
from standardphysics_pipeline.measure import PipelineMeasurements


def _node(label, centre, size, movable=False):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, label), kind="object", label=label,
        raw_category=label, dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*centre), movable=movable,
    )


def _graph(*nodes):
    floor = _node("Floor", (0, 0, 0), (10, 10, 0.02)).model_copy(update={"kind": "floor"})
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, *nodes])


def _context(graph):
    stop = Stop(name="Here", position=Vec3(x=0, y=0, z=0))
    return CheckContext(graph, Scenario(name="Test", stops=[stop, stop]), PipelineMeasurements(),
                        load_pack(), VerificationLedger())


def _move(graph, label, position):
    return graph.model_copy(update={"nodes": [
        node.model_copy(update={"transform": Mat4.translation(*position)}) if node.label == label else node
        for node in graph.nodes
    ]})


def test_four_inches_over_is_a_problem_with_mesh_measurement():
    counter = _node("Ordering counter", (0, 0, 0.46), (0.8, 0.8, 0.92))
    counter = counter.model_copy(update={"top_surface": SurfaceHeight(height_m=1.016, uncertainty_m=0.02, support_area_m2=0.3)})
    graph = _graph(counter)
    observed = service_counter_height(_context(graph))[0]
    assert observed.measured_inches == 40
    assert observed.facts["uncertainty_inches"] > 0
    assert resolve(observed, load_pack().by_id(observed.rule_id), graph)[0] == "problem"
    finding = to_finding(observed, load_pack().by_id(observed.rule_id), graph, graph.scan_id)
    assert finding.uncertainty_inches == observed.facts["uncertainty_inches"]
    assert "uncertainty ±" in finding.detail


def test_missing_or_borderline_mesh_height_never_passes():
    counter = _node("Ordering counter", (0, 0, 0.46), (0.8, 0.8, 0.92))
    for surface in (SurfaceHeight(), SurfaceHeight(height_m=0.9144, uncertainty_m=0.02, support_area_m2=0.3)):
        graph = _graph(counter.model_copy(update={"top_surface": surface}))
        observed = service_counter_height(_context(graph))[0]
        assert resolve(observed, load_pack().by_id(observed.rule_id), graph)[0] == "question"


def test_payment_on_the_high_section_is_reported_when_accessible_section_exists():
    counter = _node("Ordering counter", (0, 0, 0.46), (0.8, 0.8, 0.92))
    counter = counter.model_copy(update={"top_surface": SurfaceHeight(height_m=1.016, uncertainty_m=0.02, support_area_m2=0.3)})
    lower = _node("Lowered counter section", (0.9, 0, 0.4), (1, 0.8, 0.8))
    lower = lower.model_copy(update={"top_surface": SurfaceHeight(height_m=0.8, uncertainty_m=0.02, support_area_m2=0.3)})
    register = _node("Cash register", (0, 0, 1.1), (0.2, 0.2, 0.2))
    graph = _graph(counter, lower, register)
    observed = point_of_sale_height(_context(graph))[0]
    assert observed.measured_inches == 40
    assert resolve(observed, load_pack().by_id(observed.rule_id), graph)[0] == "problem"


def test_cash_drawer_on_a_high_pos_counter_uses_the_surface_role():
    counter = _node("POS counter", (0, 0, 0.46), (0.8, 0.8, 0.92))
    counter = counter.model_copy(update={"top_surface": SurfaceHeight(
        height_m=1.016, uncertainty_m=0.02, support_area_m2=0.3,
    )})
    graph = _graph(counter, _node("Cash drawer", (0, 0, 1.1), (0.2, 0.2, 0.2)))
    observed = service_counter_height(_context(graph))[0]
    assert observed.measured_inches == 40
    assert resolve(observed, load_pack().by_id(observed.rule_id), graph)[0] == "problem"


def test_table_height_with_unknown_scan_stays_a_question():
    table = _node("Table", (0, 0, 0.38), (0.8, 0.8, 0.76))
    graph = _graph(table.model_copy(update={"top_surface": SurfaceHeight()}))
    observed = dining_surface_height(_context(graph))[0]
    assert resolve(observed, load_pack().by_id(observed.rule_id), graph)[0] == "question"


def test_table_height_overlapping_valid_range_stays_a_question():
    table = _node("Table", (0, 0, 0.38), (0.8, 0.8, 0.76))
    table = table.model_copy(update={"top_surface": SurfaceHeight(
        height_m=0.87, uncertainty_m=0.02, support_area_m2=0.3,
    )})
    graph = _graph(table)
    observed = dining_surface_height(_context(graph))[0]
    assert resolve(observed, load_pack().by_id(observed.rule_id), graph)[0] == "question"


def test_a_borderline_height_becomes_a_finding_that_asks_for_a_measurement():
    counter = _node("Ordering counter", (0, 0, 0.46), (0.8, 0.8, 0.92))
    counter = counter.model_copy(update={"top_surface": SurfaceHeight(
        height_m=0.9144, uncertainty_m=0.02, support_area_m2=0.3,
    )})
    table = _node("Table", (3, 0, 0.38), (0.8, 0.8, 0.76))
    table = table.model_copy(update={"top_surface": SurfaceHeight(
        height_m=0.87, uncertainty_m=0.02, support_area_m2=0.3,
    )})
    graph = _graph(counter, table)
    context = _context(graph)
    for observed in (service_counter_height(context)[0], dining_surface_height(context)[0]):
        finding = to_finding(observed, load_pack().by_id(observed.rule_id), graph, graph.scan_id)
        assert finding.asks == "measurement"


def test_a_fixture_cannot_move_even_if_its_movable_flag_is_wrong():
    for label in ("Cash drawer", "POS counter", "Ramp", "Ramp landing"):
        graph = _graph(_node(label, (0, 0, 0.1), (1, 1, 0.2), movable=True))
        assert "moved_something_fixed" in {v.kind for v in violations(graph, _move(graph, label, (1, 0, 0.1)))}


def test_thin_ramp_and_landing_cannot_be_blocked():
    for label in ("Ramp", "Ramp landing"):
        ramp = _node(label, (0, 0, 0), (1, 1, 0.001))
        chair = _node("Chair", (2, 0, 0.45), (0.4, 0.4, 0.9), movable=True)
        graph = _graph(ramp, chair)
        assert "blocked_keep_clear" in {v.kind for v in violations(graph, _move(graph, "Chair", (0, 0, 0.45)))}


def test_payment_approach_cannot_be_blocked_without_touching_counter():
    counter = _node("Ordering counter", (0, 0, 0.5), (1, 0.8, 1))
    reader = _node("Cash register", (0, 0, 1.1), (0.2, 0.2, 0.2))
    chair = _node("Chair", (2, -0.9, 0.45), (0.4, 0.4, 0.9), movable=True)
    graph = _graph(counter, reader, chair)
    assert "blocked_keep_clear" in {v.kind for v in violations(graph, _move(graph, "Chair", (0, -0.9, 0.45)))}
