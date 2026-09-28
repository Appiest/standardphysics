"""A client that stops sending partway through a body is cut off, and gives back everything it held.

The test client reads a whole body before the app sees it, so these requests
go straight to the ASGI app on the client's event loop, from a client that
sends a first chunk and then goes quiet, or trickles bytes for ever.
"""

import hashlib

import anyio
import pytest

from conftest import create_scan, put_artifact
from standardphysics_api.auth import COOKIE_NAME

FIRST_CHUNK = b"x" * 10
DECLARED = 1000
TEST_TIMEOUT_SECONDS = 10


class StallingClient:
    """Sends the start of a body, then nothing more, or a trickle when `trickle_seconds` is set,
    and hangs up once it has the whole response."""

    def __init__(self, trickle_seconds: float | None = None):
        self.trickle_seconds = trickle_seconds
        self.sent: list[dict] = []
        self.answered = anyio.Event()
        self.started = False

    async def receive(self) -> dict:
        if not self.started:
            self.started = True
            return {"type": "http.request", "body": FIRST_CHUNK, "more_body": True}
        if self.trickle_seconds is not None:
            with anyio.move_on_after(self.trickle_seconds):
                await self.answered.wait()
            if not self.answered.is_set():
                return {"type": "http.request", "body": b"x", "more_body": True}
        await self.answered.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        self.sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            self.answered.set()

    def status(self) -> int:
        return next(message["status"] for message in self.sent if message["type"] == "http.response.start")


def _scope(method: str, path: str, headers: dict[str, str]) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }


async def _run(app, scope: dict, client: StallingClient) -> None:
    with anyio.fail_after(TEST_TIMEOUT_SECONDS):
        await app(scope, client.receive, client.send)


def _stall(test_client, method: str, path: str, headers: dict[str, str], trickle_seconds: float | None = None) -> int:
    signed_in = {"cookie": f"{COOKIE_NAME}={test_client.cookies.get(COOKIE_NAME)}", "content-length": str(DECLARED)}
    client = StallingClient(trickle_seconds)
    test_client.portal.call(_run, test_client.app, _scope(method, path, {**headers, **signed_in}), client)
    return client.status()


def _artifact_headers() -> dict[str, str]:
    return {"x-checksum-sha256": hashlib.sha256(b"x" * DECLARED).hexdigest(), "x-artifact-kind": "frames"}


def _staged(test_client, scan_id: str) -> list:
    return list(test_client.app.state.store.scan_dir(scan_id).glob("*/.upload-*"))


def test_an_upload_that_goes_quiet_is_dropped_and_releases_what_it_held(make_client):
    with make_client(upload_idle_seconds=1, max_owner_uploads=1) as client:
        scan_id = create_scan(client)
        status = _stall(client, "PUT", f"/api/scans/{scan_id}/artifacts/frame-0001", _artifact_headers())
        staged = _staged(client, scan_id)
        retried = put_artifact(client, scan_id, "frame-0001", b"x" * DECLARED, "frames")
    assert status == 408
    assert staged == []
    assert retried.status_code == 201, retried.text


def test_an_upload_that_trickles_past_its_total_deadline_is_dropped(make_client):
    with make_client(upload_idle_seconds=5, upload_total_seconds=1) as client:
        scan_id = create_scan(client)
        path = f"/api/scans/{scan_id}/artifacts/frame-0001"
        status = _stall(client, "PUT", path, _artifact_headers(), trickle_seconds=0.1)
        staged = _staged(client, scan_id)
    assert status == 408
    assert staged == []


@pytest.mark.parametrize("path", ["/api/auth/sign-in", "/api/scans"])
def test_a_json_body_that_goes_quiet_is_answered_with_a_408(make_client, path):
    with make_client(upload_idle_seconds=1) as client:
        status = _stall(client, "POST", path, {"content-type": "application/json"})
    assert status == 408
