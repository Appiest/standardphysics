"""The photo detector is asked for everything the ADA checks look for, and the names it returns land in the right class."""

from __future__ import annotations

import pytest
from standardphysics_pipeline.discovery import taxonomy
from standardphysics_pipeline.discovery.detect import FIXED_NAMES, INSTRUCTION


@pytest.mark.parametrize("phrase", [
    "self-order kiosks", "ordering machines", "touchscreens", "menu boards", "condiment stations",
    "self-serve stations", "straw dispensers", "pickup counters", "handoff shelves", "cashier drawers",
    "tip screens", "handrails", "railings", "ramps", "ramp landings", "steps", "thresholds",
])
def test_the_detector_is_asked_for_it(phrase):
    assert phrase in INSTRUCTION


@pytest.mark.parametrize("name", ["pickup counter", "handoff counter", "transaction counter", "cashier counter"])
def test_a_pickup_counter_is_a_service_counter_candidate(name):
    assert taxonomy.classify(name) == taxonomy.SERVICE_COUNTER
    assert taxonomy.needs_owner_confirmation(name)


@pytest.mark.parametrize("name", ["ramp", "wheelchair ramp", "handrail", "railing", "pickup counter"])
def test_a_ramp_rail_or_pickup_counter_is_built_in(name):
    assert taxonomy.is_fixture_name(name)


def test_the_detector_never_marks_a_ramp_or_handrail_movable():
    assert {"ramp", "ramp landing", "handrail", "railing"} <= FIXED_NAMES
