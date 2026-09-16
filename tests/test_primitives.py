"""The vocabulary rules and questions are both composed out of.

The three questions in docs/MISSION.md are answered here as compositions, to
prove the primitives reach them without anything in the source knowing what a
pen or a whiteboard is.
"""

import uuid

import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, SurfaceText, Vec3
from standardphysics_pipeline.primitives import BadArguments, Context, UnknownPrimitive, call, vocabulary


def node(name, kind="object", parent=None, relation=None, at=(0.0, 0.0, 0.0), size=(0.5, 0.5, 0.5), texts=()):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"prim-{name}"),
        kind=kind,
        label=name.capitalize(),
        raw_category=name.replace(" ", "_"),
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*at),
        parent_id=parent,
        relation=relation,
        texts=list(texts),
    )


@pytest.fixture
def dorm():
    """A dorm room: a desk with a cup of pens on it, a bed, and a whiteboard."""
    floor = node("floor", kind="floor", size=(4.0, 4.0, 0.01))
    wall = node("wall", kind="wall", at=(0.0, 2.0, 1.2), size=(4.0, 0.1, 2.4))
    desk = node("desk", parent=floor.id, relation="rests_on", at=(1.0, 0.0, 0.37), size=(1.2, 0.6, 0.74))
    cup = node("cup", parent=desk.id, relation="rests_on", at=(1.0, 0.0, 0.79), size=(0.1, 0.1, 0.1))
    pens = [
        node(f"pen {index}", parent=cup.id, relation="inside", at=(1.0, 0.0, 0.82), size=(0.01, 0.01, 0.14))
        for index in range(3)
    ]
    bed = node("bed", parent=floor.id, relation="rests_on", at=(-1.0, 0.0, 0.25), size=(2.0, 0.9, 0.5))
    blanket = node("blanket", parent=bed.id, relation="rests_on", at=(-1.0, 0.0, 0.52), size=(2.0, 0.9, 0.04))
    whiteboard = node(
        "whiteboard",
        parent=wall.id,
        relation="mounted_on",
        at=(0.0, 1.9, 1.4),
        size=(1.2, 0.02, 0.9),
        texts=[SurfaceText(text="Physics midterm Friday 9am", confidence=0.93, evidence_frame_ids=["frame-12"])],
    )
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, wall, desk, cup, *pens, bed, blanket, whiteboard])


@pytest.fixture
def context(dorm):
    return Context(graph=dorm)


def find(graph, label):
    return next(n for n in graph.nodes if n.label.lower() == label)


class TestTheVocabularyIsWhatAPlannerSees:
    def test_every_primitive_describes_itself_and_its_arguments(self):
        for spec in vocabulary():
            assert spec.summary.endswith("."), spec.name
            assert spec.returns in ("quantity", "nodes", "texts", "truth")
            assert "properties" in spec.arguments or spec.arguments.get("type") == "object"

    def test_a_name_outside_the_vocabulary_is_refused(self, context):
        with pytest.raises(UnknownPrimitive):
            call("delete_everything", {}, context)

    def test_arguments_that_do_not_fit_are_refused_before_anything_runs(self, context):
        with pytest.raises(BadArguments):
            call("height_of", {"node_id": "not-a-uuid"}, context)

    def test_a_node_from_another_room_is_not_measurable(self, context):
        result = call("height_of", {"node_id": str(uuid.uuid4())}, context)
        assert result.quality == "not_measurable"
        assert not result.measured


class TestAreAllOfThePensInMyDormRoom:
    """Composed, not hardcoded: find the pens, then walk what carries each one."""

    def test_every_pen_traces_back_to_the_room(self, dorm, context):
        pens = call("find_objects", {"words": "pen"}, context).nodes()
        assert len(pens) == 3
        for pen_id in pens:
            carriers = call("what_carries", {"node_id": str(pen_id)}, context).nodes()
            assert [dorm.by_id(item).label for item in carriers] == ["Cup", "Desk", "Floor"]

    def test_the_cup_is_what_actually_holds_them(self, dorm, context):
        cup = find(dorm, "cup")
        inside = call("objects_inside", {"node_id": str(cup.id)}, context).nodes()
        assert len(inside) == 3

    def test_the_desk_carries_the_pens_without_them_resting_on_it(self, dorm, context):
        desk = find(dorm, "desk")
        directly_on = call("objects_on", {"node_id": str(desk.id)}, context).nodes()
        carried = call("everything_carried_by", {"node_id": str(desk.id)}, context).nodes()
        assert [dorm.by_id(item).label for item in directly_on] == ["Cup"]
        assert len(carried) == 4


class TestWhatDidItSayOnMyWhiteboard:
    def test_the_words_come_back_with_the_frame_they_were_read_from(self, dorm, context):
        whiteboard = find(dorm, "whiteboard")
        result = call("text_on", {"node_id": str(whiteboard.id)}, context)
        assert result.payload.texts == ["Physics midterm Friday 9am"]
        assert result.evidence.frames == ["frame-12"]
        assert result.measured

    def test_a_surface_with_nothing_read_off_it_says_so_rather_than_nothing(self, dorm, context):
        desk = find(dorm, "desk")
        result = call("text_on", {"node_id": str(desk.id)}, context)
        assert result.payload.texts == []
        assert result.quality == "needs_another_look"
        assert "never saw it closely" in result.note


class TestMeasuring:
    def test_a_desk_is_measured_from_the_floor_to_its_top(self, dorm, context):
        desk = find(dorm, "desk")
        result = call("height_of", {"node_id": str(desk.id)}, context)
        assert result.payload.unit == "in"
        assert result.payload.value == pytest.approx(29.13, abs=0.05)

    def test_the_camera_is_sent_to_the_surface_that_was_measured(self, dorm, context):
        desk = find(dorm, "desk")
        result = call("height_of", {"node_id": str(desk.id)}, context)
        assert result.evidence.at.z == pytest.approx(0.74, abs=0.01)

    def test_size_is_asked_for_by_the_way_it_is_measured(self, dorm, context):
        desk = find(dorm, "desk")
        assert call("size_of", {"node_id": str(desk.id), "axis": "width"}, context).number() == pytest.approx(47.24, abs=0.05)
        assert call("size_of", {"node_id": str(desk.id), "axis": "depth"}, context).number() == pytest.approx(23.62, abs=0.05)

    def test_distance_is_measured_between_two_real_things(self, dorm, context):
        desk, bed = find(dorm, "desk"), find(dorm, "bed")
        span = call("distance_between", {"from_node_id": str(desk.id), "to_node_id": str(bed.id)}, context)
        # Two metres apart along x, a little less than that once the height differs.
        assert span.number() == pytest.approx(78.88, abs=0.05)
        assert set(span.evidence.subjects) == {desk.id, bed.id}

    def test_something_on_a_desk_stands_on_no_floor_of_its_own(self, dorm, context):
        cup = find(dorm, "cup")
        result = call("floor_area_of", {"node_id": str(cup.id)}, context)
        assert result.quality == "not_measurable"
        assert "on the desk" in result.note

    def test_a_bed_does_stand_on_the_floor(self, dorm, context):
        bed = find(dorm, "bed")
        result = call("floor_area_of", {"node_id": str(bed.id)}, context)
        assert result.measured
        assert result.payload.unit == "sq in"
