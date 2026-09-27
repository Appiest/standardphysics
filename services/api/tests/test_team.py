"""The team is a role someone with the server's shell hands out, never an email anyone can type.

An allowlisted address used to be enough: sign up with it and you were the
team. Sign-up doesn't confirm an email, so these tests keep that door shut
while the accounts the team already had keep working across the change.
"""

import sqlite3

from conftest import OWNER_EMAIL, OWNER_PASSWORD, create_scan, sign_in, sign_up
from standardphysics_api import accounts, team

TEAM_EMAIL = "builder@standardphysics.app"
UNCLAIMED_TEAM_EMAIL = "unclaimed@standardphysics.app"
ASK = {"text": "hi", "base_revision": 0}


def _database_from_before_the_team_role(tmp_path) -> None:
    """An owners table as the server wrote it before `team` existed, holding one real team account."""
    path = tmp_path / "var" / "standardphysics.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE owners (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            shop_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            guest INTEGER NOT NULL DEFAULT 0,
            apple_sub TEXT,
            reminded_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO owners (id, email, shop_name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        ("7d0b3a9e-4a55-4c0e-9c1f-2f4b8f1c0a11", TEAM_EMAIL, "Team", accounts.hash_password(OWNER_PASSWORD),
         "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()


def _asks(test_client) -> int:
    return test_client.post(f"/api/scans/{create_scan(test_client)}/ask", json=ASK).status_code


def test_a_team_account_from_before_the_role_keeps_the_team_tools(make_client, tmp_path):
    _database_from_before_the_team_role(tmp_path)
    allowlist = frozenset({TEAM_EMAIL, UNCLAIMED_TEAM_EMAIL})
    with make_client(sign_in_as_owner=False, team_emails=allowlist) as browser:
        sign_in(browser, TEAM_EMAIL, OWNER_PASSWORD)
        assert browser.get("/api/auth/session").json()["role"] == "team"
        assert _asks(browser) != 403


def test_an_allowlisted_email_signed_up_after_the_migration_is_not_the_team(make_client, tmp_path):
    _database_from_before_the_team_role(tmp_path)
    allowlist = frozenset({TEAM_EMAIL, UNCLAIMED_TEAM_EMAIL})
    with make_client(sign_in_as_owner=False, team_emails=allowlist) as browser:
        sign_up(browser, UNCLAIMED_TEAM_EMAIL)
        assert browser.get("/api/auth/session").json()["role"] == "owner"
        assert _asks(browser) == 403
    with make_client(sign_in_as_owner=False, team_emails=allowlist) as after_a_restart:
        sign_in(after_a_restart, UNCLAIMED_TEAM_EMAIL, OWNER_PASSWORD)
        assert after_a_restart.get("/api/auth/session").json()["role"] == "owner"


def test_a_granted_account_is_the_team_and_a_revoked_one_is_not(make_client):
    with make_client() as browser:
        database = browser.app.state.database
        with database.transaction() as connection:
            assert team.grant(connection, OWNER_EMAIL)
        assert browser.get("/api/auth/session").json()["role"] == "team"
        assert _asks(browser) != 403
        with database.transaction() as connection:
            assert team.revoke(connection, OWNER_EMAIL)
        assert browser.get("/api/auth/session").json()["role"] == "owner"
        assert _asks(browser) == 403


def test_granting_an_email_with_no_account_changes_nothing(client):
    with client.app.state.database.transaction() as connection:
        assert not team.grant(connection, "nobody@example.com")
        assert team.members(connection) == []


def test_the_command_line_grants_lists_and_revokes(make_client, tmp_path, monkeypatch, capsys):
    with make_client():
        pass
    monkeypatch.setenv("SP_DATA_DIR", str(tmp_path / "var"))
    assert team.main(["grant", OWNER_EMAIL]) == 0
    assert team.main(["list"]) == 0
    assert OWNER_EMAIL in capsys.readouterr().out
    assert team.main(["revoke", OWNER_EMAIL]) == 0
    assert team.main(["revoke", "nobody@example.com"]) == 1
