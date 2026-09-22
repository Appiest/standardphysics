"""ADA 2010 307. Something sticking out at head height.

The section is about what you walk into rather than what you walk around: an
object whose leading edge sits between 27 and 80 inches up may reach 4 inches
into a circulation path, because a cane sweeping the floor never finds it.

Only mounted objects are candidates. Anything standing on the floor is caught
by a cane and measured by the route checks instead, so this looks at what is up
on a wall and how far out it comes.

A pass here is worth less than a pass elsewhere, and the plan says so: LiDAR
misses thin wall-mounted items. What that means in practice is that this check
reports what it found and the report's own account of what was checked carries
the rest. It does not report on a wall with nothing mounted on it, because
there is nothing there to report.
"""

from __future__ import annotations

from standardphysics_contracts import SceneGraph, SceneNode, Vec3, bounds_the_room, lies_flat, to_inches
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import rotation_about_z

from ..rules import RuleSpec
from ..tracing import traced
from .context import CheckContext
from .observation import Observation
from .walls import upright_walls

RULE_ID = "protruding_objects"

MOUNTED_ABOVE_INCHES = 4.0
"""An object whose underside is this far up is mounted rather than standing.

Four inches clears a plinth and a set of castors without reaching a shelf.
"""


def leading_edge_inches(node: SceneNode) -> float:
    """The bottom edge, which is the one a cane would have to find."""
    return to_inches(node.transform.position.z - node.dimensions.z / 2)


def is_mounted(node: SceneNode) -> bool:
    return not bounds_the_room(node) and leading_edge_inches(node) > MOUNTED_ABOVE_INCHES


def in_the_hazard_band(node: SceneNode, rule: RuleSpec) -> bool:
    edge = leading_edge_inches(node)
    return (
        rule.parameter("leading_edge_low_inches")
        < edge
        <= rule.parameter("leading_edge_high_inches")
    )


def _room_centre(graph: SceneGraph) -> Vec3:
    floors = [node for node in graph.nodes if lies_flat(node)]
    return floors[0].transform.position if floors else Vec3(x=0.0, y=0.0, z=0.0)


def _wall_normal(wall: SceneNode, room_centre: Vec3) -> tuple[float, float]:
    """The way a wall faces into the room."""
    cos_t, sin_t = rotation_about_z(wall)
    long_along_x = wall.dimensions.x >= wall.dimensions.y
    normal = (-sin_t, cos_t) if long_along_x else (cos_t, sin_t)
    centre = wall.transform.position
    toward_room = (room_centre.x - centre.x, room_centre.y - centre.y)
    if normal[0] * toward_room[0] + normal[1] * toward_room[1] < 0:
        return (-normal[0], -normal[1])
    return normal


def _along(point: tuple[float, float], axis: tuple[float, float]) -> float:
    return point[0] * axis[0] + point[1] * axis[1]


def _wall_face(wall: SceneNode, normal: tuple[float, float]) -> float:
    """Where the wall's inner surface sits along its own normal."""
    return max(_along(corner, normal) for corner in footprint(wall))


def projection_inches(node: SceneNode, wall: SceneNode, room_centre: Vec3) -> float:
    """How far the node reaches out from the wall it is mounted on."""
    normal = _wall_normal(wall, room_centre)
    far = max(_along(corner, normal) for corner in footprint(node))
    return to_inches(far - _wall_face(wall, normal))


def _host_wall(node: SceneNode, graph: SceneGraph) -> SceneNode | None:
    """The wall a mounted object belongs to, by its declared parent or by
    whichever one it reaches out of least."""
    walls = upright_walls(graph)
    if not walls:
        return None
    if node.parent_id is not None:
        parents = [wall for wall in walls if wall.id == node.parent_id]
        if parents:
            return parents[0]
    centre = _room_centre(graph)
    return min(walls, key=lambda wall: projection_inches(node, wall, centre))


def _limit(node: SceneNode, graph: SceneGraph, rule: RuleSpec) -> float:
    """4 inches off a wall, 12 inches off a post."""
    wall = _host_wall(node, graph)
    if wall is None:
        return rule.parameter("post_mounted_max_projection_inches")
    return rule.parameter("wall_mounted_max_projection_inches")


@traced("checks.protruding_objects")
def protruding_objects(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    mounted = [
        node
        for node in ctx.graph.nodes
        if is_mounted(node) and in_the_hazard_band(node, rule)
    ]
    return [_observation(ctx, rule, node) for node in mounted]


def _observation(ctx: CheckContext, rule: RuleSpec, node: SceneNode) -> Observation:
    from .vertical import mounted_locus

    wall = _host_wall(node, ctx.graph)
    projection = (
        projection_inches(node, wall, _room_centre(ctx.graph)) if wall else 0.0
    )
    limit = _limit(node, ctx.graph, rule)
    return Observation(
        rule_id=RULE_ID,
        satisfied=rule.satisfied_by(projection),
        measured_inches=projection,
        required_inches=limit,
        relied_on=(node.id,),
        locus=mounted_locus(node),
        facts={
            "object": node.label,
            "leading_edge_inches": leading_edge_inches(node),
            "mounted_on": wall.label if wall else None,
        },
        dedupe_key=(RULE_ID, str(node.id)),
        reason="measured" if projection <= limit else "sticks_out",
    )
