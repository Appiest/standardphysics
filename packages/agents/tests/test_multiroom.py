import json
import math
import sqlite3

import pytest
from standardphysics_agents.training import TrainingChecker, score_completion, scramble, search_target, shaped_reward
from standardphysics_agents.training.phantoms import phantoms, pin, without_unmeasured
from standardphysics_agents.training.quality import (
    SIGHT_BLOCKING_HEIGHT_METERS,
    layout_quality,
    pair_term,
    relation_score,
    sight_term,
    viewpoint,
    wall_term,
)
from standardphysics_agents.training.reward import MOVED_PINNED, QUALITY_WEIGHT
from standardphysics_agents.training.rooms import ScanPlan, build_window
from standardphysics_agents.training.scans import export_scan, read_only
from standardphysics_agents.training.split import DROPPED, HELDOUT, TRAIN, HoldOut, RoomRecord, assign, pick_floor
from standardphysics_agents.training.windows import cut, reproduction, summarize_problem
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3, lies_flat
from standardphysics_fixtures import node_id
from standardphysics_pipeline.footprints import floor_polygon, polygon_bounds


def piece(name, label, centre, dims, movable=True, kind="object", **extra):
    return SceneNode(id=node_id(f"multiroom_{name}"), kind=kind, label=label, raw_category=kind,
                     dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]), transform=Mat4.translation(*centre),
                     movable=movable, **extra)


def turned(node, degrees, dx=0.0, dy=0.0):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    p = node.transform.position
    return node.model_copy(update={"transform": Mat4(m=[c, -s, 0, p.x + dx, s, c, 0, p.y + dy, 0, 0, 1, p.z,
                                                       0, 0, 0, 1])})


def replaced(graph, *nodes):
    by_id = {node.id: node for node in nodes}
    return graph.model_copy(update={"nodes": [by_id.get(node.id, node) for node in graph.nodes]})


def library() -> SceneGraph:
    """A 20 by 8 m floor: two storage units leave a 0.6 m aisle at x=3, and chairs sit far off at x=16."""
    nodes = [
        piece("floor", "Floor", (10.0, 0.0, 0.0), (20.0, 8.0, 0.0), False, "floor"),
        piece("wall_south", "Wall", (10.0, -4.0, 1.25), (20.0, 0.02, 2.5), False, "wall"),
        piece("wall_north", "Wall", (10.0, 4.0, 1.25), (20.0, 0.02, 2.5), False, "wall"),
        piece("storage_low", "Divider", (3.0, -2.15, 0.9), (0.5, 3.7, 1.8)),
        piece("storage_high", "Divider", (3.0, 2.15, 0.9), (0.5, 3.7, 1.8)),
    ]
    nodes += [piece(f"chair_{i}", "Chair", (15.0 + i * 0.7, 1.0, 0.45), (0.5, 0.5, 0.9)) for i in range(4)]
    return SceneGraph(scan_id=node_id("multiroom_library"), nodes=nodes)


LIBRARY_ROUTE = Scenario(name="Walk", stops=[Stop(name="West", position=Vec3(x=0.8, y=0.0, z=0.0)),
                                            Stop(name="East", position=Vec3(x=6.0, y=0.0, z=0.0))])


def test_export_keeps_the_first_of_a_duplicated_id_and_drops_display_fields(tmp_path, graph, scenario):
    raw = json.loads(graph.model_dump_json())
    first = raw["nodes"][0]
    raw["nodes"][0] = {**first, "appearance": {"base_color": "#aabbcc", "material": "wood"}}
    raw["nodes"].append({**first, "transform": {"m": [1, 0, 0, 14.0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}})
    database = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE scans (id TEXT, name TEXT); CREATE TABLE revisions (scan_id TEXT, revision INT, graph_json TEXT);"
        "CREATE TABLE scenarios (scan_id TEXT, scenario_json TEXT);")
    connection.execute("INSERT INTO scans VALUES ('s1', 'shop')")
    for revision in (0, 3):
        connection.execute("INSERT INTO revisions VALUES ('s1', ?, ?)", (revision, json.dumps(raw)))
    connection.execute("INSERT INTO scenarios VALUES ('s1', ?)", (scenario.model_dump_json(),))
    connection.commit()
    connection.close()

    exported = export_scan(read_only(database), "shop")
    assert exported.latest_revision == 3 and exported.scenario_model() == scenario
    assert len(exported.latest["nodes"]) == len(graph.nodes)
    assert exported.duplicates_dropped == [{"node_id": first["id"], "kind": first["kind"], "label": first["label"],
                                            "metres_from_kept": pytest.approx(math.hypot(
                                                14.0 - first["transform"]["m"][3], first["transform"]["m"][7]), abs=1e-3)}]
    assert "appearance" not in exported.latest["nodes"][0]
    assert exported.graph().nodes[0].transform == graph.nodes[0].transform
    with pytest.raises(sqlite3.OperationalError):
        read_only(database).execute("INSERT INTO scans VALUES ('s2', 'x')")


def test_phantom_filter_pins_unconfirmed_discovery_implausible_and_floating_pieces():
    room = library()
    extra = [
        piece("phantom_sofa", "Sofa", (8.0, 0.0, 0.46), (0.47, 0.18, 0.38), labeled_by="discovery"),
        piece("confirmed_find", "Chair", (9.0, 0.0, 0.45), (0.5, 0.5, 0.9), labeled_by="owner"),
        piece("tiny_chair", "Chair", (10.0, 0.0, 0.02), (0.5, 0.5, 0.04)),
        piece("floating_bin", "Box", (11.0, 0.0, 0.8), (0.4, 0.4, 0.4)),
        piece("table", "Table", (12.0, 0.0, 0.375), (1.0, 1.0, 0.75)),
        piece("laptop", "Laptop", (12.0, 0.0, 0.77), (0.3, 0.2, 0.03)),
        piece("unmeasured", "Candidate television", (13.0, 0.0, 1.0), (0.0, 0.0, 0.0), False),
    ]
    room = room.model_copy(update={"nodes": [*room.nodes, *extra]})
    found = {item.label: item.reasons for item in phantoms(room)}
    assert found == {
        "Sofa": ("unconfirmed_discovery", "implausible_size", "floating"),
        "Chair": ("implausible_size",),
        "Box": ("floating",),
    }
    pinned = pin(room, phantoms(room))
    assert not pinned.by_id(node_id("multiroom_phantom_sofa")).movable
    assert pinned.by_id(node_id("multiroom_laptop")).movable
    assert node_id("multiroom_unmeasured") not in {node.id for node in without_unmeasured(room).nodes}


def test_window_keeps_nearby_nodes_and_crops_the_floor():
    window = cut(library(), (3.0, 0.0), 3.5)
    names = {node.id for node in window.nodes}
    assert node_id("multiroom_storage_low") in names and node_id("multiroom_chair_0") not in names
    floor = next(node for node in window.nodes if lies_flat(node))
    min_x, min_y, max_x, max_y = polygon_bounds(floor_polygon(floor))
    assert (min_x, max_x) == pytest.approx((0.0, 6.5)) and (min_y, max_y) == pytest.approx((-3.5, 3.5))


def aisle_plan() -> tuple[ScanPlan, dict]:
    room = library()
    checker = TrainingChecker(LIBRARY_ROUTE)
    aisle = next(f for f in checker.fixable_problems(checker.assess(room)) if f.check_id == "route_clear_width")
    return ScanPlan("library", room, LIBRARY_ROUTE), summarize_problem(aisle)


def test_window_around_a_problem_reproduces_it():
    plan, seed = aisle_plan()
    window, log = build_window(plan, (3.0, 0.0), seed, "library:w00")
    assert window is not None and log["kept"] and window.route == "clipped"
    assert window.reproduction["seed_reproduced"] and not window.reproduction["invented"]


def test_window_that_loses_its_problem_is_dropped():
    plan, seed = aisle_plan()
    window, log = build_window(plan, (3.0, 3.2), seed, "library:w01", radius=2.0)
    assert window is None and log["why"] == "no route reproduces the scan"


def test_reproduction_rejects_an_invented_problem():
    plan, seed = aisle_plan()
    checker = TrainingChecker(LIBRARY_ROUTE)
    problems = checker.fixable_problems(checker.assess(plan.graph))
    assert reproduction(problems, problems, (3.0, 0.0), seed)["ok"]
    assert not reproduction(problems, [], (3.0, 0.0), seed)["ok"]


def room(window_id, scan, floor, pieces):
    return RoomRecord(window_id, scan, floor, frozenset(pieces))


def test_split_holds_out_whole_places_and_drops_shared_furniture():
    rooms = [
        room("shop:whole", "shop", "f0", {"a"}),
        room("lib:w0", "lib", "east", {"b", "c"}),
        room("lib:w1", "lib", "east", {"d"}),
        room("lib:w2", "lib", "west", {"c", "e"}),
        room("lib:w3", "lib", "west", {"f"}),
        room("lib:w4", "lib", "west", {"g"}),
    ]
    assert pick_floor(rooms, "lib", 2) == "east"
    splits = assign(rooms, HoldOut(scans=frozenset({"shop"}), floors=frozenset({("lib", "east")})))
    assert splits == {"shop:whole": HELDOUT, "lib:w0": HELDOUT, "lib:w1": HELDOUT,
                      "lib:w2": DROPPED, "lib:w3": TRAIN, "lib:w4": TRAIN}


def test_a_45_degree_turn_halves_the_wall_angle_score():
    owner = library()
    storage = owner.by_id(node_id("multiroom_storage_low"))
    after = replaced(owner, turned(storage, 45.0))
    wall = wall_term(owner, after, {storage.id})
    angle_part = (wall - 1 / 3) / (2 / 3)
    assert relation_score(45.0, 0.0) == pytest.approx(2 / 3 * 0.5 + 1 / 3)
    assert angle_part == pytest.approx(0.5, abs=0.02)


def test_turning_a_chair_away_from_its_table_lowers_pairs():
    table = piece("pair_table", "Table", (5.0, 0.0, 0.375), (1.0, 1.0, 0.75))
    chair = piece("pair_chair", "Chair", (4.2, 0.0, 0.45), (0.5, 0.5, 0.9))
    owner = library().model_copy(update={"nodes": [*library().nodes, table, chair]})
    assert pair_term(owner, replaced(owner, turned(chair, 0.0, dy=0.05)), {chair.id}) > 0.95
    assert pair_term(owner, replaced(owner, turned(chair, 180.0)), {chair.id}) < 0.7


def test_pair_scores_are_unchanged_by_which_local_axis_facing_reads():
    """`_facing_off` switched from `yaw_degrees` (local +X) to `front_heading_degrees`
    (local -Y) because that is the axis a chair actually faces, but `pair_term`
    only ever compares two headings of the *same* seat, so a constant 90 degree
    shift on both sides cancels. These are the exact scores the old `yaw_degrees`
    convention produced, captured before the switch, as a regression check that
    the fix changed the seat's stated meaning, not the numbers it produces."""
    table = piece("pair_table", "Table", (5.0, 0.0, 0.375), (1.0, 1.0, 0.75))
    chair = piece("pair_chair", "Chair", (4.2, 0.0, 0.45), (0.5, 0.5, 0.9))
    owner = library().model_copy(update={"nodes": [*library().nodes, table, chair]})
    expected = {
        0.0: 1.0,
        20.0: 0.8518518518518519,
        45.0: 0.6666666666666667,
        90.0: 0.33333333333333337,
        135.0: 0.33333333333333337,
        180.0: 0.33333333333333337,
        -60.0: 0.5555555555555556,
    }
    for degrees, score in expected.items():
        after = replaced(owner, turned(chair, degrees))
        assert pair_term(owner, after, {chair.id}) == pytest.approx(score, abs=1e-9)


def test_a_tall_shelf_in_front_of_the_counter_lowers_sight(graph, pipeline):
    eye = viewpoint(graph, pipeline)
    shelf = piece("shelf", "Shelf", (eye[0], eye[1] - 0.8, 0.9), (1.2, 0.3, 1.8))
    assert shelf.dimensions.z > SIGHT_BLOCKING_HEIGHT_METERS
    before = graph.model_copy(update={"nodes": [*graph.nodes, turned(shelf, 0.0, dx=30.0)]})
    after = graph.model_copy(update={"nodes": [*graph.nodes, shelf]})
    assert sight_term(before, before, pipeline) == 1.0
    assert sight_term(before, after, pipeline) < 0.9


def test_moving_a_pinned_piece_scores_zero(graph, scenario, pipeline, pack, ledger):
    table = node_id("table_1")
    checker = TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline, pinned=frozenset({table}))
    completion = '{"moves":[{"node_id":"%s","dx":0.1,"dy":0,"rotation_degrees":0}]}' % table
    verdict = score_completion(completion, graph, checker)
    assert verdict.reward == 0.0 and verdict.reason == MOVED_PINNED


def test_quality_only_moves_accepted_rewards_and_by_at_most_its_weight(graph, scenario, pipeline, pack, ledger):
    assert shaped_reward(0.5, False, 1.0, 1.0) - shaped_reward(0.5, False, 1.0, 0.0) == pytest.approx(QUALITY_WEIGHT)
    assert shaped_reward(0.5, False, 1.0, 5.0) == shaped_reward(0.5, False, 1.0, 1.0)
    checker = TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline, owner_layout=graph)
    variant = scramble(graph, checker, 1, seed=1)[0].graph
    target = search_target(variant, checker)
    assert target is not None and target.verdict.quality is not None
    verdict, q = target.verdict, target.verdict.quality["q"]
    assert verdict.reward == shaped_reward(verdict.shortfall_recovered, verdict.fixable_left == 0,
                                           verdict.disruption_meters, q)
    assert 0.0 <= q <= 1.0 and layout_quality(variant, variant, graph, pipeline).q == pytest.approx(1.0)
    assert score_completion("nonsense", variant, checker).quality is None
