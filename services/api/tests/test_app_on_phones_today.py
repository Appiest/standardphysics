"""The iPhone build already on phones (1.0, build 6) keeps working against this server.

That build signs in by email, keeps the sp_session cookie's value as a bearer
token and sends nothing but that header afterwards. It decodes `email` and
`shop_name` from the session, and only `id` and `state` from a scan.
"""

from conftest import OWNER_EMAIL, OWNER_PASSWORD, put_artifact, sign_up, usdz_fixture

STATES_THE_OLD_APP_KNOWS = {"uploading", "measuring", "checking", "ready", "failed"}


def _signed_in_like_the_old_app(test_client) -> dict[str, str]:
    response = test_client.post("/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert response.status_code == 200
    session = response.json()
    assert isinstance(session["email"], str) and isinstance(session["shop_name"], str)
    token = response.cookies.get("sp_session")
    assert token
    test_client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


def _assert_old_scan(body: dict, scan_id: str | None = None) -> str:
    assert body["state"] in STATES_THE_OLD_APP_KNOWS
    if scan_id is not None:
        assert body["id"] == scan_id
    return body["id"]


def test_the_old_app_walks_uploads_and_deletes_with_a_team_named(make_client):
    with make_client(sign_in_as_owner=False, team_emails=frozenset({"team@example.com"})) as test_client:
        sign_up(test_client)
        test_client.cookies.clear()
        bearer = _signed_in_like_the_old_app(test_client)

        created = test_client.post(
            "/api/scans",
            json={"name": "Corner cafe", "device_model": "iPhone17,1", "duration_seconds": 142.5},
            headers=bearer,
        )
        assert created.status_code == 201
        scan_id = _assert_old_scan(created.json())

        test_client.headers.update(bearer)
        assert put_artifact(test_client, scan_id, "room-json", b'{"walls": []}', "room_json").status_code in (200, 201)
        assert put_artifact(test_client, scan_id, "room-usdz", usdz_fixture(), "room_usdz").status_code in (200, 201)

        completed = test_client.post(f"/api/scans/{scan_id}/complete")
        assert completed.status_code == 200
        _assert_old_scan(completed.json(), scan_id)

        fetched = test_client.get(f"/api/scans/{scan_id}")
        assert fetched.status_code == 200
        _assert_old_scan(fetched.json(), scan_id)

        assert test_client.delete(f"/api/scans/{scan_id}").status_code == 204
        assert test_client.delete("/api/account").status_code == 204
