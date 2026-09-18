"""The layout fingerprint, and the inventory that must survive a rearrangement.

`graph_hash` lives in `packages/contracts` now, because an assessment and a
proposal both carry one and they have to agree. It is re-exported here so the
import site inside this lane did not have to move.
"""

from __future__ import annotations

from standardphysics_contracts import SceneGraph, bounds_the_room, graph_hash

__all__ = ["graph_hash", "inventory"]


def inventory(graph: SceneGraph) -> dict[str, int]:
    """How many of each thing the owner has.

    Placement is adjustable and this is not. A proposal that changes any of
    these counts has thrown away a chair, whatever else it achieved.
    """
    counts: dict[str, int] = {}
    for node in graph.nodes:
        if bounds_the_room(node):
            continue
        counts[node.label] = counts.get(node.label, 0) + 1
    return counts
