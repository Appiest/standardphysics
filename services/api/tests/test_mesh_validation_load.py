"""Checking an uploaded mesh neither stalls the server nor holds the whole file in memory.

A walk's mesh can be hundreds of megabytes of JSON. The check runs on a worker
thread, only so many run at once, and it reads the file one part at a time.
"""

import dataclasses
import json
import threading
import time
import tracemalloc

import pytest
from test_lidar_mesh import mesh_bytes

from conftest import create_scan, put_artifact
from standardphysics_api import app as api_app
from standardphysics_api import lidar_mesh

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def _part(index: int, vertex_count: int = 3) -> dict:
    return {
        "id": f"00000000-0000-0000-0000-{index:012d}",
        "transform": IDENTITY,
        "vertices": [0.125 * (step % 7) for step in range(vertex_count * 3)],
        "triangles": [step % vertex_count for step in range(3 * max(1, vertex_count // 3))],
    }


def _write(tmp_path, document) -> object:
    path = tmp_path / "lidar-mesh.json"
    path.write_bytes(document if isinstance(document, bytes) else json.dumps(document).encode())
    return path


def _slow_mesh_check(monkeypatch, check):
    original = api_app.STAGED_CHECKS["lidar_mesh"]
    monkeypatch.setitem(api_app.STAGED_CHECKS, "lidar_mesh", dataclasses.replace(original, validate=check))


def test_the_server_answers_health_while_a_mesh_is_being_checked(make_client, monkeypatch):
    checking, release, finished = threading.Event(), threading.Event(), threading.Event()

    def held_check(path):
        checking.set()
        release.wait(timeout=10)
        finished.set()

    _slow_mesh_check(monkeypatch, held_check)
    with make_client() as phone:
        scan_id = create_scan(phone)
        upload = threading.Thread(target=put_artifact, args=(phone, scan_id, "lidar-mesh", mesh_bytes(), "lidar_mesh"))
        upload.start()
        assert checking.wait(timeout=5)
        health = phone.get("/health")
        answered_during_the_check = not finished.is_set()
        release.set()
        upload.join(timeout=10)
    assert health.status_code == 200
    assert answered_during_the_check


@pytest.mark.parametrize("cap", [1, 2])
def test_no_more_meshes_are_checked_at_once_than_the_cap(make_client, monkeypatch, cap):
    running, most = 0, 0
    lock = threading.Lock()

    def counted_check(path):
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.2)
        with lock:
            running -= 1

    _slow_mesh_check(monkeypatch, counted_check)
    with make_client(max_concurrent_validations=cap, max_owner_uploads=8) as phone:
        scan_id = create_scan(phone)
        statuses: list[int] = []
        uploads = [
            threading.Thread(target=lambda name=f"mesh-{index}": statuses.append(
                put_artifact(phone, scan_id, name, mesh_bytes(), "lidar_mesh").status_code))
            for index in range(4)
        ]
        for upload in uploads:
            upload.start()
        for upload in uploads:
            upload.join(timeout=20)
    assert sorted(statuses) == [201] * 4
    assert most == cap


def test_a_mesh_is_read_a_part_at_a_time(tmp_path):
    path = _write(tmp_path, {"parts": [_part(index, vertex_count=600) for index in range(1200)], "floorY": -0.5})
    size = path.stat().st_size
    tracemalloc.start()
    try:
        checked = lidar_mesh.validate_lidar_mesh_file(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert checked.parts == 1200
    assert checked.vertices == 1200 * 600
    assert peak < size / 2, f"peak {peak} bytes for a {size} byte file"


def test_a_part_bigger_than_one_read_is_still_read_whole(tmp_path, monkeypatch):
    monkeypatch.setattr("standardphysics_api.lidar_mesh.READ_CHUNK_BYTES", 64)
    path = _write(tmp_path, {"parts": [_part(0, vertex_count=300), _part(1, vertex_count=3)]})
    assert lidar_mesh.validate_lidar_mesh_file(path).parts == 2


VALID = json.loads(mesh_bytes())


@pytest.mark.parametrize("document", [
    b"",
    b"   ",
    b"[]",
    b"{}",
    b'{"parts": []}',
    b'{"parts": null}',
    b"[" * 200_000,
    b'{"parts": [' + b"[" * 200_000,
    json.dumps(VALID).encode() + b" trailing",
    json.dumps(VALID).encode()[:-1],
    json.dumps({**VALID, "extra": 1}).encode(),
    json.dumps({**VALID, "floorY": "low"}).encode(),
    json.dumps({**VALID, "peopleFilteringEnabled": 1}).encode(),
    json.dumps(VALID)[:-1].encode() + b', "parts": []}',
    json.dumps({"parts": [{**VALID["parts"][0], "triangles": [0, 1, 3]}]}).encode(),
    json.dumps({"parts": [{**VALID["parts"][0], "vertices": [0, 0, "0"]}]}).encode(),
    json.dumps({"parts": [{**VALID["parts"][0], "extra": True}]}).encode(),
    b'{"parts": [' + json.dumps(VALID["parts"][0]).encode() + b",]}",
    b'{"parts": [' + json.dumps(VALID["parts"][0]).encode() + b"] ,}",
    b'{"parts": [' + json.dumps(VALID["parts"][0]).encode().replace(b"0,", b"NaN,", 1) + b"]}",
    b"\xff\xfe" + json.dumps(VALID).encode(),
])
def test_a_malformed_mesh_is_refused(tmp_path, document):
    with pytest.raises(lidar_mesh.InvalidLidarMesh):
        lidar_mesh.validate_lidar_mesh_file(_write(tmp_path, document))


@pytest.mark.parametrize("document", [
    VALID,
    {**VALID, "peopleFilteringEnabled": True, "floorY": -0.25},
    {"floorY": None, "parts": VALID["parts"]},
    {"parts": [_part(index) for index in range(5)]},
])
def test_a_well_formed_mesh_passes(tmp_path, document):
    assert lidar_mesh.validate_lidar_mesh_file(_write(tmp_path, json.dumps(document, indent=2).encode())).parts == len(
        document["parts"]
    )


def test_more_parts_than_a_mesh_may_have_are_refused(tmp_path):
    path = _write(tmp_path, {"parts": [_part(index) for index in range(4097)]})
    with pytest.raises(lidar_mesh.InvalidLidarMesh):
        lidar_mesh.validate_lidar_mesh_file(path)
