"""Put one real room through a running API and check what comes out.

    python3 scripts/smoke_scan.py --api http://127.0.0.1:18787

scripts/smoke_image.sh runs this against the image it has just started, so it
needs nothing but the standard library. It signs up its own account, uploads
Apple's RoomPlan sample bedroom the way the phone does (room.json, room.usdz
and its metadata, each with its checksum), finalises the scan and polls until
the worker has made it ready. Then it checks three things:

- The assessment has findings, and one of them is the bedroom's door, which
  RoomPlan measured at 29.9 inches clear against the 32 a doorway needs.
- The door finding has a picture, and it is a PNG. Only Blender renders those.
- The display model is a glTF file. Both ways the API draws one run Blender,
  so with a broken Blender this answers 404.

CI has no model keys, so this also pins down how the pipeline behaves without
them: every object keeps the name RoomPlan gave it (`labeled_by` is
"roomplan") instead of one a vision model chose, and the scan is still ready.

The committed captures with LiDAR mesh and poses carry no photos, and no room
here has both a USDZ and photos, so the photo texture bake is not exercised.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
ROOM = REPO / "packages/fixtures/standardphysics_fixtures/data/real"
UPLOADS = (
    ("room-json", "room_json", "apple_bedroom3.room.json"),
    ("room-usdz", "room_usdz", "apple_bedroom3.usdz"),
    ("room-metadata", "room_metadata", "apple_bedroom3.metadata.plist"),
)
DOOR_CLEAR_INCHES = 29.9
FINISHED_STATES = {"ready", "failed"}


class SmokeFailure(Exception):
    pass


class Api:
    """One signed-in session against the API, cookies and all."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> bytes:
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise SmokeFailure(f"{method} {path} answered {error.code}: {error.read()[:400]!r}") from None
        except urllib.error.URLError as error:
            raise SmokeFailure(f"{method} {path} did not reach the API: {error.reason}") from None

    def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode()
        answer = self.call(method, path, body, {"Content-Type": "application/json"})
        return json.loads(answer or b"{}")


def ok(message: str) -> None:
    print(f"ok  {message}", flush=True)


def upload_the_bedroom(api: Api) -> str:
    stamp = int(time.time())
    api.json("POST", "/api/auth/sign-up", {
        "email": f"smoke-scan-{stamp}@example.com", "password": "smoke-scan-password", "shop_name": "Smoke scan",
    })
    scan_id = api.json("POST", "/api/scans", {
        "name": "Apple sample bedroom", "device_model": "iPhone17,1", "duration_seconds": 60.0,
    })["id"]
    for artifact_id, kind, filename in UPLOADS:
        data = (ROOM / filename).read_bytes()
        api.call("PUT", f"/api/scans/{scan_id}/artifacts/{artifact_id}", data, {
            "Content-Type": "application/octet-stream",
            "X-Artifact-Kind": kind,
            "X-Checksum-SHA256": hashlib.sha256(data).hexdigest(),
        })
    api.json("POST", f"/api/scans/{scan_id}/complete", {})
    ok(f"uploaded and finalised the sample bedroom as {scan_id}")
    return scan_id


def wait_until_processed(api: Api, scan_id: str, seconds: float) -> None:
    deadline, state = time.monotonic() + seconds, "unknown"
    while time.monotonic() < deadline:
        state = api.json("GET", f"/api/scans/{scan_id}")["state"]
        if state in FINISHED_STATES:
            break
        time.sleep(2)
    if state != "ready":
        raise SmokeFailure(f"the scan is {state!r} after {seconds:.0f}s, not ready")
    ok("the worker made the scan ready")


def check_labels_came_from_roomplan(api: Api, scan_id: str) -> None:
    nodes = api.json("GET", f"/api/scans/{scan_id}/scene")["nodes"]
    labellers = {node.get("labeled_by") for node in nodes}
    if not nodes or labellers != {"roomplan"}:
        raise SmokeFailure(f"expected {len(nodes)} RoomPlan-labelled objects without a model key, got {labellers}")
    ok(f"all {len(nodes)} objects kept their RoomPlan names, as they should with no model key")


def the_door_finding(api: Api, scan_id: str) -> tuple[dict, int]:
    """The door's finding, and how many findings there are in all."""
    findings = api.json("GET", f"/api/scans/{scan_id}/assessment")["findings"]
    door = next(
        (finding for finding in findings
         if finding["outcome"] == "problem" and round(finding.get("measured_inches") or 0, 1) == DOOR_CLEAR_INCHES),
        None,
    )
    if door is None:
        raise SmokeFailure(f"no finding for the {DOOR_CLEAR_INCHES} inch door among {len(findings)}")
    return door, len(findings)


def wait_for_the_door_picture(api: Api, scan_id: str, seconds: float) -> str:
    """The pictures are drawn by a job queued once the scan is ready, so they arrive a little after it."""
    door, count = the_door_finding(api, scan_id)
    ok(f"the assessment has {count} findings, the door at {DOOR_CLEAR_INCHES} inches among them")
    deadline = time.monotonic() + seconds
    while not (door.get("locus") or {}).get("render_url"):
        if time.monotonic() > deadline:
            raise SmokeFailure(f"the door finding still has no picture after {seconds:.0f}s")
        time.sleep(2)
        door, _ = the_door_finding(api, scan_id)
    return door["locus"]["render_url"]


def check_blender_drew_the_scan(api: Api, scan_id: str, seconds: float) -> None:
    """The picture comes from the display job, which also stores the model if processing did not, so it goes first."""
    render_url = wait_for_the_door_picture(api, scan_id, seconds)
    picture = api.call("GET", render_url)
    if not picture.startswith(b"\x89PNG"):
        raise SmokeFailure(f"{render_url} is not a PNG")
    ok(f"Blender rendered the door finding ({len(picture):,} bytes of PNG)")
    model = api.call("GET", f"/api/scans/{scan_id}/scene.glb")
    if model[:4] != b"glTF":
        raise SmokeFailure(f"scene.glb starts {model[:4]!r}, not glTF")
    ok(f"Blender exported the display model ({len(model):,} bytes of glTF)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True)
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for the scan to be ready")
    args = parser.parse_args()
    api = Api(args.api)
    try:
        scan_id = upload_the_bedroom(api)
        wait_until_processed(api, scan_id, args.timeout)
        check_labels_came_from_roomplan(api, scan_id)
        check_blender_drew_the_scan(api, scan_id, args.timeout)
    except SmokeFailure as failure:
        print(f"scan smoke test failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
