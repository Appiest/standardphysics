"""A floor joined from several walks shows each walk the way its processing left it.

On Moffitt Library's floor, one stretch of built-in seating carried two sofas,
two benches, a counter and a table at once. The floor took every walk's boxes
as its phone measured them, so the 110 boxes the walks' own processing had
removed came back, and where two walks overlapped both walks' boxes of the same
thing were kept.
"""

from __future__ import annotations

import uuid

import numpy as np
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3

from standardphysics_api.combine import walk_on_floor, without_repeats_across_walks

SCAN = uuid.uuid4()


def box(label: str, at: tuple[float, float, float], size: tuple[float, float, float], **fields) -> SceneNode:
    x, y, z = at
    return SceneNode(
        id=fields.pop("id", uuid.uuid4()),
        kind=fields.pop("kind", "object"),
        label=label,
        raw_category=label.lower(),
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4(m=[1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]),
        **fields,
    )


def graph(*nodes: SceneNode) -> SceneGraph:
    return SceneGraph(scan_id=SCAN, nodes=list(nodes))


def slid(dx: float, dy: float) -> np.ndarray:
    motion = np.eye(4)
    motion[0, 3], motion[1, 3] = dx, dy
    return motion


def same_frame(frame: str) -> str:
    return frame


def position(node: SceneNode) -> tuple[float, float, float]:
    return node.transform.m[3], node.transform.m[7], node.transform.m[11]


class TestTheFloorTakesEachWalkAsProcessed:
    def test_a_box_the_walk_removed_stays_removed(self):
        seat = box("Sofa", (0, 0, 0.4), (2, 1, 0.8))
        bench = box("Bench", (0, 0, 0.45), (2.1, 1, 0.9), labeled_by="discovery")
        walk = graph(bench)

        floor = walk_on_floor(walk, graph(seat), graph(), slid(5, 0), {seat.id: uuid.uuid4()}, same_frame)

        assert [node.label for node in floor] == ["Bench"]

    def test_a_box_the_walk_renamed_keeps_its_floor_id_and_takes_the_new_name(self):
        storage = box("Storage", (1, 1, 0.5), (1, 0.5, 1))
        on_floor = uuid.uuid4()
        walk = graph(storage.model_copy(update={"label": "Lockers"}))

        [lockers] = walk_on_floor(walk, graph(storage), graph(), slid(5, 0), {storage.id: on_floor}, same_frame)

        assert lockers.id == on_floor
        assert lockers.label == "Lockers"
        assert position(lockers) == (6, 1, 0.5)

    def test_a_box_the_walk_left_alone_stays_exactly_where_the_owner_placed_it(self):
        door = box("Door", (2, 0, 1), (0.9, 0, 2), kind="door")
        on_floor = uuid.uuid4()
        placed_by_owner = box("Door", (7.0004, 0, 1), (0.9, 0, 2), kind="door", id=on_floor)

        [placed] = walk_on_floor(
            graph(door), graph(door), graph(placed_by_owner), slid(5, 0), {door.id: on_floor}, same_frame
        )

        assert position(placed) == (7.0004, 0, 1)

    def test_a_box_the_walk_resized_goes_where_the_walk_was_placed(self):
        table = box("Table", (2, 0, 0.37), (1, 1, 0.74))
        on_floor = uuid.uuid4()
        placed_by_owner = box("Table", (7.0004, 0, 0.37), (1, 1, 0.74), id=on_floor)
        remeasured = table.model_copy(update={"dimensions": Vec3(x=1.2, y=1, z=0.74)})

        [placed] = walk_on_floor(
            graph(remeasured), graph(table), graph(placed_by_owner), slid(5, 0), {table.id: on_floor}, same_frame
        )

        assert position(placed) == (7, 0, 0.37)

    def test_something_resting_on_a_measured_box_points_at_that_box_on_the_floor(self):
        table = box("Table", (0, 0, 0.37), (1, 1, 0.74))
        laptop = box("Laptop", (0, 0, 0.76), (0.3, 0.2, 0.03), parent_id=table.id, relation="rests_on")
        on_floor = uuid.uuid4()

        floor = walk_on_floor(graph(table, laptop), graph(table), graph(), slid(0, 3), {table.id: on_floor}, same_frame)

        assert {node.id for node in floor} == {on_floor, laptop.id}
        assert next(node for node in floor if node.id == laptop.id).parent_id == on_floor


class TestAThingTwoWalksBoxedIsKeptOnce:
    def test_two_walks_boxing_the_same_bench_leave_one(self):
        from_west = box("Bench", (10, 20, 0.45), (1.4, 2.2, 0.9), quality="needs_another_look")
        from_north = box("Sofa", (10.05, 19.9, 0.43), (1.35, 2.1, 0.86))

        kept = without_repeats_across_walks([[from_west], [from_north]])

        assert kept == [from_north]

    def test_the_better_measured_box_is_the_one_kept(self):
        thin = box("Table", (0, 0, 0.4), (1, 1, 0.8), quality="needs_another_look")
        measured = box("Table", (0.02, 0, 0.4), (1, 1, 0.8))

        assert without_repeats_across_walks([[thin], [measured]]) == [measured]

    def test_one_walk_boxing_two_things_in_one_place_is_left_alone(self):
        first = box("Bin", (0, 0, 0.4), (0.4, 0.4, 0.8))
        second = box("Bin", (0.02, 0, 0.4), (0.4, 0.4, 0.8))

        assert without_repeats_across_walks([[first, second]]) == [first, second]

    def test_a_bag_on_a_table_seen_by_another_walk_is_not_the_table(self):
        table = box("Table", (0, 0, 0.37), (1.2, 0.8, 0.74))
        bag = box("Bag", (0, 0, 0.85), (0.4, 0.3, 0.2), labeled_by="discovery")

        assert without_repeats_across_walks([[table], [bag]]) == [table, bag]

    def test_a_wall_two_walks_both_measured_is_not_merged(self):
        wall = box("Wall", (0, 0, 1.5), (4, 0, 3), kind="wall")
        again = box("Wall", (0.01, 0, 1.5), (4, 0, 3), kind="wall")

        assert without_repeats_across_walks([[wall], [again]]) == [wall, again]

    def test_what_rested_on_a_dropped_repeat_rests_on_the_box_kept(self):
        kept_table = box("Table", (0, 0, 0.37), (1, 1, 0.74))
        repeat = box("Table", (0.03, 0, 0.37), (1, 1, 0.74), quality="needs_another_look")
        cup = box("Cup", (0, 0, 0.8), (0.1, 0.1, 0.12), parent_id=repeat.id, relation="rests_on")

        kept = without_repeats_across_walks([[kept_table], [repeat, cup]])

        assert [node.id for node in kept] == [kept_table.id, cup.id]
        assert kept[1].parent_id == kept_table.id
