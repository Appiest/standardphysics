"""scripts/deploy.sh, run against stand-ins for ssh, git and docker so nothing leaves this machine.

The stand-in ssh runs the command it is handed in a local bash, which is the
same text the Droplet would run, so these tests read the real remote script.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = REPO / "scripts/deploy.sh"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

STAND_INS = {
    "ssh": 'exec bash -c "${@: -1}"\n',
    "flock": "exit 0\n",
    "git": """echo "git $*" >> "$STAND_IN_LOG"
case "$1" in
  rev-parse) echo "$FAKE_COMMIT" ;;
  rev-list) echo 0 ;;
  remote) echo https://example.invalid/standardphysics.git ;;
esac
""",
    "docker": """echo "docker $* GIT_SHA=${GIT_SHA:-}" >> "$STAND_IN_LOG"
if [ "$2" = exec ]; then
  printf '%s' "${@: -1}" > "$STAND_IN_QUERY"
  [ -n "${FAKE_IN_FLIGHT_FAILS:-}" ] && exit 1
  echo "$FAKE_IN_FLIGHT"
fi
""",
}


@pytest.fixture
def box(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STAND_INS.items():
        stand_in = bin_dir / name
        stand_in.write_text("#!/usr/bin/env bash\n" + body)
        stand_in.chmod(0o755)
    deploy_dir = tmp_path / "standardphysics/deploy/digitalocean"
    deploy_dir.mkdir(parents=True)
    doctor = deploy_dir / "doctor.sh"
    doctor.write_text("#!/usr/bin/env bash\nexit 0\n")
    doctor.chmod(0o755)
    return tmp_path


def deploy(box: pathlib.Path, in_flight: int = 0, **environment: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{box / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "STAND_IN_LOG": str(box / "calls.log"),
        "STAND_IN_QUERY": str(box / "query.py"),
        "FAKE_COMMIT": COMMIT,
        "FAKE_IN_FLIGHT": str(in_flight),
        "SP_DEPLOY_DIR": str(box / "standardphysics"),
        "SP_DEPLOY_LOCK": str(box / "deploy.lock"),
        "SP_DEPLOY_HISTORY": str(box / "deploys.log"),
        **environment,
    }
    return subprocess.run(["bash", str(DEPLOY)], env=env, capture_output=True, text=True, timeout=60)


def calls(box: pathlib.Path) -> list[str]:
    return (box / "calls.log").read_text().splitlines()


def compose_up(box: pathlib.Path) -> list[str]:
    return [line for line in calls(box) if line.startswith("docker compose up")]


def test_the_image_is_built_for_the_commit_it_pulled_and_the_deploy_is_written_down(box):
    result = deploy(box)
    assert result.returncode == 0, result.stderr
    assert compose_up(box) == [f"docker compose up -d --build GIT_SHA={COMMIT}"]
    assert (box / "deploys.log").read_text().split()[1] == COMMIT


def test_a_box_left_on_an_old_commit_by_a_rollback_returns_to_master_before_pulling(box):
    assert deploy(box).returncode == 0
    git = [line for line in calls(box) if line.startswith("git checkout") or line.startswith("git pull")]
    assert git == ["git checkout --quiet master", "git pull --ff-only"]


def test_a_deploy_waits_for_queued_and_running_jobs(box):
    result = deploy(box, in_flight=2)
    assert result.returncode == 75
    assert "2 job(s)" in result.stderr
    assert compose_up(box) == []
    assert not any(line.startswith("git pull") for line in calls(box))


def test_the_container_is_asked_a_query_that_counts_only_unfinished_jobs(box):
    deploy(box, in_flight=0)
    database = box / "standardphysics.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (state TEXT)")
        connection.executemany("INSERT INTO jobs VALUES (?)", [("queued",), ("running",), ("done",), ("failed",)])
    query = (box / "query.py").read_text().replace("/data/standardphysics.sqlite3", str(database))
    counted = subprocess.run([sys.executable, "-c", query], capture_output=True, text=True, check=True)
    assert counted.stdout.strip() == "2"


def test_forcing_a_deploy_goes_ahead_with_jobs_in_flight(box):
    result = deploy(box, in_flight=2, SP_DEPLOY_FORCE="1")
    assert result.returncode == 0, result.stderr
    assert len(compose_up(box)) == 1


def test_a_stopped_api_has_no_jobs_to_interrupt(box):
    result = deploy(box, FAKE_IN_FLIGHT_FAILS="1")
    assert result.returncode == 0, result.stderr
    assert len(compose_up(box)) == 1
