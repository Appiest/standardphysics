"""How the menu words a guess: as a relation to the problem, with each piece named by its label and id tag."""

from __future__ import annotations

import math
from dataclasses import dataclass

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, to_inches, to_meters

from ..redesign import FurnitureMove
from .edits import TrainingEdits

TURN_WORDING_DEGREES = 1.0


@dataclass(frozen=True)
class _Guess:
    edits: TrainingEdits
    wording: str
    restores_scan: bool = False
    """Every piece it moves goes back where the scan found it."""


def _name(node: SceneNode) -> str:
    return f"{node.label} [{str(node.id)[:4]}]"


def _anchor(graph: SceneGraph, node: SceneNode, finding: Finding) -> tuple[str, tuple[float, float]]:
    """What a move is described relative to: the nearest other piece the problem names, or the problem's spot."""
    here = (node.transform.position.x, node.transform.position.y)
    nodes = {other.id: other for other in graph.nodes}
    others = [nodes[node_id] for node_id in (finding.locus.node_ids if finding.locus else [])
              if node_id != node.id and node_id in nodes]
    if others:
        other = min(others, key=lambda o: math.dist(here, (o.transform.position.x, o.transform.position.y)))
        return f"the {other.label}", (other.transform.position.x, other.transform.position.y)
    point = finding.locus.point if finding.locus and finding.locus.point else node.transform.position
    return "the problem spot", (point.x, point.y)


def _turn_words(degrees: float) -> str:
    return f"{abs(degrees):.0f} degrees {'counter-clockwise' if degrees > 0 else 'clockwise'}"


def _slide_words(graph, node, finding, dx: float, dy: float, verb: str) -> str:
    anchor, spot = _anchor(graph, node, finding)
    here = (node.transform.position.x, node.transform.position.y)
    away = math.dist((here[0] + dx, here[1] + dy), spot) >= math.dist(here, spot)
    inches = to_inches(math.hypot(dx, dy))
    return f"{verb} {_name(node)} {inches:.0f} in {'further from' if away else 'closer to'} {anchor}"


def _move_words(graph: SceneGraph, move: NodeMove, finding: Finding) -> str:
    node = graph.by_id(move.node_id)
    delta, degrees = move.delta_translation, move.delta_rotation_z_degrees
    turned = abs(degrees) >= TURN_WORDING_DEGREES
    if math.hypot(delta.x, delta.y) < to_meters(0.5):
        return f"turn {_name(node)} {_turn_words(degrees)}"
    words = _slide_words(graph, node, finding, delta.x, delta.y, "slide")
    return f"{words} and turn it {_turn_words(degrees)}" if turned else words


def _furniture(move: NodeMove) -> FurnitureMove:
    return FurnitureMove(node_id=move.node_id, dx=move.delta_translation.x, dy=move.delta_translation.y,
                         rotation_degrees=move.delta_rotation_z_degrees)
