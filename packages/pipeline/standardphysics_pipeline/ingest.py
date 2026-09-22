"""RoomPlan's `CapturedRoom` JSON to our `SceneGraph`.

Swift's `JSONEncoder` writes enums with associated values as a single-key
object, so a category arrives as `"table"`, `{"table": {}}`, or
`{"door": {"isOpen": false}}` depending on the case. The parser accepts all
three rather than guessing which one a given SDK produces.

**This is written against the documented shape, not a real export.** Until a
real `room.json` lands from Lane A, treat a parse of it as unverified: see
docs/handoffs/B-to-A.md. `parse_room_json` raises rather than inventing a value
whenever a field it needs is missing or shaped unexpectedly.
"""

from __future__ import annotations

import uuid
from typing import Any

from standardphysics_contracts import Mat4, SceneGraph, SceneNode, lies_flat

from .coords import capture_to_room, dimensions_to_z_up, transform_from_arkit

SURFACE_KINDS = {
    "walls": "wall",
    "doors": "door",
    "windows": "window",
    "openings": "opening",
    "floors": "floor",
}

FIXED_CATEGORIES = {
    "refrigerator", "stove", "oven", "dishwasher", "sink", "toilet",
    "bathtub", "washerdryer", "fireplace", "stairs",
}
"""Plumbed in, wired in, or structural. Astra can still correct a label, but
nothing here starts out movable.
"""

CONFIDENCE_TO_QUALITY = {
    "high": "measured",
    "medium": "needs_another_look",
    "low": "needs_another_look",
}


class RoomParseError(ValueError):
    """The export did not look like a CapturedRoom."""


def _enum_name(value: Any, field: str) -> str:
    """Swift enums arrive as a string or as a single-key object."""
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value)).lower()
    raise RoomParseError(f"could not read {field} from {value!r}")


def _vector(value: Any, field: str) -> tuple[float, float, float]:
    if isinstance(value, dict):
        try:
            return float(value["x"]), float(value["y"]), float(value["z"])
        except KeyError as exc:
            raise RoomParseError(f"{field} missing {exc}") from exc
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return float(value[0]), float(value[1]), float(value[2])
    raise RoomParseError(f"could not read {field} from {value!r}")


def _matrix(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) == 16:
        return [float(v) for v in value]
    if isinstance(value, (list, tuple)) and len(value) == 4:
        columns = [c for c in value if isinstance(c, (list, tuple)) and len(c) == 4]
        if len(columns) == 4:
            return [float(component) for column in columns for component in column]
    raise RoomParseError(f"transform was not 16 floats: {value!r}")


def _identifier(element: dict, fallback: str) -> uuid.UUID:
    raw = element.get("identifier")
    if raw is None:
        return uuid.uuid5(uuid.NAMESPACE_OID, fallback)
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise RoomParseError(f"identifier was not a UUID: {raw!r}") from exc


def _quality(element: dict) -> str:
    """A missing confidence is not a high one."""
    raw = element.get("confidence")
    if raw is None:
        return "needs_another_look"
    return CONFIDENCE_TO_QUALITY.get(_enum_name(raw, "confidence"), "needs_another_look")


def _parent(element: dict) -> uuid.UUID | None:
    """Doors, windows and openings carry the wall they were cut into."""
    raw = element.get("parentIdentifier")
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _node(element: dict, kind: str, index: int) -> SceneNode:
    category = _enum_name(element.get("category", kind), "category")
    width, height, depth = _vector(element["dimensions"], "dimensions")
    parent = _parent(element)

    return SceneNode(
        id=_identifier(element, f"{kind}-{index}"),
        kind=kind,
        label=category.replace("_", " ").capitalize(),
        raw_category=category,
        dimensions=dimensions_to_z_up(width, height, depth),
        transform=transform_from_arkit(_matrix(element["transform"])),
        quality=_quality(element),
        movable=_is_movable(kind, category),
        labeled_by="roomplan",
        parent_id=parent,
        relation="cut_into" if parent is not None else None,
    )


def _is_movable(kind: str, category: str) -> bool:
    """Only furniture starts movable, and only when it is not plumbed in.

    Astra may correct this from the imagery. Nothing else may: relabelling can
    never unlock a fixture on its own.
    """
    if kind != "object":
        return False
    return category not in FIXED_CATEGORIES


def parse_room_json(payload: dict, scan_id: uuid.UUID | None = None) -> SceneGraph:
    if not isinstance(payload, dict):
        raise RoomParseError("expected a JSON object")

    nodes: list[SceneNode] = []
    for key, kind in SURFACE_KINDS.items():
        for index, element in enumerate(payload.get(key) or []):
            nodes.append(_node(element, kind, index))
    for index, element in enumerate(payload.get("objects") or []):
        nodes.append(_node(element, "object", index))

    if not nodes:
        raise RoomParseError("no walls, surfaces or objects in the export")

    floor_height = _stand_on_the_floor(nodes)

    return SceneGraph(
        scan_id=scan_id or _identifier(payload, "scan"),
        revision=0,
        nodes=nodes,
        capture_to_room=capture_to_room(floor_height),
    )


def capture_to_room_from_payload(payload: dict) -> Mat4:
    """The ARKit-to-room transform ingest applies, for graphs saved before it was recorded."""
    floors = payload.get("floors") or [] if isinstance(payload, dict) else []
    if not floors:
        return capture_to_room(0.0)
    return capture_to_room(_matrix(floors[0]["transform"])[13])


def missing_coverage(graph: SceneGraph) -> list[SceneNode]:
    """Nodes a rescan would improve, for the ask-the-owner path."""
    return [node for node in graph.nodes if node.quality == "needs_another_look"]


def to_arkit_columns(node: SceneNode) -> list[float]:
    """Our transform back to ARKit's column-major layout, for round-trip tests."""
    m = node.transform.m
    rows = [m[0:4], m[4:8], m[8:12], m[12:16]]
    basis = [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]]
    basis_inv = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]
    step = [[sum(basis[r][k] * rows[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]
    result = [[sum(step[r][k] * basis_inv[k][c] for k in range(4)) for c in range(4)]
              for r in range(4)]
    return [result[r][c] for c in range(4) for r in range(4)]


def _stand_on_the_floor(nodes: list[SceneNode]) -> float:
    """Move the whole room so its floor sits at z = 0.

    RoomPlan puts the origin wherever the phone happened to be when the scan
    started, which is about chest height. On a real capture the floor comes back
    at roughly z = -1.4, so anything comparing a height against an absolute z
    gets both directions wrong: sofas and tables read as open floor, while a
    wall cabinet whose underside is a metre up reads as a floor obstruction.

    Shifting once here means nothing downstream has to know where the phone was
    standing. Heights are heights above the floor everywhere after this.

    The export's floors array is what stands on; a scan declares its floor
    outright, and a thicker slab is still the floor. The sheet heuristic checks
    how thin something lies down, which walls and screens need, not which one
    surface is the ground; it stays as a fallback for payloads that carry no
    floors array at all.
    """
    floor = next((node for node in nodes if node.kind == "floor"), None)
    if floor is None:
        floor = next((node for node in nodes if lies_flat(node)), None)
    if floor is None:
        return 0.0

    drop = floor.transform.position.z
    for node in nodes:
        node.transform.m[11] -= drop
    return drop
