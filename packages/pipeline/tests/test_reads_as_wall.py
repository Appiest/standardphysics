"""The named wall predicate both the pipeline and the checks share.

`reads_as_wall` is the one place that says what a wall is as a capture knows
it: an upright room sheet, or the wall class an ingest step or reviewer
assigned. A wall measured through a door jamb is too thick for the sheet
heuristic while its class label still names it a wall; the label is the
fallback, never the first answer.
"""

from __future__ import annotations

import uuid

from standardphysics_agents.checks.walls import upright_walls
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.occupancy import reads_as_wall


def _node(name: str, *, kind: str, center, dims) -> SceneNode:
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_URL, name),
        kind=kind,
        label=name,
        raw_category=name.casefold(),
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*center),
    )


def test_an_upright_sheet_reads_as_a_wall_by_geometry():
    sheet = _node("sheet", kind="object", center=(0, 0, 1.5), dims=(4.0, 0.04, 3.0))
    assert reads_as_wall(sheet)


def test_a_labelled_wall_too_thick_for_the_sheet_heuristic_reads_as_a_wall():
    thick = _node("thick", kind="wall", center=(0, 0, 1.5), dims=(4.0, 0.15, 3.0))
    assert reads_as_wall(thick)


def test_a_table_does_not_read_as_a_wall():
    table = _node("table", kind="object", center=(0, 0, 0.4), dims=(1.2, 0.6, 0.75))
    assert not reads_as_wall(table)


def test_a_floor_sheet_does_not_read_as_a_wall():
    floor = _node("floor", kind="floor", center=(0, 0, 0.0), dims=(4.0, 4.0, 0.02))
    assert not reads_as_wall(floor)


def test_upright_walls_uses_the_same_predicate():
    graph = SceneGraph(
        scan_id=uuid.uuid4(),
        revision=0,
        nodes=[
            _node("sheet", kind="object", center=(-1, 0, 1.5), dims=(4.0, 0.04, 3.0)),
            _node("thick", kind="wall", center=(1, 0, 1.5), dims=(4.0, 0.15, 3.0)),
            _node("table", kind="object", center=(0, 2, 0.4), dims=(1.2, 0.6, 0.75)),
        ],
    )
    walls = upright_walls(graph)
    assert {node.label for node in walls} == {"sheet", "thick"}
