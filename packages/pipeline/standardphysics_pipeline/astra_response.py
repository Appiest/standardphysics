"""Turning a labelling reply into label patches, and refusing any reply that is incomplete or cites evidence it was not given."""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

from standardphysics_contracts import DisplayAppearance, DisplayReconstruction, SceneGraph

from .astra_patches import LabelPatch, QualityName


def body_has_images(body: dict[str, Any]) -> bool:
    messages = body.get("messages") or []
    if len(messages) < 2 or not isinstance(messages[1], dict):
        return False
    content = messages[1].get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )


def body_evidence_by_node(body: dict[str, Any]) -> dict[UUID, set[str]]:
    """Only calibrated crops establish photo evidence for display completion."""
    messages = body.get("messages") or []
    if len(messages) < 2 or not isinstance(messages[1], dict):
        return {}
    content = messages[1].get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return {}
    try:
        context = json.loads(content[0].get("text") or "")
    except (TypeError, ValueError):
        return {}
    evidence = context.get("photo_evidence") if isinstance(context, dict) else None
    result: dict[UUID, set[str]] = {}
    if not isinstance(evidence, list):
        return result
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("frame_id"), str):
            continue
        frame_id = item["frame_id"]
        for raw_id in item.get("object_ids") or []:
            try:
                result.setdefault(UUID(str(raw_id)), set()).add(frame_id)
            except (TypeError, ValueError):
                continue
    return result


def patches_from_model(
    payload: dict,
    graph: SceneGraph,
    *,
    allow_appearance: bool = True,
    evidence_by_node: dict[UUID, set[str]] | None = None,
) -> list[LabelPatch] | None:
    content = _message_content(payload)
    if content is None:
        return None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"nodes"}:
        return None
    items = parsed.get("nodes")
    if not isinstance(items, list):
        return None
    expected = {node.id for node in graph.contents()}
    if not expected:
        return None
    patches = [
        _patch_from_item(
            item,
            expected,
            allow_appearance=allow_appearance,
            evidence_frame_ids=(evidence_by_node or {}),
        )
        for item in items
    ]
    if any(patch is None for patch in patches):
        return None
    found = [patch for patch in patches if patch is not None]
    return found if {patch.node_id for patch in found} == expected and len(found) == len(expected) else None


def _message_content(payload: dict) -> str | None:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else None


ITEM_KEYS = {"id", "label", "movable", "quality", "appearance", "reconstruction"}
QUALITIES = {"measured", "needs_another_look"}


def _node_id_of(item: dict, expected: set[UUID]) -> UUID | None:
    try:
        node_id = UUID(str(item.get("id")))
    except (ValueError, TypeError):
        return None
    return node_id if node_id in expected else None


def _labelling_of(item: dict) -> tuple[str, QualityName, bool] | None:
    """The three fields every patch must carry, or None if any is unusable."""
    label, quality, movable = item.get("label"), item.get("quality"), item.get("movable")
    if not isinstance(label, str) or not label.strip():
        return None
    if quality not in QUALITIES or not isinstance(movable, bool):
        return None
    return label, cast(QualityName, quality), movable


def _reconstruction_of(raw: object, allowed: set[str]) -> DisplayReconstruction | None:
    """A reconstruction is kept only when every frame it cites is one we gave it.

    A model that cites a frame it was never shown, or cites one twice, has
    invented its evidence, so the reconstruction is dropped rather than trusted.
    """
    try:
        reconstruction = DisplayReconstruction.model_validate(raw)
    except (TypeError, ValueError):
        return None
    cited = reconstruction.evidence_frame_ids
    if not allowed or len(set(cited)) != len(cited) or not set(cited).issubset(allowed):
        return None
    return reconstruction


def _patch_from_item(
    item: object,
    expected: set[UUID],
    *,
    allow_appearance: bool = True,
    evidence_frame_ids: dict[UUID, set[str]] | None = None,
) -> LabelPatch | None:
    if not isinstance(item, dict) or set(item) - ITEM_KEYS:
        return None
    node_id = _node_id_of(item, expected)
    labelling = _labelling_of(item) if node_id is not None else None
    if node_id is None or labelling is None:
        return None
    label, quality, movable = labelling

    appearance = None
    raw_appearance = item.get("appearance")
    if raw_appearance is not None:
        if not allow_appearance:
            return None
        try:
            appearance = DisplayAppearance.model_validate(raw_appearance)
        except (TypeError, ValueError):
            return None

    raw_reconstruction = item.get("reconstruction")
    reconstruction = None
    if raw_reconstruction is not None:
        reconstruction = _reconstruction_of(raw_reconstruction, (evidence_frame_ids or {}).get(node_id, set()))

    return LabelPatch(node_id, label.strip()[:80], movable, quality, appearance, reconstruction)
