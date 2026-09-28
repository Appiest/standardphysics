"""Where the scan found each piece, recovered for revisions saved before that was recorded.

`SceneNode.measured_position` is stamped on a piece's first move. A piece
moved in a revision saved before the stamp existed has none, so its next move
would measure its travel from where it stands now and an earlier move would
escape the cap. Revisions are an append-only history, so the answer is read
back from that history whenever a revision is loaded to be rearranged, and
nothing stored is rewritten.

Walking the history from the first revision, each step between a revision
and the one it was saved from is one of two kinds:

- A rearrangement, which drags movable pieces and never a fixed one, because
  the constraints refuse that. Where a piece was found stays where it was.
- A room placement, which re-registers a whole room rigidly, walls and floor
  included. Where a piece was found moves with its room. Each piece's own
  turn and shift is exactly its room's, so the found position is carried by
  the piece's own movement, after checking that some fixed surface moved the
  same way in the same step.

A step is not trusted when the revision it was saved from was rewritten
afterwards, which happens when ingest runs again and replaces revision 0.
A piece that moved across an untrusted step, or moved in a placement step
without matching any room, has no recoverable origin and is left without
one rather than given a guess. So is a node whose id another node in the same
revision also carries, since the history cannot say which pose was whose.
"""

from __future__ import annotations

import datetime
import math
from collections import Counter
from dataclasses import dataclass

from standardphysics_contracts import SceneGraph, SceneNode, Vec3

STILL = 1e-6
"""Metres or radians below which a pose change is float noise, not a move."""

SAME_PLACEMENT_METERS = 1e-3
SAME_PLACEMENT_RADIANS = 1e-4
"""How closely a piece's movement must match a fixed surface's to share its room's placement.

A placement moves every node of a room by one turn and one shift, computed
from the same numbers, so members agree to far better than a millimetre.
"""


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float
    yaw: float

    @classmethod
    def of(cls, m: list[float]) -> Pose:
        return cls(x=m[3], y=m[7], z=m[11], yaw=math.atan2(m[4], m[0]))

    def differs_from(self, other: Pose) -> bool:
        shifts = (self.x - other.x, self.y - other.y, self.z - other.z, _wrapped(self.yaw - other.yaw))
        return max(abs(value) for value in shifts) > STILL


@dataclass(frozen=True)
class Placement:
    """A turn about the vertical axis, then a shift, taking one pose to another."""

    turn: float
    shift_x: float
    shift_y: float
    lift: float

    @classmethod
    def between(cls, before: Pose, after: Pose) -> Placement:
        turn = after.yaw - before.yaw
        cos_t, sin_t = math.cos(turn), math.sin(turn)
        return cls(
            turn=turn,
            shift_x=after.x - (cos_t * before.x - sin_t * before.y),
            shift_y=after.y - (sin_t * before.x + cos_t * before.y),
            lift=after.z - before.z,
        )

    def carry(self, point: Vec3) -> Vec3:
        cos_t, sin_t = math.cos(self.turn), math.sin(self.turn)
        return Vec3(
            x=cos_t * point.x - sin_t * point.y + self.shift_x,
            y=sin_t * point.x + cos_t * point.y + self.shift_y,
            z=point.z + self.lift,
        )

    def matches(self, other: Placement) -> bool:
        return (
            abs(_wrapped(self.turn - other.turn)) <= SAME_PLACEMENT_RADIANS
            and abs(self.shift_x - other.shift_x) <= SAME_PLACEMENT_METERS
            and abs(self.shift_y - other.shift_y) <= SAME_PLACEMENT_METERS
        )


def _wrapped(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


@dataclass(frozen=True)
class RevisionPoses:
    """What the walk needs from one stored revision, without parsing the whole graph."""

    revision: int
    saved_at: datetime.datetime
    poses: dict[str, Pose]
    fixed: frozenset[str]
    stamped: dict[str, Vec3]
    shared: frozenset[str]
    """Ids more than one node carries, whose poses cannot be told apart."""

    @classmethod
    def of(cls, revision: int, created_at: str, graph: dict) -> RevisionPoses:
        nodes = graph.get("nodes", [])
        return cls(
            revision=revision,
            saved_at=_instant(created_at),
            poses={node["id"]: Pose.of(node["transform"]["m"]) for node in nodes},
            fixed=frozenset(node["id"] for node in nodes if not node.get("movable", False)),
            stamped={node["id"]: Vec3(**node["measured_position"]) for node in nodes if node.get("measured_position")},
            shared=frozenset(node_id for node_id, count in Counter(node["id"] for node in nodes).items() if count > 1),
        )


def _instant(created_at: str) -> datetime.datetime:
    moment = datetime.datetime.fromisoformat(created_at)
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


Origins = dict[str, Vec3 | None]
"""Where each piece was found, by node id; None where that cannot be recovered."""


def recover_origins(history: list[RevisionPoses]) -> Origins:
    """Where each node of the last revision was found, walking the history in order."""
    if not history:
        return {}
    first = history[0]
    origins: Origins = {
        node_id: None if node_id in first.shared else first.stamped.get(node_id, _position(pose))
        for node_id, pose in first.poses.items()
    }
    for before, after in zip(history, history[1:]):
        origins = _Step(before, after).carry(origins)
    return origins


def _position(pose: Pose) -> Vec3:
    return Vec3(x=pose.x, y=pose.y, z=pose.z)


class _Step:
    def __init__(self, before: RevisionPoses, after: RevisionPoses):
        self.before, self.after = before, after
        self.trusted = before.saved_at <= after.saved_at
        self.moved = {
            node_id
            for node_id, pose in after.poses.items()
            if node_id in before.poses and pose.differs_from(before.poses[node_id])
        }
        self.rooms = [self._placement(node_id) for node_id in self.moved if node_id in after.fixed]

    def _placement(self, node_id: str) -> Placement:
        return Placement.between(self.before.poses[node_id], self.after.poses[node_id])

    def carry(self, origins: Origins) -> Origins:
        return {
            node_id: self.after.stamped[node_id]
            if node_id in self.after.stamped
            else self._origin(node_id, origins.get(node_id))
            for node_id in self.after.poses
        }

    def _origin(self, node_id: str, was: Vec3 | None) -> Vec3 | None:
        if node_id in self.after.shared or node_id in self.before.shared:
            return None
        if node_id not in self.before.poses:
            return _position(self.after.poses[node_id]) if self.trusted else None
        if node_id not in self.moved:
            return was
        if not self.trusted or was is None:
            return None
        return self._moved_origin(node_id, was)

    def _moved_origin(self, node_id: str, was: Vec3) -> Vec3 | None:
        if not self.rooms:
            return was
        placement = self._placement(node_id)
        if node_id in self.after.fixed or any(placement.matches(room) for room in self.rooms):
            return placement.carry(was)
        return None


def with_origins(graph: SceneGraph, origins: Origins) -> tuple[SceneGraph, int]:
    """The graph with a recovered origin on every moved piece that lacks one, and how many could not be recovered.

    A piece whose origin is where it stands needs no record, because a missing
    record already means that.
    """
    nodes, unrecovered = [], 0
    for node in graph.nodes:
        key = str(node.id)
        if node.measured_position is not None or key not in origins:
            nodes.append(node)
            continue
        origin = origins[key]
        unrecovered += origin is None
        nodes.append(_recorded(node, origin))
    return graph.model_copy(update={"nodes": nodes}), unrecovered


def _recorded(node: SceneNode, origin: Vec3 | None) -> SceneNode:
    if origin is None:
        return node
    now = node.transform.position
    if max(abs(now.x - origin.x), abs(now.y - origin.y), abs(now.z - origin.z)) <= STILL:
        return node
    return node.model_copy(update={"measured_position": origin})
