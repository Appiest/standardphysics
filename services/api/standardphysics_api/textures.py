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
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import ValidationError
from standardphysics_contracts import (
    PhotoManifest,
    PoseRecord,
    SceneGraph,
    TextureBuild,
    TextureCoverage,
    TextureRequest,
    TextureStatus,
)
from standardphysics_pipeline.discovery.discover import detections_digest, known_detections
from standardphysics_pipeline.ingest import capture_to_room_from_payload
from standardphysics_pipeline.textures import (
    BakeInputs,
    bake_graph_for,
    box_bake_key,
    stale_node_ids,
    texture_build_key,
)
from standardphysics_pipeline.textures.scan_colour import paint_the_scan
from standardphysics_pipeline.textures.stages import timed
from standardphysics_pipeline.textures.surface_materials import materials_digest

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
    return "not_started", _built(connection, store, scan_id, poses, frames, shas), None


def room_materials_dir(store, scan_id) -> pathlib.Path:
    """Where a room's own generated materials live, if it has any."""
    return store.scan_dir(scan_id) / "materials"


def room_detections_dir(store, scan_id) -> pathlib.Path:
    """Where discovery keeps what the vision model said about each photo."""
    return store.scan_dir(scan_id) / "detections"


def _built(connection, store, scan_id, poses, frames: dict, shas: dict) -> dict:
    """The bake inputs, keyed by the photos themselves and the room's own materials.

    The key has to name the same build whether the phone's manifest arrived or
    the poses stood in for it, or one capture bakes twice under two names. New
    materials for a room change how it looks, so they change the key too.
    """
    lidar = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
    digest = hashlib.sha256(poses.sha256.encode())
    for frame_id in sorted(shas):
        digest.update(frame_id.encode())
        digest.update(shas[frame_id].encode())
    digest.update((lidar.sha256 if lidar else "").encode())
    digest.update(materials_digest(room_materials_dir(store, scan_id)).encode())
    digest.update(detections_digest(room_detections_dir(store, scan_id)).encode())
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
        return "not_started", _built(connection, store, scan_id, poses, frames, shas), None
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


def bake_inputs(database, store, scan_id, revision=None):
    """The layout a build would bake for this revision and the stored inputs it would read.

    Returns (bake_graph, inputs), where inputs is None while photos are missing.
    """
    with database.connect() as connection:
        _, bake, inputs, _ = _status(connection, store, scan_id, revision)
    return bake, inputs


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
    frame_paths = {key: store.artifact_path(scan_id, value) for key, value in inputs["frames"].items()}
    poses_path = store.artifact_path(scan_id, inputs["poses"])
    try:
        people = known_detections(frame_paths, poses_path, room_detections_dir(store, scan_id))
        painted = paint_the_scan(
            mesh_path=store.artifact_path(scan_id, inputs["lidar"]),
            poses_path=poses_path,
            frame_paths=frame_paths,
            graph=graph,
            out_path=out_dir / "scan.glb",
            materials_dir=room_materials_dir(store, scan_id),
            people=people,
        )
    except (ValueError, OSError, RuntimeError) as error:
        log.warning("no coloured scan for %s: %s", scan_id, error)
        return False
    log.info(
        "painted %.0f%% of the scan for %s from %d photos in %.0fs",
        painted.painted_fraction * 100, scan_id, painted.photos_used, painted.seconds,
    )
    return painted.glb_path.is_file()


def build_prefix(scan_id, build_key: str) -> str:
    """Where the asset route below serves this build's files from."""
    return f"/api/scans/{scan_id}/textures/{build_key}"


def build_dir(store, scan_id) -> pathlib.Path:
    root = store.scan_dir(scan_id) / "textures"
    root.mkdir(parents=True, exist_ok=True)
    return root


def staged_build_dir(store, scan_id) -> pathlib.Path:
    """A directory to assemble a build in, renamed into place only once it is whole."""
    return pathlib.Path(tempfile.mkdtemp(prefix=".bake-", dir=build_dir(store, scan_id)))


def finish_build(staged: pathlib.Path, destination: pathlib.Path, result: TextureBuild) -> None:
    """Seal a staged build and move it into place under its own name.

    The rename is what makes a build appear all at once. Nothing may be written into
    the destination directly, because the asset route serves whatever is there and a
    half-copied GLB is indistinguishable from a finished one.
    """
    for path in staged.iterdir():
        if not ASSET_NAME.fullmatch(path.name):
            raise ValueError(f"not a texture asset: {path.name}")
    (staged / "result.json").write_text(result.model_dump_json())
    staged.rename(destination)


def record_build(database, scan_id, build_key: str, graph: SceneGraph, inputs: dict, result: TextureBuild) -> None:
    """Record a build the job queue never queued, such as one a script produced.

    `run_texture` updates the row its own queued job already owns; this inserts one for
    a build that has no job behind it. Both write the row only after `finish_build` has
    renamed the files into place, so a row never points at a directory still being made.
    """
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO texture_builds (scan_id, build_key, graph_json, inputs_json, result_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(scan_id, build_key) DO UPDATE SET result_json=excluded.result_json",
            (
                str(scan_id), build_key, graph.model_dump_json(),
                json.dumps(inputs), result.model_dump_json(), repo.now(),
            ),
        )


@dataclass(frozen=True)
class _Boxes:
    """The photographed boxes of a build, however they were come by."""

    mask_names: list[str]
    coverage: TextureCoverage
    frames_used: int
    seconds: float


def run_texture(database, store, stages, scan_id, build_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM texture_builds WHERE id=? AND scan_id=?", (build_id, str(scan_id))
        ).fetchone()
    if row is None or row["result_json"]:
        return
    destination = build_dir(store, scan_id) / row["build_key"]
    result_path = destination / "result.json"
    if result_path.is_file():
        result = TextureBuild.model_validate_json(result_path.read_bytes())
    else:
        result = _new_build(database, store, stages, scan_id, row, destination)
    with database.transaction() as connection:
        connection.execute("UPDATE texture_builds SET result_json=? WHERE id=?", (result.model_dump_json(), build_id))


def _new_build(database, store, stages, scan_id, row, destination: pathlib.Path) -> TextureBuild:
    graph = SceneGraph.model_validate_json(row["graph_json"])
    inputs = json.loads(row["inputs_json"])
    box_key = box_bake_key(graph, inputs["digest"])
    temporary = staged_build_dir(store, scan_id)
    try:
        boxes = _reused_boxes(database, store, scan_id, box_key, temporary)
        with timed("box model"):
            boxes = boxes or _baked_boxes(store, stages, scan_id, graph, inputs, temporary)
        with timed("painted scan"):
            scan_glb = _paint_the_scan(store, scan_id, graph, inputs, temporary)
        prefix = build_prefix(scan_id, row["build_key"])
        result = TextureBuild(
            build_id=row["build_key"], glb_url=prefix + "/scene.glb",
            scan_glb_url=prefix + "/scan.glb" if scan_glb else None,
            coverage_mask_urls=[prefix + "/" + name for name in boxes.mask_names],
            bake_graph=graph, coverage=boxes.coverage,
            frames_used=boxes.frames_used, seconds=boxes.seconds, box_key=box_key,
        )
        finish_build(temporary, destination, result)
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _reused_boxes(database, store, scan_id, box_key: str, temporary: pathlib.Path) -> _Boxes | None:
    """The boxes of an earlier finished build of the same photos and layout, copied in rather than baked again.

    The boxes do not change when only the painter of the scan does, and baking
    them is minutes of work per walk that came out byte for byte the same.
    """
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT result_json FROM texture_builds WHERE scan_id=? AND result_json IS NOT NULL ORDER BY id DESC",
            (str(scan_id),),
        ).fetchall()
    for row in rows:
        earlier = TextureBuild.model_validate_json(row["result_json"])
        source = build_dir(store, scan_id) / earlier.build_id
        names = [url.rsplit("/", 1)[-1] for url in earlier.coverage_mask_urls]
        if earlier.box_key != box_key or not all((source / name).is_file() for name in ["scene.glb", *names]):
            continue
        for name in ["scene.glb", *names]:
            shutil.copyfile(source / name, temporary / name)
        return _Boxes(names, earlier.coverage, earlier.frames_used, 0.0)
    return None


def _baked_boxes(store, stages, scan_id, graph: SceneGraph, inputs: dict, temporary: pathlib.Path) -> _Boxes:
    baked = stages.bake_textures(BakeInputs(
        bake_graph=graph, poses_path=store.artifact_path(scan_id, inputs["poses"]),
        frame_paths={key: store.artifact_path(scan_id, value) for key, value in inputs["frames"].items()},
        lidar_mesh_path=store.artifact_path(scan_id, inputs["lidar"]) if inputs["lidar"] else None,
        out_dir=temporary,
        materials_dir=room_materials_dir(store, scan_id),
    ))
    if not baked.glb_path.is_file() or baked.glb_path.name != "scene.glb" or baked.glb_path.parent != temporary:
        raise ValueError("baker did not produce scene.glb")
    for mask in baked.coverage_mask_paths:
        if mask.parent != temporary or not mask.is_file() or not ASSET_NAME.fullmatch(mask.name):
            raise ValueError("baker produced an invalid coverage mask")
    return _Boxes([mask.name for mask in baked.coverage_mask_paths], baked.coverage, baked.frames_used, baked.seconds)


def install_texture_routes(app: FastAPI, database, store, worker):
    @app.get("/api/scans/{scan_id}/textures", response_model=TextureStatus)
    def get_status(scan_id: uuid.UUID, revision: int | None = None):
        return texture_status(database, store, scan_id, revision)

    @app.post("/api/scans/{scan_id}/textures", response_model=TextureStatus, status_code=202)
    def start(scan_id: uuid.UUID, body: TextureRequest):
        return queue_texture(database, store, worker, scan_id, body.revision, retry=True)

    @app.head("/api/scans/{scan_id}/textures/{build_key}/{filename}")
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
