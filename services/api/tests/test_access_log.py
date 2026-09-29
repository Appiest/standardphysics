"""The API's access log never carries a share link's token, which opens a report to anyone who has it."""

from __future__ import annotations

import io
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request

import uvicorn

from standardphysics_api.__main__ import server_config
from standardphysics_api.access_log import without_tokens

TOKEN = secrets.token_urlsafe(24)


def _serve_briefly_and_fetch(app, path: str) -> str:
    """Serve `app` with the production server config, ask for `path` once, and return what the access log wrote."""
    config = server_config(app, "127.0.0.1", 0)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    handler.setFormatter(uvicorn.logging.AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    access = logging.getLogger("uvicorn.access")
    access.addHandler(handler)
    try:
        port = server.servers[0].sockets[0].getsockname()[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10).read()
        except urllib.error.HTTPError:
            pass
    finally:
        server.should_exit = True
        thread.join(10)
        access.removeHandler(handler)
    return captured.getvalue()


def test_a_share_request_is_logged_without_its_token(make_client):
    app = make_client(sign_in_as_owner=False).app
    for path in (f"/api/shared/{TOKEN}", f"/api/shared/{TOKEN}/scene.glb"):
        logged = _serve_briefly_and_fetch(app, path)
        assert "/api/shared/" in logged, logged
        assert TOKEN not in logged, logged


def test_only_the_token_segment_is_replaced():
    assert without_tokens(f"/api/shared/{TOKEN}/renders/a.png?x=1") == "/api/shared/<token>/renders/a.png?x=1"
    assert without_tokens(f"/r/{TOKEN}") == "/r/<token>"
    assert without_tokens("/api/scans/123/shares") == "/api/scans/123/shares"
