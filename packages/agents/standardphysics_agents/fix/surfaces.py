"""Guesses for an item that sits too high: set it down on a lower surface nearby.

The slide ladder and the placement beam open floor space, so a card reader on
a 42 inch counter gives them nothing to work with: there is no gap, and the
shortfall runs the other way. What a person would do is carry the reader to
the lowered counter section. A move already settles an item onto whatever is
under where it lands (`moves.settle`), so sliding the reader over a lower
surface lowers it. This file proposes those slides, spread over each surface's
top so at least one lands on free space; the hard constraints and the checker
decide which of them are real.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, bounds_the_room, to_meters
from standardphysics_pipeline.footprints import rotation_about_z

from .moves import floor_height, rests_on_something, top_of
from .strategies import Candidate

GRID_ALONG = 5
GRID_ACROSS = 3
SURFACE_TOLERANCE = 0.005
"""A surface a few millimetres over the limit by float arithmetic still counts as meeting it."""


@dataclass(frozen=True)
class SurfaceMove:
    candidate: Candidate
    item: SceneNode
    surface: SceneNode


def _too_high(finding: Finding) -> bool:
    measured, required = finding.measured_inches, finding.required_inches
    return measured is not None and required is not None and measured > required and finding.locus is not None


def _spots(surface: SceneNode, item: SceneNode) -> list[tuple[float, float]]:
    """Points spread over the surface's top, inset so the item stays wholly on it."""
    cos_t, sin_t = rotation_about_z(surface)
    centre = surface.transform.position
    reach = max(item.dimensions.x, item.dimensions.y) / 2
    half_u, half_v = max(surface.dimensions.x / 2 - reach, 0.0), max(surface.dimensions.y / 2 - reach, 0.0)
    spots = []
    for i in range(GRID_ALONG):
        u = -half_u + 2 * half_u * i / (GRID_ALONG - 1)
        for j in range(GRID_ACROSS):
            v = -half_v + 2 * half_v * j / (GRID_ACROSS - 1)
            spots.append((centre.x + u * cos_t - v * sin_t, centre.y + u * sin_t + v * cos_t))
    return spots


def _surfaces(graph: SceneGraph, item: SceneNode, ceiling: float, floor_z: float) -> list[SceneNode]:
    return [node for node in graph.nodes
            if node.id != item.id and not bounds_the_room(node) and not rests_on_something(node, floor_z)
            and floor_z < top_of(node) <= ceiling + SURFACE_TOLERANCE]


def lower_surface_moves(graph: SceneGraph, finding: Finding) -> list[SurfaceMove]:
    """Slides that set each too-high movable item the finding names down on a low enough surface, nearest first."""
    if not _too_high(finding):
        return []
    assert finding.locus is not None and finding.required_inches is not None
    floor_z = floor_height(graph)
    ceiling = floor_z + to_meters(finding.required_inches)
    nodes = {node.id: node for node in graph.nodes}
    items = [nodes[node_id] for node_id in finding.locus.node_ids
             if node_id in nodes and nodes[node_id].movable and rests_on_something(nodes[node_id], floor_z)]
    found = []
    for item in items:
        here = item.transform.position
        for surface in _surfaces(graph, item, ceiling, floor_z):
            for x, y in _spots(surface, item):
                move = NodeMove(node_id=item.id, delta_translation=Vec3(x=x - here.x, y=y - here.y, z=0.0))
                candidate = Candidate("lower_surface", [move], math.hypot(x - here.x, y - here.y))
                found.append(SurfaceMove(candidate, item, surface))
    return sorted(found, key=lambda found_move: found_move.candidate.disruption)
