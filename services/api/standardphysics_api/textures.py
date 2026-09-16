"""Immutable photo builds, independent of measured room revisions and assessments."""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
import shutil
import tempfile
import uuid

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import ValidationError
from standardphysics_contracts import PhotoManifest, PoseRecord, SceneGraph, TextureBuild, TextureRequest, TextureStatus
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures import BakeInputs, bake_graph_for, stale_node_ids, texture_build_key
from standardphysics_pipeline.textures.scan_colour import paint_the_scan

from . import repository as repo
from .errors import ApiProblem

log = logging.getLogger(__name__)

TEXTURE = "texture"
BUILD_KEY = re.compile(r"^[0-9a-f]{64}$")
ASSET_NAME = re.compile(r"^(scene\.glb|scan\.glb|coverage-[0-3]\.png)$")
MAX_METADATA_BYTES = 4_000_000


def _metadata(path: pathlib.Path) -> bytes:
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("photo metadata is too large")
    return path.read_bytes()


def validate_manifest(payload: bytes) -> PhotoManifest:
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("photo manifest is too large")
    return PhotoManifest.model_validate_json(payload)


def _poses_of(connection, store, scan_id):
    """The stored poses artifact and its projectable records, or nothing."""
    poses = repo.artifact_of_kind(connection, scan_id, "poses")
    if poses is None:
        return None, {}
    payload = json.loads(_metadata(store.artifact_path(scan_id, poses.id)))
    records = [PoseRecord.model_validate(item) for item in payload]
    cameras = {item.frame_id: item for item in records if item.projectable}
    if len(cameras) != sum(item.projectable for item in records):
        raise ValueError("duplicate camera frame ids")
    return poses, cameras


def _inputs_from_poses(connection, store, scan_id):
    """Build inputs from the poses when the phone's manifest never arrived.

    The manifest exists to say the upload finished. The poses say which photos
    the walk took, and every stored artifact was checksummed on the way in, so
    a scan whose every projectable pose has its photo is just as complete. A
    capture that reached us whole should not wait on a second copy of its own
    index.
    """
    poses, cameras = _poses_of(connection, store, scan_id)
    if poses is None or not cameras:
        return "needs_photos", None, None
    frames, shas = {}, {}
    for frame_id in sorted(cameras):
        artifact = repo.find_artifact(connection, scan_id, frame_id)
        if artifact is None or artifact.kind != "frames":
            return "waiting_for_photos", None, None
        frames[frame_id], shas[frame_id] = artifact.id, artifact.sha256
    return "not_started", _built(connection, scan_id, poses, frames, shas), None


def _built(connection, scan_id, poses, frames: dict, shas: dict) -> dict:
    """The bake inputs, keyed by the photos themselves.

    The key has to name the same build whether the phone's manifest arrived or
    the poses stood in for it, or one capture bakes twice under two names.
    """
    lidar = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
    digest = hashlib.sha256(poses.sha256.encode())
    for frame_id in sorted(shas):
        digest.update(frame_id.encode())
        digest.update(shas[frame_id].encode())
    digest.update((lidar.sha256 if lidar else "").encode())
    return {
        "poses": poses.id, "frames": frames,
        "lidar": lidar.id if lidar else None, "digest": digest.hexdigest(),
    }


def _manifest_frames(connection, scan_id, manifest, cameras):
    """Artifact ids and checksums for every photo the manifest names, or None while any is still uploading."""
    frames, shas = {}, {}
    for frame in manifest.frames:
        artifact = repo.find_artifact(connection, scan_id, frame.frame_id)
        if artifact is None:
            return None
        if artifact.kind != "frames" or artifact.sha256 != frame.sha256 or artifact.bytes != frame.bytes:
            raise ValueError("photo manifest does not match uploaded images")
        if frame.frame_id not in cameras:
            raise ValueError("photo lacks synchronized version 2 camera metadata; capture again")
        frames[frame.frame_id], shas[frame.frame_id] = artifact.id, artifact.sha256
    return frames, shas


def _inputs(connection, store, scan_id):
    manifest_artifact = repo.artifact_of_kind(connection, scan_id, "photo_manifest")
    if manifest_artifact is None:
        if not repo.artifact_of_kind(connection, scan_id, "frames"):
            return "needs_photos", None, None
        try:
            return _inputs_from_poses(connection, store, scan_id)
        except (ValueError, TypeError, OSError, ValidationError) as error:
            return "failed", None, str(error)[:300]
    try:
        manifest = validate_manifest(_metadata(store.artifact_path(scan_id, manifest_artifact.id)))
        poses, cameras = _poses_of(connection, store, scan_id)
        if poses is None:
            return "waiting_for_photos", None, None
        if poses.sha256 != manifest.poses_sha256:
            raise ValueError("photo manifest does not match camera poses")
        uploaded = _manifest_frames(connection, scan_id, manifest, cameras)
        if uploaded is None:
            return "waiting_for_photos", None, None
        frames, shas = uploaded
        return "not_started", _built(connection, scan_id, poses, frames, shas), None
    except (ValueError, TypeError, OSError, ValidationError) as error:
        return "failed", None, str(error)[:300]


def _graphs(connection, store, scan_id, revision):
    if not repo.scan_exists(connection, scan_id):
        raise ApiProblem(404, "no scan")
    row = repo.get_revision(connection, scan_id, revision)
    if row is None:
        raise ApiProblem(409, "room geometry is not ready")
    shown = repo.graph_of(row)
    capture_row = repo.get_revision(connection, scan_id, 0)
    if capture_row is None:
        raise ApiProblem(409, "captured room is not ready")
    capture = repo.graph_of(capture_row)
    if capture.capture_to_room is None:
        room = repo.artifact_of_kind(connection, scan_id, "room_json")
        if room:
            try:
                room_metadata = json.loads(_metadata(store.artifact_path(scan_id, room.id)))
                capture = capture.model_copy(
                    update={"capture_to_room": capture_to_room_from_payload(room_metadata)}
                )
            except (ValueError, KeyError, TypeError, OSError):
                pass
    return shown, bake_graph_for(shown, capture)


def _status(connection, store, scan_id, revision):
    shown, bake = _graphs(connection, store, scan_id, revision)
    state, inputs, error = _inputs(connection, store, scan_id)
    row = None
    key = None
    if inputs is not None:
        if bake.capture_to_room is None:
            state, inputs, error = "needs_photos", None, "capture alignment is missing"
        else:
            key = texture_build_key(bake, inputs["digest"])
            row = connection.execute(
                "SELECT b.*, j.state AS job_state, j.error AS job_error FROM texture_builds b"
                " LEFT JOIN jobs j ON j.scan_id=b.scan_id AND j.kind='texture' AND j.revision=b.id"
                " WHERE b.scan_id=? AND b.build_key=?",
                (str(scan_id), key),
            ).fetchone()
    if row:
        state = "complete" if row["result_json"] else row["job_state"] or "not_started"
        error = row["job_error"]
    ready = row if row and row["result_json"] else connection.execute(
        "SELECT * FROM texture_builds WHERE scan_id=? AND result_json IS NOT NULL ORDER BY id DESC LIMIT 1",
        (str(scan_id),),
    ).fetchone()
    build = TextureBuild.model_validate_json(ready["result_json"]) if ready else None
    stale = stale_node_ids(shown, build.bake_graph) if build else []
    result = TextureStatus(
        scan_id=scan_id, revision=shown.revision, state=state, build=build,
        exact=bool(ready and ready["build_key"] == key), stale_node_ids=stale, error=error,
        can_retry=bool(inputs and state == "failed"),
    )
    return result, bake, inputs, key


def texture_status(database, store, scan_id, revision=None):
    with database.connect() as connection:
        return _status(connection, store, scan_id, revision)[0]


def queue_texture(database, store, worker, scan_id, revision=None, *, retry=False):
    with database.transaction() as connection:
        status, bake, inputs, key = _status(connection, store, scan_id, revision)
        settled = status.state in ("queued", "running", "complete")
        if inputs is None or settled or (status.state == "failed" and not retry):
            return status
        connection.execute(
            "INSERT OR IGNORE INTO texture_builds (scan_id, build_key, graph_json, inputs_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (str(scan_id), key, bake.model_dump_json(), json.dumps(inputs), repo.now()),
        )
        row = connection.execute(
            "SELECT id FROM texture_builds WHERE scan_id=? AND build_key=?", (str(scan_id), key)
        ).fetchone()
        repo.queue_job_again(connection, scan_id, TEXTURE, row["id"])
    worker.wake()
    return texture_status(database, store, scan_id, revision)


def maybe_queue_texture(database, store, worker, scan_id, revision=None):
    try:
        queue_texture(database, store, worker, scan_id, revision)
    except ApiProblem as error:
        if error.status not in (404, 409):
            raise


def _paint_the_scan(store, scan_id, graph, inputs, out_dir) -> bool:
    """Colour the captured surface as well as the boxes, when there is a mesh to colour.

    The boxes are what a check measures and an owner drags, and they are the
    wrong thing to photograph: a generated top misses the surface under it by
    inches, so a photo laid on it slides. The scan is the surface, so this is
    the one that looks like the room. It is display only and never blocks a
    build; a capture without a mesh simply has no scan to show.
    """
    if not inputs["lidar"]:
        return False
    try:
        painted = paint_the_scan(
            mesh_path=store.artifact_path(scan_id, inputs["lidar"]),
            poses_path=store.artifact_path(scan_id, inputs["poses"]),
            frame_paths={key: store.artifact_path(scan_id, value) for key, value in inputs["frames"].items()},
            capture_to_room=graph.capture_to_room,
            out_path=out_dir / "scan.glb",
        )
    except (ValueError, OSError, RuntimeError) as error:
        log.warning("no coloured scan for %s: %s", scan_id, error)
        return False
    log.info(
        "painted %.0f%% of the scan for %s from %d photos in %.0fs",
        painted.painted_fraction * 100, scan_id, painted.photos_used, painted.seconds,
    )
    return painted.glb_path.is_file()


def run_texture(database, store, stages, scan_id, build_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM texture_builds WHERE id=? AND scan_id=?", (build_id, str(scan_id))
        ).fetchone()
    if row is None or row["result_json"]:
        return
    graph = SceneGraph.model_validate_json(row["graph_json"])
    inputs = json.loads(row["inputs_json"])
    root = store.scan_dir(scan_id) / "textures"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / row["build_key"]
    result_path = destination / "result.json"
    if result_path.is_file():
        result = TextureBuild.model_validate_json(result_path.read_bytes())
    else:
        temporary = pathlib.Path(tempfile.mkdtemp(prefix=".bake-", dir=root))
        try:
            baked = stages.bake_textures(BakeInputs(
                bake_graph=graph, poses_path=store.artifact_path(scan_id, inputs["poses"]),
                frame_paths={key: store.artifact_path(scan_id, value) for key, value in inputs["frames"].items()},
                lidar_mesh_path=store.artifact_path(scan_id, inputs["lidar"]) if inputs["lidar"] else None,
                out_dir=temporary,
            ))
            prefix = f"/api/scans/{scan_id}/textures/{row['build_key']}"
            if not baked.glb_path.is_file() or baked.glb_path.name != "scene.glb" or baked.glb_path.parent != temporary:
                raise ValueError("baker did not produce scene.glb")
            for mask in baked.coverage_mask_paths:
                if mask.parent != temporary or not mask.is_file() or not ASSET_NAME.fullmatch(mask.name):
                    raise ValueError("baker produced an invalid coverage mask")
            scan_glb = _paint_the_scan(store, scan_id, graph, inputs, temporary)
            result = TextureBuild(
                build_id=row["build_key"], glb_url=prefix + "/scene.glb",
                scan_glb_url=prefix + "/scan.glb" if scan_glb else None,
                coverage_mask_urls=[prefix + "/" + path.name for path in baked.coverage_mask_paths],
                bake_graph=graph, coverage=baked.coverage,
                frames_used=baked.frames_used, seconds=baked.seconds,
            )
            (temporary / "result.json").write_text(result.model_dump_json())
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    with database.transaction() as connection:
        connection.execute("UPDATE texture_builds SET result_json=? WHERE id=?", (result.model_dump_json(), build_id))


def install_texture_routes(app: FastAPI, database, store, worker):
    @app.get("/api/scans/{scan_id}/textures", response_model=TextureStatus)
    def get_status(scan_id: uuid.UUID, revision: int | None = None):
        return texture_status(database, store, scan_id, revision)

    @app.post("/api/scans/{scan_id}/textures", response_model=TextureStatus, status_code=202)
    def start(scan_id: uuid.UUID, body: TextureRequest):
        return queue_texture(database, store, worker, scan_id, body.revision, retry=True)

    @app.get("/api/scans/{scan_id}/textures/{build_key}/{filename}")
    def asset(scan_id: uuid.UUID, build_key: str, filename: str):
        if not BUILD_KEY.fullmatch(build_key) or not ASSET_NAME.fullmatch(filename):
            raise ApiProblem(404, "no texture asset")
        with database.connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM texture_builds WHERE scan_id=? AND build_key=?",
                (str(scan_id), build_key),
            ).fetchone()
        if row is None or row["result_json"] is None:
            raise ApiProblem(404, "textures are not ready")
        path = store.scan_dir(scan_id) / "textures" / build_key / filename
        if not path.is_file():
            raise ApiProblem(404, "no texture asset")
        return FileResponse(
            path,
            media_type="model/gltf-binary" if filename.endswith(".glb") else "image/png",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )
