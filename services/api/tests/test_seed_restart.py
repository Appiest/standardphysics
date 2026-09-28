import logging
import re
import secrets

from fastapi.testclient import TestClient

from conftest import no_blender_stages, sign_in
from standardphysics_api.app import create_app
from standardphysics_api.settings import Settings

CLAIMED_PASSWORD = re.compile(r"Sign in as (\S+) with password (\S+)")


def _server(data_dir) -> TestClient:
    """A server as `from_environment` builds it with SP_SEED_OWNER_PASSWORD unset: a new
    random password on every startup."""
    settings = Settings(data_dir=data_dir, seed_sample_shop=True, seed_owner_password=secrets.token_urlsafe(12))
    return TestClient(create_app(settings, no_blender_stages(), run_worker=False))


def _seed_messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if "sample shop seeded" in record.getMessage()]


def test_a_restart_never_logs_a_password_that_fails_to_sign_in(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="standardphysics_api.app")

    with _server(tmp_path / "var") as first:
        [first_message] = _seed_messages(caplog)
        email, password = CLAIMED_PASSWORD.search(first_message).groups()
        sign_in(first, email, password)

    caplog.clear()
    with _server(tmp_path / "var") as restarted:
        [restart_message] = _seed_messages(caplog)
        assert CLAIMED_PASSWORD.search(restart_message) is None
        assert email in restart_message
        sign_in(restarted, email, password)
