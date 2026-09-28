import csv
import io


def test_signup_collects_invite_address_without_an_account(make_client):
    with make_client(sign_in_as_owner=False, waitlist_admin_token="a-secret-token") as client:
        assert client.post("/api/waitlist", json={"email": " Student@Example.com ", "role": "student"}).status_code == 202
        assert client.post("/api/waitlist", json={"email": "student@example.com", "role": "shop_owner"}).status_code == 202
        response = client.get("/api/waitlist.csv", headers={"Authorization": "Bearer a-secret-token"})

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["email"] == "student@example.com"
    assert rows[0]["role"] == "shop_owner"
    assert rows[0]["joined_at"]
    assert response.headers["cache-control"] == "no-store"


def test_signup_rejects_invalid_email_and_role(make_client):
    with make_client(sign_in_as_owner=False) as client:
        assert client.post("/api/waitlist", json={"email": "not-an-email", "role": "student"}).status_code == 400
        assert client.post("/api/waitlist", json={"email": "x@example.com", "role": "admin"}).status_code == 400


def test_only_admin_token_can_read_invite_addresses(make_client):
    with make_client(sign_in_as_owner=False, waitlist_admin_token="a-secret-token") as client:
        assert client.post("/api/waitlist", json={"email": "x@example.com", "role": "student"}).status_code == 202
        assert client.get("/api/waitlist.csv").status_code == 401
        assert client.get("/api/waitlist.csv", headers={"Authorization": "Bearer wrong"}).status_code == 401

    with make_client(sign_in_as_owner=False) as client:
        assert client.get("/api/waitlist.csv").status_code == 404
