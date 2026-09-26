"""Walls and room shell, read two ways.

Real scans produce the room shell as thin sheets: `bounds_the_room` reads a
wall, floor, door or window from its measured extents alone. Tests and coarse
reconstructions also carry the scanner's explicit label on `kind`, and a region
labelled "wall" is still a wall when it is too thick to read as a sheet. The
label is the fallback, not the first answer.
"""

from __future__ import annotations

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, is_fixed_to_a_surface, lies_flat
from standardphysics_pipeline.occupancy import reads_as_wall

SHELL_KINDS = frozenset({"wall", "floor", "ceiling", "door", "window", "opening"})


def upright_walls(graph: SceneGraph) -> list[SceneNode]:
    """The room's standing walls: sheets that stand up, or labelled walls."""
    return [node for node in graph.nodes if reads_as_wall(node)]


def standing_walls(graph: SceneGraph) -> list[SceneNode]:
    """The walls themselves, without what is set into or fixed onto them.

    `upright_walls` also answers for a door, a window or a whiteboard measured
    as a standing sheet. A doorway cut into a wall records that wall as its
    parent and a board records the face it hangs on, so leaving both out keeps
    only the sheets the room stands on.
    """
    return [node for node in graph.nodes if _is_standing_wall(node)]


def _is_standing_wall(node: SceneNode) -> bool:
    return (reads_as_wall(node) and not lies_flat(node)
            and node.parent_id is None and not is_fixed_to_a_surface(node))


def is_room_shell(node: SceneNode) -> bool:
    """Whether the node makes the room rather than standing in it.

    A wall the scanner measured as a sheet answers one way and a labelled wall
    too thick to read as a sheet answers the other; both are the room either way.
    """
    return bounds_the_room(node) or node.kind in SHELL_KINDS
