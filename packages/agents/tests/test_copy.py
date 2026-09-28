"""Every sentence the owner reads, checked against section 2 of the plan."""

from __future__ import annotations

import re

import pytest
from standardphysics_agents import assess
from standardphysics_contracts import Mat4, SceneNode, Vec3
from standardphysics_fixtures.shop import node_id

JARGON = (
    "node", "scene graph", "confidence", "evidence", "assessment", "revision",
    "locus", "meter", "metre", "mm", "occupancy", "threshold inches", "provider",
    "clearance radius", "pinch point", "uuid",
)

DENIALS = (
    "couldn't", "could not", "unable", "we did not", "we didn't", "not checked",
    "no data", "estimate only", "not a legal", "cannot be", "failed to",
)

METRIC = re.compile(r"\d+(\.\d+)?\s?(m|cm|mm|metres|meters)\b")


def _sentences(result):
    for finding in result.findings:
        for text in (finding.title, finding.detail, finding.fix):
            if text:
                yield finding.check_id, text


@pytest.fixture
def sentences(graph, scenario, measure, ledger):
    return list(_sentences(assess(graph, scenario, measure, ledger=ledger)))


def test_no_jargon_reaches_a_screen(sentences):
    for check_id, text in sentences:
        lowered = text.casefold()
        for word in JARGON:
            assert word not in lowered, f"{check_id}: {text!r} contains {word!r}"


def test_no_sentence_points_at_an_absence(sentences):
    for check_id, text in sentences:
        lowered = text.casefold()
        for phrase in DENIALS:
            assert phrase not in lowered, f"{check_id}: {text!r} contains {phrase!r}"


def test_every_number_is_in_inches(sentences):
    for check_id, text in sentences:
        assert not METRIC.search(text), f"{check_id}: {text!r}"


def test_nothing_is_shouted(sentences):
    for check_id, text in sentences:
        shouted = [w for w in re.findall(r"[A-Za-z]{3,}", text) if w.isupper()]
        assert not shouted, f"{check_id}: {text!r} shouts {shouted}"


def test_a_title_names_the_thing_without_a_full_stop(graph, scenario, measure, ledger):
    result = assess(graph, scenario, measure, ledger=ledger)
    for finding in result.findings:
        assert not finding.title.endswith("."), finding.title


def test_every_problem_says_what_to_do(graph, scenario, measure, ledger):
    result = assess(graph, scenario, measure, ledger=ledger)
    for finding in result.problems:
        assert finding.fix, finding.title


def test_nothing_that_passed_carries_a_fix(graph, scenario, measure, ledger):
    result = assess(graph, scenario, measure, ledger=ledger)
    passes = [f for f in result.findings if f.outcome == "passes"]
    assert passes
    assert all(f.fix is None for f in passes)


def test_a_fix_never_asks_the_owner_to_move_a_built_in(
    graph, scenario, pipeline, ledger
):
    """The ordering counter is fixed. A fix that named it would send someone
    to shove a wall. The pinch is the two display cases, which do move."""
    result = assess(graph, scenario, pipeline, ledger=ledger)
    pinches = [f for f in result.problems if f.check_id == "route_clear_width"]
    assert pinches
    for finding in pinches:
        assert finding.fix
        assert "move the ordering counter" not in finding.fix.casefold()
        fixed = [
            graph.by_id(nid).label.casefold()
            for nid in (finding.locus.node_ids if finding.locus else [])
            if not graph.by_id(nid).movable
        ]
        for label in fixed:
            assert f"move the {label}" not in finding.fix.casefold()


def test_a_fix_that_needs_a_builder_says_so_as_an_action(graph, scenario, stub, ledger):
    """Two fixed blockers cannot be rearranged, so the action is to ask."""
    pinned = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"movable": False})
                if node.label == "Display case"
                else node
                for node in graph.nodes
            ]
        }
    )
    result = assess(pinned, scenario, stub, ledger=ledger)
    pinch = next(
        f for f in result.problems if f.check_id == "route_clear_width"
    )
    assert pinch.fix == "Ask a contractor about opening this gap to 36 inches."


def _with_ramp_kiosk_and_straws(graph):
    def piece(name, label, centre, dims, movable=False):
        return SceneNode(id=node_id(name), kind="object", label=label, raw_category=label, movable=movable,
                         dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]), transform=Mat4.translation(*centre))

    return graph.model_copy(update={"nodes": [
        *graph.nodes,
        piece("copy_ramp", "Ramp", (2.2, -1.0, 0.15), (1.6, 0.8, 0.3)),
        piece("copy_chair", "Chair", (2.2, 0.2, 0.45), (0.45, 0.45, 0.9), movable=True),
        piece("copy_kiosk", "Self-order kiosk", (-2.6, -1.0, 0.8), (0.5, 0.5, 1.6)),
        piece("copy_screen", "Touchscreen", (-2.8, 1.0, 1.5), (0.05, 0.5, 0.4)),
        piece("copy_straws", "Straw dispenser", (-0.5, 3.6, 1.4), (0.15, 0.15, 0.25)),
    ]})


def test_ramp_kiosk_and_self_serve_copy_reads_the_same_way(graph, scenario, pipeline, ledger):
    result = assess(_with_ramp_kiosk_and_straws(graph), scenario, pipeline, ledger=ledger)
    new_checks = {"ramp_running_slope", "ramp_rise", "ramp_clear_width", "ramp_landing_length", "ramp_handrails",
                  "kiosk_reach", "kiosk_clear_floor", "self_service_reach"}
    findings = [f for f in result.findings if f.check_id in new_checks]
    assert {f.check_id for f in findings} == new_checks
    assert {f.outcome for f in findings} == {"problem", "question", "passes"}
    for finding in findings:
        assert not finding.title.endswith("."), finding.title
        assert (finding.fix is not None) == (finding.outcome == "problem"), finding.title
        for text in filter(None, (finding.title, finding.detail, finding.fix)):
            lowered = text.casefold()
            assert not any(word in lowered for word in JARGON), text
            assert not any(phrase in lowered for phrase in DENIALS), text
            assert not METRIC.search(text), text
            assert not [w for w in re.findall(r"[A-Za-z]{3,}", text) if w.isupper()], text
