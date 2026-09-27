"""A detector's free-text name for something a customer operates still reaches the reach-range check."""

import uuid

import pytest
from standardphysics_agents.checks.roles import is_operable_part, operable_parts
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3


@pytest.mark.parametrize("label", [
    "Sanitizer", "Hand sanitizer", "Sanitizer dispenser", "Hand sanitizer dispenser",
    "Wall mounted hand sanitizer", "Sanitizer station", "Hand sanitizer stand",
    "Fire extinguisher", "Fire extinguisher cabinet", "Hand dryer", "Light switch",
    "Soap dispenser", "Paper towel dispenser",
])
def test_every_wording_of_an_operable_thing_counts(label):
    assert is_operable_part(label)


@pytest.mark.parametrize("label", [
    "Hand sanitizer sign", "Fire extinguisher sign", "Exit sign", "Payment terminal", "Dispenser",
    "Bell pepper", "Switchboard", "",
])
def test_a_sign_or_an_unrelated_name_does_not(label):
    assert not is_operable_part(label)


def test_a_discovered_sanitizer_dispenser_is_an_operable_part():
    dispenser = SceneNode(id=uuid.uuid4(), kind="object", label="Hand sanitizer dispenser",
                          raw_category="hand_sanitizer_dispenser", dimensions=Vec3(x=0.15, y=0.1, z=0.3),
                          transform=Mat4.translation(0.0, 0.0, 1.35))
    assert operable_parts(SceneGraph(scan_id=uuid.uuid4(), nodes=[dispenser])) == [dispenser]
