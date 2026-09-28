import logging
import stat

from conftest import SEED_OWNER_PASSWORD


def test_a_chosen_demo_password_stays_out_of_the_log(make_client, caplog):
    with caplog.at_level(logging.WARNING):
        make_client(seed=True)
    assert "sample shop seeded" in caplog.text
    assert SEED_OWNER_PASSWORD not in caplog.text


def test_a_generated_demo_password_goes_to_a_private_file(make_client, tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        make_client(seed=True, seed_owner_password_generated=True)
    password_file = tmp_path / "var" / "demo-password"
    assert password_file.read_text().strip() == SEED_OWNER_PASSWORD
    assert stat.S_IMODE(password_file.stat().st_mode) == 0o600
    assert SEED_OWNER_PASSWORD not in caplog.text
    assert str(password_file) in caplog.text
