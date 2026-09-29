"""The labelling request: its instruction, the schema the answer must fit, and the scene and photos it carries."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Iterable

from standardphysics_contracts import SceneGraph, SceneNode, stands_upright

from .astra_photo_evidence import calibrated_photo_evidence, frame_image_messages, photo_evidence_context
from .astra_transport import DEFAULT_MODEL, MODEL_ENV, base_url, endpoint_host, request_options
from .mesh_evidence import object_mesh_profiles

MAX_OUTPUT_TOKENS = 8_192


INSTRUCTION = (
    "Label every supplied object in this shop scan with an ordinary name such as "
    "Ordering counter, Display case, Table, or Chair. Counters, plumbed-in fixtures, "
    "and seating fixed to a floor or wall, such as booths, banquettes, built-in benches, "
    "pews and bolted seat rows, are not movable. raw_category and label are the phone's "
    "coarse guess and are often wrong: name what the photos show, such as Ordering kiosk, "
    "Card reader or Payment terminal, even when the guess says Chair. "
    "Do not change sizes. You may add a display-only appearance "
    "with a six-digit base color and broad material when the frame evidence is "
    "clear; when images_provided is false, appearance must be null. For each "
    "object, return reconstruction only when its calibrated photo crop evidence "
    "shows a recognizable assembly. Reconstruction parts are display-only, use "
    "only box/cylinder/ellipsoid primitives inside the normalized measured bounds, "
    "and must cite only frame ids associated with that object. Return every object "
    "exactly once. Parts use object-local XYZ = width, depth, height with Z up "
    "and local front at -Y; "
    "each axis must obey abs(center[i]) + size[i]/2 <= 0.5. Cylinder axis is its "
    "long direction, bevel is a fraction of the smallest part size. Complete "
    "missing legs and supports only as conservative plausible display inference; "
    "keep people, cables, and other clutter out. photo_evidence.camera_local is "
    "the crop camera in that object's normalized local XYZ: use it to orient the "
    "visible surfaces, rather than assuming a photo always faces local front. "
    "Prefer 6 to 12 meaningful parts."
)
_PART_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "primitive", "center", "size", "axis", "bevel", "base_color", "material"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 60},
        "primitive": {"type": "string", "enum": ["box", "cylinder", "ellipsoid"]},
        "center": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": -0.5, "maximum": 0.5}},
        "size": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "exclusiveMinimum": 0, "maximum": 1}},
        "axis": {"type": "string", "enum": ["x", "y", "z"]},
        "bevel": {"type": "number", "minimum": 0, "maximum": 0.2},
        "base_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "material": {"type": "string", "enum": ["paint", "wood", "fabric", "metal", "stone", "glass", "neutral"]},
    },
}
_RECONSTRUCTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "confidence", "evidence_frame_ids", "parts"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 300},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_frame_ids": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string"}},
        "parts": {"type": "array", "minItems": 1, "maxItems": 32, "items": _PART_SCHEMA},
    },
}
LABEL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["nodes"],
    "properties": {"nodes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "label", "movable", "quality", "appearance", "reconstruction"],
        "properties": {
            "id": {"type": "string"}, "label": {"type": "string"},
            "movable": {"type": "boolean"},
            "quality": {"type": "string", "enum": ["measured", "needs_another_look"]},
            "appearance": {"anyOf": [
                {"type": "null"},
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["base_color", "material"],
                    "properties": {
                        "base_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                        "material": {"type": "string", "enum": ["paint", "wood", "fabric", "metal", "stone", "glass", "neutral"]},
                    },
                },
            ]},
            "reconstruction": {"anyOf": [{"type": "null"}, _RECONSTRUCTION_SCHEMA]},
        },
    }}},
}


SCHEMA_LIMITS = frozenset({"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"})
MAKERS_WITHOUT_SCHEMA_LIMITS = frozenset({"google"})
"""Google rejects a response schema carrying numeric or length limits (HTTP 400, invalid argument). The limits are
dropped for those makers only; every answer is re-checked against them when it is parsed regardless."""


def _without_limits(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _without_limits(value) for key, value in node.items() if key not in SCHEMA_LIMITS}
    if isinstance(node, list):
        return [_without_limits(value) for value in node]
    return node


def schema_for(model: str) -> dict[str, Any]:
    return _without_limits(LABEL_SCHEMA) if model.split("/")[0] in MAKERS_WITHOUT_SCHEMA_LIMITS else LABEL_SCHEMA


def chat_body(
    graph: SceneGraph,
    *,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
    mesh_profiles: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    calibrated = calibrated_photo_evidence(graph, frame_paths, poses_path)
    image_messages = [item.message for item in calibrated] or frame_image_messages(graph, frame_paths, poses_path)
    content: str | list[dict[str, Any]] = json.dumps(
        _context(
            graph,
            images_provided=bool(image_messages),
            photo_evidence=photo_evidence_context(calibrated),
            mesh_profiles=mesh_profiles if mesh_profiles is not None else object_mesh_profiles(graph, lidar_mesh_path),
        )
    )
    if image_messages:
        content = [{"type": "text", "text": content}, *image_messages]
    chosen_model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    body = {
        "model": chosen_model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "astra_labels", "strict": True,
                                                                   "schema": schema_for(chosen_model)}},
    }
    body.update(request_options(chosen_model, endpoint_host(base_url())))
    return body


def _context(
    graph: SceneGraph,
    *,
    images_provided: bool = False,
    photo_evidence: list[dict[str, Any]] | None = None,
    mesh_profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "walls": [_surface_brief(node) for node in graph.nodes if stands_upright(node)],
        "objects": [
            _object_brief(node, (mesh_profiles or {}).get(str(node.id)))
            for node in graph.contents()
        ],
        "doors": [_surface_brief(node) for node in graph.nodes if node.kind == "door"],
        "images_provided": images_provided,
        "photo_evidence": photo_evidence or [],
    }


def _surface_brief(node: SceneNode) -> dict[str, Any]:
    position = node.transform.position
    return {"id": str(node.id), "kind": node.kind, "label": node.label, "x": round(position.x, 3), "y": round(position.y, 3), "length": round(max(node.dimensions.x, node.dimensions.y), 3)}


def _object_brief(node: SceneNode, mesh_profile: Any = None) -> dict[str, Any]:
    position = node.transform.position
    brief = {
        "id": str(node.id), "raw_category": node.raw_category, "label": node.label,
        "width": round(node.dimensions.x, 3), "depth": round(node.dimensions.y, 3), "height": round(node.dimensions.z, 3),
        "x": round(position.x, 3), "y": round(position.y, 3), "movable": node.movable,
    }
    if mesh_profile is not None:
        brief["mesh_profile"] = mesh_profile
    return brief
