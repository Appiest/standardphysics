import logging
import secrets

from fastapi.testclient import TestClient

from conftest import no_blender_stages, sign_in
from standardphysics_api.app import DEMO_PASSWORD_FILE, create_app
from standardphysics_api.settings import Settings


def _server(data_dir, password: str) -> TestClient:
    """A server as `from_environment` builds it with SP_SEED_OWNER_PASSWORD unset: a new
    random password on every startup."""
    settings = Settings(
        data_dir=data_dir, seed_sample_shop=True, seed_owner_password=password, seed_owner_password_generated=True
    )
    return TestClient(create_app(settings, no_blender_stages(), run_worker=False))


def test_a_restart_keeps_the_password_file_pointing_at_a_password_that_signs_in(tmp_path, caplog):
    data_dir = tmp_path / "var"
    first_password, restart_password = secrets.token_urlsafe(12), secrets.token_urlsafe(12)
    caplog.set_level(logging.WARNING, logger="standardphysics_api.app")

    with _server(data_dir, first_password) as first:
        written = (data_dir / DEMO_PASSWORD_FILE).read_text().strip()
        sign_in(first, "demo@standardphysics.app", written)

    with _server(data_dir, restart_password) as restarted:
        assert (data_dir / DEMO_PASSWORD_FILE).read_text().strip() == written
        sign_in(restarted, "demo@standardphysics.app", written)

    assert first_password not in caplog.text
    assert restart_password not in caplog.text
    assert "it was created with" in caplog.text
