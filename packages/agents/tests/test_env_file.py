"""The .env loader the API and the evaluation command lines share."""

import os

from standardphysics_agents.env_file import REPO_ENV_FILE, load_dotenv
from standardphysics_agents.evaluation.scan_campaign import parse_arguments

REQUIRED = ["--graph", "g.json", "--mesh", "m.json", "--tasks", "t.json", "--out", "o.json"]


def test_keys_load_without_overriding_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# a comment\nSP_TEST_NEW="fresh"\nSP_TEST_SET=from-file\nnot a pair\n')
    monkeypatch.setenv("SP_TEST_NEW", "restored after the test")
    monkeypatch.delenv("SP_TEST_NEW")
    monkeypatch.setenv("SP_TEST_SET", "from-shell")

    load_dotenv(env)

    assert os.environ["SP_TEST_NEW"] == "fresh"
    assert os.environ["SP_TEST_SET"] == "from-shell"


def test_a_missing_file_loads_nothing(tmp_path):
    load_dotenv(tmp_path / "absent.env")


def test_the_repo_env_file_sits_at_the_repo_root():
    assert (REPO_ENV_FILE.parent / "packages" / "agents" / "pyproject.toml").exists()


def test_the_scan_campaign_reads_the_repo_env_file_unless_told_otherwise(tmp_path):
    assert parse_arguments(REQUIRED).env_file == REPO_ENV_FILE
    chosen = tmp_path / "keys.env"
    assert parse_arguments([*REQUIRED, "--env-file", str(chosen)]).env_file == chosen
