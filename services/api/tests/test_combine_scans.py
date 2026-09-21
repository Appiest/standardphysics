from __future__ import annotations

import copy
import pathlib
import sys
import uuid

import pytest
from standardphysics_pipeline import parse_room_json

SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from combine_scans import overlay  # noqa: E402


def _transform(y: float) -> list[float]:
    """An identity ARKit transform with translation in its column-major Y slot."""
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, y, 0.0, 1.0]


def _payload(floor_id: str, object_id: str, floor_y: float, object_height: float) -> dict:
    return {
        "version": 1,
        "story": "ground",
        "captureMetadata": {"source": floor_id},
        "floors": [
            {
                "identifier": floor_id,
                "dimensions": [4.0, 0.1, 4.0],
                "transform": _transform(floor_y),
                "confidence": "high",
                "floorMetadata": "preserve me",
            }
        ],
        "objects": [
            {
                "identifier": object_id,
                "category": "chair",
                "dimensions": [1.0, 1.0, 1.0],
                "transform": _transform(floor_y + object_height),
                "confidence": "medium",
                "objectMetadata": {"label": "keep me"},
            }
        ],
    }


def test_overlay_aligns_capture_floors_without_mutating_payloads() -> None:
    first = _payload(str(uuid.uuid4()), str(uuid.uuid4()), -1.0, 0.75)
    second = _payload(str(uuid.uuid4()), str(uuid.uuid4()), -1.2, 0.75)
    first_before = copy.deepcopy(first)
    second_before = copy.deepcopy(second)

    merged = overlay([first, second])

    assert first == first_before
    assert second == second_before
    assert merged["captureMetadata"] == first["captureMetadata"]
    assert merged["floors"][1]["floorMetadata"] == "preserve me"
    assert merged["objects"][1]["objectMetadata"] == {"label": "keep me"}
    assert merged["floors"][1]["transform"][13] == pytest.approx(-1.0)
    assert merged["objects"][1]["transform"][13] == pytest.approx(-0.25)

    graph = parse_room_json(merged)
    floors = [node for node in graph.nodes if node.kind == "floor"]
    objects = [node for node in graph.nodes if node.kind == "object"]
    assert [node.transform.position.z for node in floors] == pytest.approx([0.0, 0.0])
    assert [node.transform.position.z for node in objects] == pytest.approx([0.75, 0.75])
