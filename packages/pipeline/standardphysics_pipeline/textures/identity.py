"""Which photos-and-shapes a texture build is for, and which shown nodes it still fits.

Photos are projected once, onto every node at the placement it had when the
room was captured. Moving furniture afterwards reuses those textures through
the node's own transform. A node whose generated shape changes no longer fits
them, and a node that did not exist at capture was never photographed.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from standardphysics_contracts import SceneGraph, SceneNode, stands_upright

TEXTURE_PIPELINE_VERSION = "7"
PRECISION = 6
PORTAL_KINDS = frozenset({"door", "window", "opening"})
PLACEMENT_BOUND_KINDS = frozenset({"wall", "floor", "door", "window", "opening"})
"""Kinds whose generated shape depends on where they sit, so their placement is part of their shape."""


def bake_graph_for(requested: SceneGraph, capture: SceneGraph) -> SceneGraph:
    """The requested layout's nodes, each returned to its captured placement."""
    captured = {node.id: node for node in capture.nodes}
    nodes = [
        node.model_copy(update={"transform": captured[node.id].transform})
        for node in requested.nodes
        if node.id in captured
    ]
    return requested.model_copy(update={
        "nodes": nodes,
        "capture_to_room": requested.capture_to_room or capture.capture_to_room,
    })


def texture_build_key(bake_graph: SceneGraph, manifest_sha256: str) -> str:
    payload = {
        "version": TEXTURE_PIPELINE_VERSION,
        "manifest": manifest_sha256,
        "capture_to_room": _rounded(bake_graph.capture_to_room.m) if bake_graph.capture_to_room else None,
        "nodes": sorted(_placed_fingerprint(node) for node in bake_graph.nodes),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stale_node_ids(shown: SceneGraph, bake_graph: SceneGraph) -> list[UUID]:
    """Shown nodes that the build cannot dress: new since capture, or a different generated shape."""
    baked = {node.id: node for node in bake_graph.nodes}
    shown_portals, baked_portals = _portal_fingerprints(shown), _portal_fingerprints(bake_graph)
    return [
        node.id
        for node in shown.nodes
        if node.id not in baked
        or _shape_fingerprint(node, shown_portals) != _shape_fingerprint(baked[node.id], baked_portals)
    ]


def _shape_fingerprint(node: SceneNode, portals: list) -> str:
    parts: list = [node.kind, node.raw_category, _rounded(node.dimensions.as_tuple()), _visual_parts(node)]
    if node.kind in PLACEMENT_BOUND_KINDS:
        parts.append(_rounded(node.transform.m))
    if stands_upright(node):
        parts.append(portals)
    return json.dumps(parts, separators=(",", ":"))


def _portal_fingerprints(graph: SceneGraph) -> list:
    return sorted(_placed_fingerprint(node) for node in graph.nodes if node.kind in PORTAL_KINDS)


def _placed_fingerprint(node: SceneNode) -> str:
    parts = [str(node.id), node.kind, node.raw_category, _rounded(node.dimensions.as_tuple()), _rounded(node.transform.m), _visual_parts(node)]
    return json.dumps(parts, separators=(",", ":"))


def _visual_parts(node: SceneNode) -> dict:
    """Appearance belongs in the display cache, never the measurement hash."""
    return {
        "appearance": node.appearance.model_dump(mode="json") if node.appearance else None,
        "parts": [part.model_dump(mode="json") for part in node.reconstruction.parts] if node.reconstruction else None,
    }


def _rounded(values) -> list[float]:
    return [round(float(value), PRECISION) for value in values]
