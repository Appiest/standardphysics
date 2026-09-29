"""Bounded Astra reconstruction for a measured RoomPlan graph.

The model may suggest labels and movability. It never changes geometry, node
identity, confirmation quality, or a lock already set by RoomPlan or an owner.
"""

from __future__ import annotations

import logging
import os
import pathlib
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from standardphysics_contracts import SceneGraph, SceneNode

from .astra_frames import MAX_IMAGE_COUNT
from .astra_patches import LabelPatch, ReconstructionSource, apply_patches, local_patches
from .astra_prompt import chat_body
from .astra_response import body_evidence_by_node, body_has_images, patches_from_model
from .astra_transport import (
    Transport,
    api_key_env,
    chat_headers,
    chat_url,
    label_model,
    openrouter_post,
)
from .mesh_evidence import object_mesh_profiles

logger = logging.getLogger(__name__)


MAX_RECONSTRUCTION_WORKERS = 3
RECONSTRUCTION_WALL_TIMEOUT_SECONDS = 150.0


# Each object can contribute two complementary crops; preserve the six-image
# request limit by asking about at most three objects per model call.
RECONSTRUCTION_BATCH_SIZE = MAX_IMAGE_COUNT // 2


@dataclass(frozen=True)
class ReconstructionResult:
    """The graph plus the label source used for this reconstruction."""

    graph: SceneGraph
    source: ReconstructionSource

    @property
    def used_model(self) -> bool:
        return self.source == "astra"


def reconstruct(
    graph: SceneGraph,
    *,
    transport: Transport | None = None,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> SceneGraph:
    """Return the safe reconstructed graph for pipeline callers."""
    return reconstruct_result(
        graph, transport=transport, frame_paths=frame_paths, poses_path=poses_path,
        lidar_mesh_path=lidar_mesh_path,
    ).graph


def reconstruct_result(
    graph: SceneGraph,
    *,
    transport: Transport | None = None,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> ReconstructionResult:
    """Expose whether labels came from Astra or the deterministic local fallback."""
    remote = _remote_patches(
        graph, transport, frame_paths=frame_paths, poses_path=poses_path,
        lidar_mesh_path=lidar_mesh_path,
    )
    if remote is None:
        return ReconstructionResult(apply_patches(graph, local_patches(graph)), "roomplan")
    local = apply_patches(graph, local_patches(graph))
    return ReconstructionResult(apply_patches(local, remote, source="astra"), "astra")


def propose_patches(
    graph: SceneGraph,
    *,
    transport: Transport | None = None,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> list[LabelPatch]:
    """Return remote patches only when the complete model response is valid."""
    return _remote_patches(
        graph, transport, frame_paths=frame_paths, poses_path=poses_path,
        lidar_mesh_path=lidar_mesh_path,
    ) or local_patches(graph)


def _remote_patches(
    graph: SceneGraph,
    transport: Transport | None,
    *,
    frame_paths: Iterable[pathlib.Path] | None = None,
    poses_path: pathlib.Path | None = None,
    lidar_mesh_path: pathlib.Path | None = None,
) -> list[LabelPatch] | None:
    api_key = os.environ.get(api_key_env())
    if transport is None and not api_key:
        return None
    objects = graph.contents()
    if not objects:
        return None
    paths = tuple(frame_paths or ())
    mesh_profiles = object_mesh_profiles(graph, lidar_mesh_path)
    deadline = time.monotonic() + RECONSTRUCTION_WALL_TIMEOUT_SECONDS
    return _attempt_with_model(graph, objects, label_model(), transport, api_key or "", paths, poses_path,
                               mesh_profiles, deadline)


def _attempt_with_model(
    graph: SceneGraph,
    objects: list[SceneNode],
    model: str,
    transport: Transport | None,
    api_key: str,
    frame_paths: tuple[pathlib.Path, ...],
    poses_path: pathlib.Path | None,
    mesh_profiles: dict[str, Any],
    deadline: float,
) -> list[LabelPatch] | None:
    """Every object answered by this one model, or None when a batch is missing, invalid, or times out."""
    batches = [objects[index:index + RECONSTRUCTION_BATCH_SIZE] for index in range(0, len(objects), RECONSTRUCTION_BATCH_SIZE)]
    executor: ThreadPoolExecutor | None = None
    try:
        executor = ThreadPoolExecutor(max_workers=MAX_RECONSTRUCTION_WORKERS)
        futures = [executor.submit(
            _remote_batch, graph, batch, transport, api_key, model, frame_paths, poses_path, mesh_profiles, deadline
        ) for batch in batches]
        patches: list[LabelPatch] = []
        for future in as_completed(futures, timeout=max(0.0, deadline - time.monotonic())):
            result = future.result()
            if result is None:
                logger.warning("astra_remote_batch_invalid reason=invalid_response model=%s", model)
                return None
            patches.extend(result)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, ValueError) as error:
        logger.warning("astra_remote_batch_failed reason=%s model=%s", type(error).__name__, model)
        return None
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
    return patches if {patch.node_id for patch in patches} == {node.id for node in objects} and len(patches) == len(objects) else None


def _remote_batch(
    graph: SceneGraph,
    objects: Sequence[SceneNode],
    transport: Transport | None,
    api_key: str,
    model: str,
    frame_paths: Iterable[pathlib.Path] | None,
    poses_path: pathlib.Path | None,
    mesh_profiles: dict[str, Any],
    deadline: float,
) -> list[LabelPatch] | None:
    if time.monotonic() >= deadline:
        return None
    scoped = graph.model_copy(update={"nodes": [n for n in graph.nodes if graph.bounds_the_room(n)] + list(objects)})
    body = chat_body(
        scoped,
        frame_paths=frame_paths,
        poses_path=poses_path,
        mesh_profiles=mesh_profiles,
        model=model,
    )
    if transport is not None:
        payload = transport(chat_url(), body, chat_headers(api_key))
    else:
        payload = openrouter_post(chat_url(), body, chat_headers(api_key), deadline=deadline)
    _log_usage(body.get("model"), payload.get("usage"))
    return patches_from_model(
        payload,
        scoped,
        allow_appearance=body_has_images(body),
        evidence_by_node=body_evidence_by_node(body),
    )


def _log_usage(model: Any, usage: Any) -> None:
    """One line per labelling request with its tokens and, where the host reports it, its cost in dollars."""
    if not isinstance(usage, dict):
        return
    logger.info("astra_usage model=%s prompt_tokens=%s completion_tokens=%s cost=%s", model,
                usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost"))
