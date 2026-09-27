"""Pushes to the owner's phone, and the guest shops that are deleted after 30 days."""

import base64
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from PIL import Image

from conftest import OWNER_EMAIL, OWNER_PASSWORD, create_scan, drain
from standardphysics_api import guest_sweep
from standardphysics_api.notifications import ApnsNotifier, LoggedNotifier, Push, notifier_from
from standardphysics_api.settings import Settings

DEVICE = "ab" * 32


class Recorder:
    def __init__(self):
        self.sent: list[tuple[str, Push]] = []

    def send(self, database, owner_id, push):
        self.sent.append((str(owner_id), push))


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_a_key_path_the_server_cannot_read_only_turns_pushes_off(monkeypatch, tmp_path):
    monkeypatch.setenv("SP_APNS_KEY", "")
    monkeypatch.setenv("SP_APNS_KEY_PATH", str(tmp_path / "AuthKey_MISSING.p8"))
    monkeypatch.setenv("SP_APNS_KEY_ID", "ABC123DEFG")
    monkeypatch.setenv("SP_APNS_TEAM_ID", "K4Z2L5279D")

    settings = Settings.from_environment()

    assert settings.apns_key is None
    assert isinstance(notifier_from(settings), LoggedNotifier)


def test_a_phone_registers_for_pushes(client):
    assert client.put(f"/api/devices/{DEVICE}", json={"environment": "sandbox"}).status_code == 204
    assert client.put("/api/devices/not-a-token", json={}).status_code == 400
    assert client.delete(f"/api/devices/{DEVICE}").status_code == 204


def test_registering_needs_a_sign_in(make_client):
    with make_client(sign_in_as_owner=False) as anonymous:
        assert anonymous.put(f"/api/devices/{DEVICE}", json={}).status_code == 401


def test_the_first_results_send_one_push_and_a_recheck_stays_quiet(make_client):
    client = make_client(seed=True).__enter__()
    recorder = Recorder()
    client.app.state.worker.notifier = recorder
    drain(client)
    assert [push.title for _, push in recorder.sent] == ["Your shop is measured"]
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    scenario = client.get(f"/api/scans/{scan_id}/scenario").json()
    client.put(f"/api/scans/{scan_id}/scenario", json=scenario)
    drain(client)
    assert len(recorder.sent) == 1


def test_a_checked_photo_tells_the_owner_what_it_showed(make_client):
    client = make_client(seed=True, team_emails=frozenset({"demo@standardphysics.app"})).__enter__()
    drain(client)
    recorder = Recorder()
    client.app.state.notifier = recorder
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    client.put(f"/api/scans/{scan_id}/requests/door_hardware/photo", content=_jpeg())
    client.put(f"/api/team/reviews/{scan_id}/door_hardware", json={"outcome": "passes"})
    assert [(push.title, push.body) for _, push in recorder.sent] == [
        ("We checked your photo", "The front door handle works with a closed fist"),
    ]


def _age(client, scan_id, days):
    then = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with client.app.state.database.connect() as connection:
        connection.execute("UPDATE scans SET created_at = ?, last_opened_at = ? WHERE id = ?", (then, then, scan_id))


def test_a_guest_is_reminded_once_and_then_its_shops_are_deleted(make_client):
    with make_client(sign_in_as_owner=False) as phone:
        phone.post("/api/auth/guest")
        scan_id = create_scan(phone)
        app = phone.app
        recorder = Recorder()
        _age(phone, scan_id, 28)
        guest_sweep.sweep(app.state.database, app.state.store, recorder, datetime.now(UTC))
        guest_sweep.sweep(app.state.database, app.state.store, recorder, datetime.now(UTC))
        assert [push.title for _, push in recorder.sent] == ["Save your shop to keep it"]
        _age(phone, scan_id, 31)
        guest_sweep.sweep(app.state.database, app.state.store, recorder, datetime.now(UTC))
        assert phone.get("/api/auth/session").status_code == 401


def test_a_saved_account_is_never_swept(client):
    scan_id = create_scan(client)
    _age(client, scan_id, 90)
    guest_sweep.sweep(client.app.state.database, client.app.state.store, Recorder(), datetime.now(UTC))
    assert client.get(f"/api/scans/{scan_id}").status_code == 200


def test_a_guest_phone_keeps_getting_pushes_after_it_signs_in(client, make_client):
    owner_id = client.get("/api/auth/session").json()["owner_id"]
    with make_client(sign_in_as_owner=False) as phone:
        phone.post("/api/auth/guest")
        phone.put(f"/api/devices/{DEVICE}", json={"environment": "production"})
        phone.post("/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        with phone.app.state.database.connect() as connection:
            row = connection.execute("SELECT owner_id FROM devices WHERE token = ?", (DEVICE,)).fetchone()
    assert row["owner_id"] == owner_id


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_apple_gets_a_signed_token_and_a_dead_device_is_forgotten(client):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    seen = []

    def apple(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        header, claims, signature = request.headers["authorization"].removeprefix("bearer ").split(".")
        raw = _b64decode(signature)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key.public_key().verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
        assert json.loads(_b64decode(header)) == {"alg": "ES256", "kid": "KEY123"}
        assert json.loads(_b64decode(claims))["iss"] == "TEAM123"
        return httpx.Response(410, json={"reason": "Unregistered"})

    notifier = ApnsNotifier(pem, "KEY123", "TEAM123", "com.standardphysics.capture",
                            client=httpx.Client(transport=httpx.MockTransport(apple)))
    client.put(f"/api/devices/{DEVICE}", json={"environment": "sandbox"})
    owner_id = client.get("/api/auth/session").json()["owner_id"]
    notifier.send(client.app.state.database, uuid.UUID(owner_id), Push(title="Hello", body="There"))
    assert str(seen[0].url) == f"https://api.sandbox.push.apple.com/3/device/{DEVICE}"
    assert seen[0].headers["apns-topic"] == "com.standardphysics.capture"
    assert json.loads(seen[0].content)["aps"]["alert"] == {"title": "Hello", "body": "There"}
    with client.app.state.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0
