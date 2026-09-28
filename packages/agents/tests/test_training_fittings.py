import json

import pytest
from standardphysics_agents.fix.moves import top_of
from standardphysics_agents.training import TrainingChecker, prompt_messages, score_completion
from standardphysics_agents.training.catalog import CATALOG
from standardphysics_agents.training.edits import TrainingEdits, apply_edits, edits_json, parse_edits
from standardphysics_agents.training.fittings import (
    HeightChange,
    LoweredSection,
    Replacement,
    add_lowered_section,
    change_height,
    replace,
)
from standardphysics_agents.training.prices import (
    CARRY_POINT_OF_SALE,
    construction_price,
    fixture_move_price,
    furniture_price,
    wall_shift_price,
)
from standardphysics_agents.training.prompt import FITTINGS_SYSTEM_PROMPT, SYSTEM_PROMPT
from standardphysics_contracts import Mat4, SceneNode, Vec3, to_inches, to_meters
from standardphysics_fixtures import node_id
from standardphysics_pipeline import footprint, gap_between

REGISTER = node_id("register")
COUNTER = node_id("counter")


def _with(graph, *nodes):
    return graph.model_copy(update={"nodes": [*graph.nodes, *nodes]})


def _register_on_the_counter(graph):
    """The demo: a cash register on the 47 inch ordering counter, customer side."""
    register = SceneNode(id=REGISTER, kind="object", label="Cash register", raw_category="electronics",
                         dimensions=Vec3(x=0.35, y=0.28, z=0.25),
                         transform=Mat4.translation(0.3, 3.42, to_meters(47.0) + 0.125), movable=False)
    return _with(graph, register)


def _soap_dispenser(top_inches):
    tall = 0.25
    return SceneNode(id=node_id("soap"), kind="object", label="Soap dispenser", raw_category="storage",
                     dimensions=Vec3(x=0.15, y=0.1, z=tall),
                     transform=Mat4.translation(2.94, -1.0, to_meters(top_inches) - tall / 2), movable=False)


@pytest.fixture
def fittings(scenario, pipeline, pack, ledger):
    return TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline, scope="fittings")


@pytest.fixture
def layout(scenario, pipeline, pack, ledger):
    return TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline)


def _section(end="start", carry=(REGISTER,)):
    return LoweredSection(counter_id=COUNTER, end=end, carry=list(carry))


def test_a_height_change_rebuilds_a_table_and_carries_what_stands_on_it(graph):
    table = graph.by_id(node_id("table_1"))
    cup = SceneNode(id=node_id("cup"), kind="object", label="Cup", raw_category="object",
                    dimensions=Vec3(x=0.08, y=0.08, z=0.1),
                    transform=Mat4.translation(table.transform.position.x, table.transform.position.y, 0.75 + 0.05))
    changed = change_height(_with(graph, cup), HeightChange(node_id=table.id, top_inches=34.0))
    assert to_inches(top_of(changed.by_id(table.id))) == pytest.approx(34.0)
    assert changed.by_id(table.id).transform.position.z - changed.by_id(table.id).dimensions.z / 2 == pytest.approx(0.0)
    assert to_inches(top_of(changed.by_id(cup.id)) - 0.1) == pytest.approx(34.0)


def test_a_height_change_stays_within_what_the_piece_is_sold_at(graph):
    with pytest.raises(ValueError):
        change_height(graph, HeightChange(node_id=node_id("table_1"), top_inches=60.0))
    with pytest.raises(ValueError):
        change_height(graph, HeightChange(node_id=node_id("wall_west"), top_inches=40.0))
    assert parse_edits('{"height_changes":[{"node_id":"%s","top_inches":200}]}' % node_id("table_1")) is None


def test_a_mounted_item_is_rehung_rather_than_resized(graph):
    rehung = change_height(_with(graph, _soap_dispenser(55.0)), HeightChange(node_id=node_id("soap"), top_inches=46.0))
    dispenser = rehung.by_id(node_id("soap"))
    assert to_inches(top_of(dispenser)) == pytest.approx(46.0) and dispenser.dimensions.z == pytest.approx(0.25)


def test_a_replacement_takes_the_catalog_size_in_the_same_place(graph):
    table = graph.by_id(node_id("table_1"))
    swapped = replace(graph, Replacement(node_id=table.id, catalog_item="accessible_two_top")).by_id(table.id)
    assert swapped.label == CATALOG["accessible_two_top"].label
    assert to_inches(top_of(swapped)) == pytest.approx(30.0)
    assert to_inches(swapped.dimensions.x) == pytest.approx(30.0)
    assert swapped.transform.position.x == table.transform.position.x
    with pytest.raises(ValueError):
        replace(graph, Replacement(node_id=table.id, catalog_item="lowered_counter_section"))
    assert parse_edits('{"replacements":[{"node_id":"%s","catalog_item":"sofa"}]}' % table.id) is None


def test_a_lowered_section_is_cut_from_the_counter_with_the_register_on_it(graph):
    room = _register_on_the_counter(graph)
    fitted = add_lowered_section(room, _section())
    counter = fitted.by_id(COUNTER)
    section = next(node for node in fitted.nodes if node.label == "Lowered counter section")
    register = fitted.by_id(REGISTER)
    assert to_inches(max(counter.dimensions.x, counter.dimensions.y)) == pytest.approx(to_inches(3.2) - 36.0)
    assert to_inches(section.dimensions.x) == pytest.approx(36.0) and to_inches(top_of(section)) == pytest.approx(36.0)
    assert gap_between(footprint(section), footprint(counter)) == pytest.approx(0.0, abs=1e-6)
    assert gap_between(footprint(register), footprint(counter)) > 0.0
    assert to_inches(register.transform.position.z - register.dimensions.z / 2) == pytest.approx(36.0)


def test_only_a_register_on_that_counter_can_be_carried(graph):
    with pytest.raises(ValueError):
        add_lowered_section(graph, _section(carry=[node_id("table_1")]))
    assert parse_edits('{"add_lowered_section":[{"counter_id":"%s","end":"start","length_inches":30}]}'
                       % COUNTER) is None


def test_the_demo_counter_clears_with_a_section_and_the_register_carried(graph, fittings):
    room = _register_on_the_counter(graph)
    assert "service_counter_height" in {f.check_id for f in fittings.fixable_problems(fittings.assess(room))}
    candidate = apply_edits(room, TrainingEdits(add_lowered_section=[_section()]))
    verdicts = fittings.assess(candidate).verdicts
    assert verdicts["service_counter_height"] == "passes"
    assert verdicts["point_of_sale_height"] == "passes"


def test_leaving_the_register_on_the_high_part_is_a_new_problem(graph, fittings):
    room = _register_on_the_counter(graph)
    candidate = apply_edits(room, TrainingEdits(add_lowered_section=[_section(carry=())]))
    assert fittings.assess(candidate).verdicts["point_of_sale_height"] == "problem"


def test_an_added_piece_must_not_land_on_anything(graph, fittings):
    chair = graph.by_id(node_id("chair_1"))
    beside = chair.model_copy(update={"id": node_id("chair_beside"), "transform": Mat4.translation(-1.35, 2.2, 0.45)})
    swap = TrainingEdits(replacements=[Replacement(node_id=node_id("table_1"), catalog_item="accessible_four_top")])
    verdict = score_completion(edits_json(swap), _with(graph, beside), fittings)
    assert not verdict.hard_constraints_pass and "collided" in verdict.reason


def test_moving_the_register_costs_least_and_a_wall_shift_most(graph):
    carry = CARRY_POINT_OF_SALE
    furniture = furniture_price(0.5)
    height = construction_price(graph, TrainingEdits(height_changes=[HeightChange(node_id=node_id("table_1"),
                                                                                  top_inches=30.0)]))
    swap = construction_price(graph, TrainingEdits(replacements=[Replacement(node_id=node_id("table_1"),
                                                                             catalog_item="accessible_two_top")]))
    assert carry < furniture < height <= swap < fixture_move_price(3.0) < wall_shift_price(1.0)


def test_a_section_costs_less_than_lowering_the_whole_counter(graph):
    section = construction_price(graph, TrainingEdits(add_lowered_section=[_section()]))
    whole = construction_price(graph, TrainingEdits(height_changes=[HeightChange(node_id=COUNTER, top_inches=36.0)]))
    assert section < whole


def test_the_layout_scope_still_only_asks_what_furniture_can_fix(graph, layout, fittings):
    room = _register_on_the_counter(graph)
    assert "service_counter_height" not in {f.check_id for f in layout.fixable_problems(layout.assess(room))}
    assert set(fittings.unfixable_rules()) >= {"door_clear_width", "entrance_threshold", "floor_surface"}
    assert "service_counter_height" not in fittings.unfixable_rules()
    assert "service_counter_height" in layout.unfixable_rules()


def test_fittings_measure_reach_where_production_asks(graph, fittings, scenario, pipeline, pack, ledger):
    room = _with(graph, _soap_dispenser(55.0))
    problems = fittings.fixable_problems(fittings.assess(room))
    assert "reach_range" in {f.check_id for f in problems}
    lowered = TrainingEdits(height_changes=[HeightChange(node_id=node_id("soap"), top_inches=47.0)])
    assert fittings.assess(apply_edits(room, lowered)).verdicts["reach_range"] == "passes"
    production = TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline, max_tier=3)
    assert production.assess(room).verdicts["reach_range"] == "question"


def test_bar_height_tables_ask_for_one_at_dining_height(graph, fittings):
    raised = graph.model_copy(update={"nodes": [
        change_height(graph, HeightChange(node_id=node.id, top_inches=42.0)).by_id(node.id)
        if node.label == "Table" else node for node in graph.nodes]})
    problems = {f.check_id for f in fittings.fixable_problems(fittings.assess(raised))}
    assert "dining_surface_height" in problems
    one = TrainingEdits(height_changes=[HeightChange(node_id=node_id("table_4"), top_inches=30.0)])
    assert fittings.assess(apply_edits(raised, one)).verdicts["dining_surface_height"] == "passes"


def test_the_fittings_prompt_shows_heights_counters_and_the_catalog(graph, fittings, layout):
    room = _register_on_the_counter(graph)
    system, user = prompt_messages(room, fittings)
    assert system["content"] == FITTINGS_SYSTEM_PROMPT
    for shape in ('"height_changes"', '"replacements"', '"add_lowered_section"', "accessible_two_top"):
        assert shape in system["content"]
    view = json.loads(user["content"])
    counter = next(item for item in view["counters"] if item["id"] == str(COUNTER))
    assert counter["top_inches"] == 47.0 and counter["point_of_sale"][0]["id"] == str(REGISTER)
    assert any(item["label"] == "Table" and item["top_inches"] == 29.5 for item in view["heights"])
    assert prompt_messages(room, layout)[0]["content"] == SYSTEM_PROMPT
    assert "heights" not in json.loads(prompt_messages(room, layout)[1]["content"])


def test_a_register_moves_with_its_counter_rather_than_on_its_own(graph):
    room = _register_on_the_counter(graph)
    with pytest.raises(ValueError):
        change_height(room, HeightChange(node_id=REGISTER, top_inches=36.0))
    lowered = change_height(room, HeightChange(node_id=COUNTER, top_inches=36.0))
    assert to_inches(lowered.by_id(REGISTER).transform.position.z - 0.125) == pytest.approx(36.0)


def test_the_menu_offers_a_lowered_section_for_a_counter_too_high_to_order_from(graph, fittings):
    from standardphysics_agents.training.menu import build_menu

    room = _register_on_the_counter(graph)
    menu = build_menu(room, fittings)
    too_high = next(f.id for f in fittings.assess(room).problems if f.check_id == "service_counter_height")
    height = menu.problems[too_high]
    sections = [option for option in menu.options if option.edits.add_lowered_section]
    assert sections, [option.wording for option in menu.options]
    clearing = [option for option in sections if height in option.effect["clears"]]
    assert clearing
    assert all(REGISTER in option.edits.add_lowered_section[0].carry for option in clearing)
    assert all("(construction)" in option.wording for option in sections)


def test_the_layout_scope_menu_offers_no_fittings(graph, layout):
    from standardphysics_agents.training.menu import build_menu

    menu = build_menu(_register_on_the_counter(graph), layout)
    assert not any(option.edits.add_lowered_section or option.edits.height_changes or option.edits.replacements
                   for option in menu.options)


def test_the_menu_ranks_a_lowered_section_above_rebuilding_the_whole_counter(graph, fittings):
    from standardphysics_agents.training.menu import _rank, build_menu

    menu = build_menu(_register_on_the_counter(graph), fittings)
    sections = [_rank(option.effect) for option in menu.options if option.edits.add_lowered_section]
    rebuilds = [_rank(option.effect) for option in menu.options if option.edits.height_changes]
    assert sections and rebuilds
    assert min(sections) < min(rebuilds)
