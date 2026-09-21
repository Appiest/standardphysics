"""The gap a fix is trying to open, read off a finding.

A finding already carries everything needed: the two things forming the gap,
the line drawn across it, and how far short the measurement fell. Re-deriving
any of that from geometry would be a second opinion on a question Lane B has
already answered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import Finding, SceneGraph, SceneNode, Vec3, bounds_the_room, to_meters


@dataclass(frozen=True)
class Pinch:
    finding_id: UUID
    movable: list[SceneNode]
    fixed: list[SceneNode]
    centre: Vec3
    across: tuple[float, float]
    """Unit vector along the measurement, from one side of the gap to the other."""

    along: tuple[float, float]
    """Unit vector at right angles to it, roughly the direction of travel."""

    deficit_meters: float
    sealed: bool = False
    """Whether there is no gap here at all, rather than one that is too narrow.

    It changes which rearrangements are worth trying first: a gap of zero
    cannot be widened, because the two things forming it are already touching
    and there is nothing between them to open.
    """

    @property
    def fixable(self) -> bool:
        return bool(self.movable) and self.deficit_meters > 0


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    return (1.0, 0.0) if length < 1e-9 else (dx / length, dy / length)


def _nodes(graph: SceneGraph, node_ids) -> list[SceneNode]:
    found = []
    for node_id in node_ids:
        try:
            found.append(graph.by_id(node_id))
        except KeyError:
            continue
    return found


def _measurement_line(finding: Finding) -> tuple[Vec3, Vec3] | None:
    locus = finding.locus
    if locus is None or locus.annotation.kind != "dimension_line":
        return None
    points = locus.annotation.points
    return (points[0], points[1]) if len(points) >= 2 else None


def _axes_from_nodes(nodes: list[SceneNode]) -> tuple[Vec3, tuple[float, float]]:
    first, second = nodes[0].transform.position, nodes[-1].transform.position
    centre = Vec3(x=(first.x + second.x) / 2, y=(first.y + second.y) / 2, z=0.0)
    return centre, _unit(second.x - first.x, second.y - first.y)


def _unlockable(node: SceneNode) -> bool:
    """Something fixed that an owner could still say yes to moving.

    A built-in counter or a shelf might turn out to be movable after all. A
    wall is not a lock anybody can release, so offering to unlock one would be
    offering nothing.
    """
    return not node.movable and not bounds_the_room(node)


def pinch_from(finding: Finding, graph: SceneGraph) -> Pinch | None:
    """What the fix agent has to work with, or None if there is nothing to go on.

    A sealed route has no width to report, so its measurement is empty. That is
    a gap of zero rather than an unknown: the shortfall is the whole of what the
    section asks for. Treating it as unknown would leave the one case the owner
    most wants solved with nothing tried on it.
    """
    if finding.locus is None or finding.required_inches is None:
        return None

    nodes = _nodes(graph, finding.locus.node_ids)
    if not nodes:
        return None

    line = _measurement_line(finding)
    if finding.locus.annotation.kind == "region":
        centre, across = finding.locus.point, (1.0, 0.0)
    elif line is None:
        centre, across = _axes_from_nodes(nodes)
    else:
        start, end = line
        centre = Vec3(x=(start.x + end.x) / 2, y=(start.y + end.y) / 2, z=0.0)
        across = _unit(end.x - start.x, end.y - start.y)

    return Pinch(
        finding_id=finding.id,
        movable=[node for node in nodes if node.movable],
        fixed=[node for node in nodes if _unlockable(node)],
        centre=centre,
        across=across,
        along=(-across[1], across[0]),
        deficit_meters=to_meters(
            finding.required_inches - (finding.measured_inches or 0.0)
        ),
        sealed=finding.measured_inches is None,
    )
