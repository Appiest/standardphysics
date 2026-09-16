"""The scene is a tree: the blanket rests on the bed, the bed rests on the floor.

Everything that answers where something is walks these edges, so the shape is
tested directly rather than through whatever happens to read it.
"""

import uuid

import pytest
from pydantic import ValidationError
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3


def node(name: str, kind: str = "object", parent=None, relation=None, at=(0.0, 0.0, 0.0), size=(1.0, 1.0, 1.0)):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"tree-{name}"),
        kind=kind,
        label=name,
        raw_category=name,
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*at),
        parent_id=parent,
        relation=relation,
    )


@pytest.fixture
def dorm():
    """A floor, a bed on it, a blanket on the bed, and a pen inside a cup on a desk."""
    floor = node("floor", kind="floor")
    bed = node("bed", parent=floor.id, relation="rests_on")
    blanket = node("blanket", parent=bed.id, relation="rests_on")
    desk = node("desk", parent=floor.id, relation="rests_on")
    cup = node("cup", parent=desk.id, relation="rests_on")
    pen = node("pen", parent=cup.id, relation="inside")
    poster = node("poster", parent=node("wall", kind="wall").id, relation="mounted_on")
    wall = node("wall", kind="wall")
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, bed, blanket, desk, cup, pen, wall, poster])


def labels(nodes):
    return sorted(item.label for item in nodes)


class TestWalkingTheTree:
    def test_a_blanket_knows_it_is_on_a_bed_on_the_floor(self, dorm):
        blanket = next(n for n in dorm.nodes if n.label == "blanket")
        assert [n.label for n in dorm.ancestors_of(blanket.id)] == ["bed", "floor"]

    def test_a_pen_inside_a_cup_is_still_carried_by_the_desk(self, dorm):
        pen = next(n for n in dorm.nodes if n.label == "pen")
        assert [n.label for n in dorm.ancestors_of(pen.id)] == ["cup", "desk", "floor"]

    def test_the_floor_carries_everything_standing_on_it(self, dorm):
        floor = next(n for n in dorm.nodes if n.label == "floor")
        assert labels(dorm.descendants_of(floor.id)) == ["bed", "blanket", "cup", "desk", "pen"]

    def test_children_can_be_asked_for_by_the_kind_of_edge(self, dorm):
        cup = next(n for n in dorm.nodes if n.label == "cup")
        assert labels(dorm.children_of(cup.id, "inside")) == ["pen"]
        assert dorm.children_of(cup.id, "rests_on") == []

    def test_a_room_has_roots(self, dorm):
        assert labels(dorm.roots()) == ["floor", "wall"]


class TestWhatBlocksARoute:
    def test_something_on_the_floor_is_an_obstacle(self, dorm):
        assert "bed" in labels(dorm.obstacles())

    def test_a_cup_on_a_desk_does_not_narrow_the_aisle_beside_it(self, dorm):
        """The cup is inside the desk's footprint, so counting it would shrink
        every route past the desk by the width of a cup that blocks nobody."""
        assert "cup" not in labels(dorm.obstacles())
        assert "pen" not in labels(dorm.obstacles())
        assert "blanket" not in labels(dorm.obstacles())


class TestTheTreeHasToHold:
    def test_an_edge_kind_without_a_parent_is_refused(self):
        with pytest.raises(ValidationError, match="needs a parent_id"):
            SceneNode(
                id=uuid.uuid4(), kind="object", label="x", raw_category="x",
                dimensions=Vec3(x=1, y=1, z=1), transform=Mat4.translation(0, 0, 0),
                relation="rests_on",
            )

    def test_a_parent_that_is_not_in_the_graph_is_refused(self):
        orphan = node("orphan", parent=uuid.uuid4(), relation="rests_on")
        with pytest.raises(ValidationError, match="not in the graph"):
            SceneGraph(scan_id=uuid.uuid4(), nodes=[orphan])

    def test_a_loop_is_refused_rather_than_hanging_a_walk(self):
        first, second = node("first"), node("second")
        looped = [
            first.model_copy(update={"parent_id": second.id, "relation": "rests_on"}),
            second.model_copy(update={"parent_id": first.id, "relation": "rests_on"}),
        ]
        with pytest.raises(ValidationError, match="loops back"):
            SceneGraph(scan_id=uuid.uuid4(), nodes=looped)


class TestGraphsWrittenBeforeEdgesHadNames:
    def test_an_object_that_named_a_parent_was_resting_on_it(self):
        parent = node("desk")
        stored = {
            "id": str(uuid.uuid4()), "kind": "object", "label": "lamp", "raw_category": "lamp",
            "dimensions": {"x": 1, "y": 1, "z": 1},
            "transform": Mat4.translation(0, 0, 0).model_dump(),
            "parent_id": str(parent.id),
        }
        assert SceneNode.model_validate(stored).relation == "rests_on"

    def test_a_door_that_named_a_wall_was_cut_into_it(self):
        stored = {
            "id": str(uuid.uuid4()), "kind": "door", "label": "door", "raw_category": "door",
            "dimensions": {"x": 1, "y": 1, "z": 2},
            "transform": Mat4.translation(0, 0, 0).model_dump(),
            "parent_id": str(uuid.uuid4()),
        }
        assert SceneNode.model_validate(stored).relation == "cut_into"

    def test_a_door_still_stands_on_the_floor(self):
        stored = {
            "id": str(uuid.uuid4()), "kind": "door", "label": "door", "raw_category": "door",
            "dimensions": {"x": 1, "y": 1, "z": 2},
            "transform": Mat4.translation(0, 0, 0).model_dump(),
            "parent_id": str(uuid.uuid4()),
        }
        assert SceneNode.model_validate(stored).touches_floor
