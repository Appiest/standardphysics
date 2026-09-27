"""A whole library floor exports within the time Blender is given.

Every box used to be added through an operator, and each join first deselected
everything in the scene, so a floor's export slowed down as it filled. Moffitt's
704-node floor took 213 seconds on the server against a 300 second limit, and
the floor build failed. The graph here is that floor's.
"""

from __future__ import annotations

import pathlib
import shutil
import time

import pytest
from standardphysics_contracts import SceneGraph
from standardphysics_pipeline import blender
from standardphysics_pipeline.blender import export_glb, glb_node_names

FLOOR = pathlib.Path(__file__).resolve().parents[3] / "datasets" / "replays" / "moffitt-floor" / "graph.json"
BUDGET_SECONDS = 20
"""A laptop took 32 to 60 seconds before the change and 8 to 11 after; the server is about four times slower."""

pytestmark = [
    pytest.mark.skipif(not FLOOR.is_file(), reason="the recorded floor is not in this checkout"),
    pytest.mark.skipif(shutil.which("blender") is None, reason="Blender is not installed"),
]


def test_a_704_node_floor_exports_quickly_and_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(blender, "TIMEOUT_SECONDS", 600)
    graph = SceneGraph.model_validate_json(FLOOR.read_text())
    start = time.monotonic()
    out = export_glb(graph, tmp_path / "floor.glb")
    assert time.monotonic() - start < BUDGET_SECONDS
    assert len(glb_node_names(out)) == len(graph.nodes)
