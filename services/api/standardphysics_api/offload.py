"""Photo bakes and Blender steps run on a machine with more memory, with this one as the fallback.

`main` serves the four heavy stage functions (`bake_textures`, `export_glb`, `usdz_to_glb`,
`render_finding`) on another box on the same private network, such as a Mac on Tailscale. For
each call the API sends the input files as one tar and gets the output files back as another.

The other box takes the work only when it runs the same commit and the same Blender as this
one, so an offloaded result is the one this machine would have made. When it refuses, cannot
be reached or fails for any reason but Blender's own error, the stage runs here as it always
did. A BlenderError from the other box is raised here unchanged: the same Blender on the same
inputs would fail the same way locally, and trying again here would only double the wait.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hmac
import io
import json
import logging
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from standardphysics_contracts import Locus, SceneGraph, TextureCoverage
from standardphysics_pipeline import blender
from standardphysics_pipeline.check_blender import blender_path
from standardphysics_pipeline.textures import BakeInputs, BakeResult
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

DOCUMENT = "document.json"
"""The one JSON member of every parcel: a call's arguments going out, its result coming back."""
OUTPUTS = "out"
CHUNK_BYTES = 1 << 20
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


class _Parcel:
    """A tar of named files plus a JSON document, the shape both directions of a call take."""

    def __init__(self, directory: pathlib.Path):
        self.directory = directory
        self.files: dict[str, pathlib.Path] = {}

    def add(self, role: str, path: pathlib.Path | None) -> str | None:
        """Put a file, or a whole directory, in the parcel and return the name the other side finds it by.

        The file keeps its own name because Blender's importers read the extension.
        """
        if path is None:
            return None
        name = f"{role}/{path.name}"
        self.files[name] = path
        return name

    def write(self, document: dict[str, Any], filename: str) -> pathlib.Path:
        target = self.directory / filename
        encoded = json.dumps(document).encode()
        with tarfile.open(target, "w", dereference=True) as tar:
            info = tarfile.TarInfo(DOCUMENT)
            info.size = len(encoded)
            tar.addfile(info, io.BytesIO(encoded))
            for name, path in self.files.items():
                tar.add(path, arcname=name)
        return target


def _open(parcel: pathlib.Path, into: pathlib.Path) -> dict[str, Any]:
    """Unpack a parcel into a directory and return its document.

    Only plain files and directories are written, each checked to land inside the directory.
    `extractall(filter="data")` would do the same, but only from Python 3.11.4.
    """
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(parcel) as tar:
        for member in tar:
            _unpack_member(tar, member, into)
    return json.loads((into / DOCUMENT).read_text())


def _unpack_member(tar: tarfile.TarFile, member: tarfile.TarInfo, into: pathlib.Path) -> None:
    target = _member(into, member.name)
    if member.isdir():
        target.mkdir(parents=True, exist_ok=True)
        return
    source = tar.extractfile(member) if member.isfile() else None
    if source is None:
        raise ValueError(f"{member.name!r} is not a plain file")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source, target.open("wb") as file:
        shutil.copyfileobj(source, file, CHUNK_BYTES)


def _member(root: pathlib.Path, name: str) -> pathlib.Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"{name!r} is outside the parcel")
    return path


def _inside(root: pathlib.Path, name: str | None) -> pathlib.Path | None:
    return None if name is None else _member(root, name)


def _deliver(root: pathlib.Path, name: str, target: pathlib.Path) -> pathlib.Path:
    """Move one output file to where the local stage would have written it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(_member(root, f"{OUTPUTS}/{pathlib.Path(name).name}"), target)
    return target


@dataclass(frozen=True)
class _Operation:
    """One stage function as three steps: pack its arguments here, run it there, rebuild its result here."""

    pack: Callable[..., dict[str, Any]]
    serve: Callable[[Any, dict[str, Any], pathlib.Path, pathlib.Path], dict[str, Any]]
    unpack: Callable[..., Any]


def _pack_export(parcel: _Parcel, graph: SceneGraph, out_path: pathlib.Path, lidar_mesh=None) -> dict[str, Any]:
    return {"graph": graph.model_dump_json(), "lidar_mesh": parcel.add("lidar", lidar_mesh)}


def _serve_export(steps, document, root, out) -> dict[str, Any]:
    graph = SceneGraph.model_validate_json(document["graph"])
    glb = steps.export_glb(graph, out / "scene.glb", _inside(root, document["lidar_mesh"]))
    return {"output": glb.name}


def _unpack_export(document, root, graph, out_path, lidar_mesh=None) -> pathlib.Path:
    return _deliver(root, document["output"], out_path)


def _pack_conversion(parcel: _Parcel, usdz: pathlib.Path, out: pathlib.Path, mapping=None) -> dict[str, Any]:
    return {"usdz": parcel.add("usdz", usdz), "mapping": parcel.add("mapping", mapping)}


def _serve_conversion(steps, document, root, out) -> dict[str, Any]:
    converted = steps.usdz_to_glb(
        _member(root, document["usdz"]), out / "scene.glb", _inside(root, document["mapping"])
    )
    counts = converted._asdict()
    counts.pop("glb_path")
    return {"output": converted.glb_path.name, "counts": counts}


def _unpack_conversion(document, root, usdz, out, mapping=None) -> blender.ConversionResult:
    return blender.ConversionResult(glb_path=_deliver(root, document["output"], out), **document["counts"])


def _pack_render(parcel: _Parcel, graph: SceneGraph, locus, out_path, size=(1200, 800)) -> dict[str, Any]:
    return {"graph": graph.model_dump_json(), "locus": locus.model_dump_json(), "size": list(size)}


def _serve_render(steps, document, root, out) -> dict[str, Any]:
    graph = SceneGraph.model_validate_json(document["graph"])
    locus = Locus.model_validate_json(document["locus"])
    still = steps.render_finding(graph, locus, out / "finding.png", tuple(document["size"]))
    return {"output": still.name}


def _unpack_render(document, root, graph, locus, out_path, size=(1200, 800)) -> pathlib.Path:
    return _deliver(root, document["output"], out_path)


def _pack_bake(parcel: _Parcel, inputs: BakeInputs) -> dict[str, Any]:
    materials = inputs.materials_dir if inputs.materials_dir is not None and inputs.materials_dir.is_dir() else None
    return {
        "graph": inputs.bake_graph.model_dump_json(),
        "poses": parcel.add("poses", inputs.poses_path),
        "frames": {
            key: parcel.add(f"frames/{index}", path) for index, (key, path) in enumerate(inputs.frame_paths.items())
        },
        "lidar": parcel.add("lidar", inputs.lidar_mesh_path),
        "materials": parcel.add("materials", materials),
    }


def _serve_bake(steps, document, root, out) -> dict[str, Any]:
    baked = steps.bake_textures(
        BakeInputs(
            bake_graph=SceneGraph.model_validate_json(document["graph"]),
            poses_path=_member(root, document["poses"]),
            frame_paths={key: _member(root, name) for key, name in document["frames"].items()},
            lidar_mesh_path=_inside(root, document["lidar"]),
            out_dir=out,
            materials_dir=_inside(root, document["materials"]),
        )
    )
    return {
        "glb": baked.glb_path.name,
        "masks": [mask.name for mask in baked.coverage_mask_paths],
        "coverage": baked.coverage.model_dump_json(),
        "frames_used": baked.frames_used,
        "seconds": baked.seconds,
    }


def _unpack_bake(document, root, inputs: BakeInputs) -> BakeResult:
    return BakeResult(
        glb_path=_deliver(root, document["glb"], inputs.out_dir / document["glb"]),
        coverage_mask_paths=[_deliver(root, name, inputs.out_dir / name) for name in document["masks"]],
        coverage=TextureCoverage.model_validate_json(document["coverage"]),
        frames_used=document["frames_used"],
        seconds=document["seconds"],
    )


OPERATIONS: dict[str, _Operation] = {
    "export_glb": _Operation(_pack_export, _serve_export, _unpack_export),
    "usdz_to_glb": _Operation(_pack_conversion, _serve_conversion, _unpack_conversion),
    "render_finding": _Operation(_pack_render, _serve_render, _unpack_render),
    "bake_textures": _Operation(_pack_bake, _serve_bake, _unpack_bake),
}


@functools.cache
def local_blender_version() -> str:
    """The first line of `blender --version`, such as "Blender 5.2.1"."""
    output = subprocess.run([blender_path(), "--version"], capture_output=True, text=True, timeout=120).stdout
    return output.splitlines()[0].strip()


@dataclass
class Offload:
    """The client side: runs an operation on the other box, or says why it could not."""

    url: str
    token: str
    commit: str
    timeout_seconds: float
    blender_version: Callable[[], str] = local_blender_version
    client: Callable[[], httpx.Client] | None = None

    @classmethod
    def from_settings(cls, settings) -> Offload | None:
        if not settings.offload_url or not settings.offload_token:
            return None
        return cls(
            settings.offload_url.rstrip("/"), settings.offload_token, settings.git_sha, settings.bake_timeout_seconds
        )

    def run_or_fall_back(self, operation: str, local: Callable[..., Any], *args) -> Any:
        try:
            return self.run(operation, *args)
        except blender.BlenderError:
            raise
        except Exception as error:
            log.warning("%s ran here because the offload box at %s did not: %s", operation, self.url, _reason(error))
            return local(*args)

    def run(self, operation: str, *args) -> Any:
        spec = OPERATIONS[operation]
        with tempfile.TemporaryDirectory(prefix="sp-offload-") as directory:
            work = pathlib.Path(directory)
            parcel = _Parcel(work)
            sent = parcel.write(spec.pack(parcel, *args), "request.tar")
            received = self._post(operation, sent, work / "response.tar")
            document = _open(received, work / "result")
            return spec.unpack(document, work / "result", *args)

    def _post(self, operation: str, sent: pathlib.Path, received: pathlib.Path) -> pathlib.Path:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-SP-Commit": self.commit,
            "X-SP-Blender": self.blender_version(),
        }
        with (
            self._client() as client,
            client.stream(
                "POST", f"{self.url}/v1/stages/{operation}", content=_chunks(sent), headers=headers
            ) as response,
        ):
            _raise_for_refusal(response)
            with received.open("wb") as file:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    file.write(chunk)
        return received

    def _client(self) -> httpx.Client:
        if self.client is not None:
            return self.client()
        timeout = httpx.Timeout(10.0, read=self.timeout_seconds, write=self.timeout_seconds)
        return httpx.Client(timeout=timeout)


def _chunks(path: pathlib.Path) -> Iterator[bytes]:
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            yield chunk


def _raise_for_refusal(response: httpx.Response) -> None:
    if response.status_code == 200:
        return
    response.read()
    if response.status_code == 422:
        raise blender.BlenderError(response.json()["blender_error"])
    raise OffloadRefused(f"HTTP {response.status_code}: {response.text[:500]}")


class OffloadRefused(RuntimeError):
    """The other box answered but would not run the operation: wrong token, commit or Blender."""


def _reason(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def offloaded(stages, offload: Offload | None):
    """These stages with each heavy function tried on the other box first. Without one, the stages as they are."""
    if offload is None:
        return stages
    heavy = {name: getattr(stages, name) for name in OPERATIONS}
    return dataclasses.replace(
        stages,
        **{name: functools.partial(offload.run_or_fall_back, name, local) for name, local in heavy.items()},
    )


def create_offload_app(token: str, commit: str, blender_version: str, steps) -> FastAPI:
    """The server side. `steps` is anything with the four heavy functions, normally `Stages()`."""
    app = FastAPI(title="Standard Physics offload")

    @app.get("/v1/health")
    def health() -> dict[str, str]:
        return {"commit": commit, "blender": blender_version}

    @app.post("/v1/stages/{operation}")
    async def run(operation: str, request: Request):
        refusal = _refusal(request, operation, token, commit, blender_version)
        if refusal is not None:
            return refusal
        work = pathlib.Path(tempfile.mkdtemp(prefix="sp-offload-"))
        try:
            received = await _receive(request, work / "request.tar")
            sent = await run_in_threadpool(_serve, OPERATIONS[operation], steps, received, work)
        except blender.BlenderError as error:
            shutil.rmtree(work, ignore_errors=True)
            return JSONResponse({"blender_error": str(error)}, status_code=422)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        return FileResponse(sent, media_type="application/x-tar", background=BackgroundTask(shutil.rmtree, work, True))

    return app


def _refusal(request: Request, operation: str, token: str, commit: str, blender_version: str) -> JSONResponse | None:
    offered = request.headers.get("authorization", "")
    if not hmac.compare_digest(offered.encode(), f"Bearer {token}".encode()):
        return JSONResponse({"refused": "wrong token"}, status_code=401)
    if operation not in OPERATIONS:
        return JSONResponse({"refused": f"no operation {operation!r}"}, status_code=404)
    theirs = (request.headers.get("x-sp-commit"), request.headers.get("x-sp-blender"))
    if "unknown" in (commit, theirs[0]) or theirs != (commit, blender_version):
        mine = {"commit": commit, "blender": blender_version}
        return JSONResponse({"refused": "versions differ", "here": mine, "sent": theirs}, status_code=409)
    return None


async def _receive(request: Request, target: pathlib.Path) -> pathlib.Path:
    with target.open("wb") as file:
        async for chunk in request.stream():
            file.write(chunk)
    return target


def _serve(spec: _Operation, steps, received: pathlib.Path, work: pathlib.Path) -> pathlib.Path:
    root = work / "in"
    document = _open(received, root)
    out = work / OUTPUTS
    out.mkdir()
    result = spec.serve(steps, document, root, out)
    parcel = _Parcel(work)
    for path in sorted(out.iterdir()):
        parcel.add(OUTPUTS, path)
    return parcel.write(result, "response.tar")


def _this_commit() -> str:
    """SP_GIT_SHA, or the checkout's HEAD. A checkout with uncommitted changes counts as no commit."""
    if os.environ.get("SP_GIT_SHA"):
        return os.environ["SP_GIT_SHA"]
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if dirty.returncode != 0 or dirty.stdout.strip():
        return "unknown"
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from .stages import Stages

    parser = argparse.ArgumentParser(description="Run the API's photo bakes and Blender steps on this machine.")
    parser.add_argument("--host", required=True, help="the address to listen on, such as this machine's Tailscale IP")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args(argv)
    token = os.environ.get("SP_OFFLOAD_TOKEN")
    if not token:
        raise SystemExit("Set SP_OFFLOAD_TOKEN to the same value as the API's.")
    commit, version = _this_commit(), local_blender_version()
    log.warning("offload serving commit %s on %s", commit, version)
    uvicorn.run(create_offload_app(token, commit, version, Stages()), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
