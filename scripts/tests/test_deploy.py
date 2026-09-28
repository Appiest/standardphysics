"""scripts/deploy.sh, run against stand-ins for ssh, git and docker so nothing leaves this machine.

The stand-in ssh runs the command it is handed in a local bash, which is the
same text the Droplet would run, so these tests read the real remote script.
The stand-in docker runs the real check_serving.py against a local server
playing the API and the workspace, so a deploy is judged by what they answer.
"""

from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = REPO / "scripts/deploy.sh"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PUBLISHED = f"ghcr.io/imhaohao/standardphysics:{COMMIT}"
DIGEST = "ghcr.io/imhaohao/standardphysics@sha256:" + "f" * 64
PREVIOUS = "fedcba9876543210fedcba9876543210fedcba98"
CHECK_SERVING = REPO / "deploy/digitalocean/check_serving.py"

STAND_INS = {
    "ssh": 'exec bash -c "${@: -1}"\n',
    "flock": "exit 0\n",
    "sleep": "exit 0\n",
    "git": """echo "git $*" >> "$STAND_IN_LOG"
case "$1" in
  rev-parse) echo "$FAKE_COMMIT" ;;
  rev-list) echo 0 ;;
  remote) echo https://example.invalid/standardphysics.git ;;
esac
""",
    "docker": """echo "docker $* GIT_SHA=${GIT_SHA:-}" >> "$STAND_IN_LOG"
[ "$1" = pull ] && [ -z "${FAKE_PUBLISHED:-}" ] && exit 1
[ "$1" = image ] && echo "$FAKE_DIGEST"
if [ "$2" = exec ] && [ "${@: -2:1}" = - ]; then
  exec "$STAND_IN_PYTHON" - "${@: -1}" "$FAKE_ORIGIN" "$FAKE_ORIGIN"
fi
if [ "$2" = exec ]; then
  printf '%s' "${@: -1}" > "$STAND_IN_QUERY"
  [ -n "${FAKE_IN_FLIGHT_FAILS:-}" ] && exit 1
  echo "$FAKE_IN_FLIGHT"
fi
""",
}


class FakeStack(http.server.BaseHTTPRequestHandler):
    """The API's two health routes and the workspace's sign-in page, each answering
    from `answers` with the number of requests the server has had so far."""

    answers: dict = {}

    def do_GET(self):
        self.server.asked.append(self.path)
        status, body = self.answers[self.path](len(self.server.asked))
        self.send_response(status)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


def healthy_answers(commit: str = COMMIT) -> dict:
    return {
        "/health/ready": lambda asked: (200, {"status": "ready", "problems": []}),
        "/health/details": lambda asked: (200, {"commit": commit}),
        "/sign-in": lambda asked: (200, {}),
    }


@pytest.fixture(autouse=True)
def stack(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeStack)
    server.asked = []
    FakeStack.answers = healthy_answers()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("FAKE_ORIGIN", f"http://127.0.0.1:{server.server_port}")
    yield server
    server.shutdown()
    server.server_close()


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
    shutil.copy(CHECK_SERVING, deploy_dir / CHECK_SERVING.name)
    return tmp_path


def deploy(box: pathlib.Path, in_flight: int = 0, **environment: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{box / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "STAND_IN_LOG": str(box / "calls.log"),
        "STAND_IN_QUERY": str(box / "query.py"),
        "STAND_IN_PYTHON": sys.executable,
        "FAKE_COMMIT": COMMIT,
        "FAKE_IN_FLIGHT": str(in_flight),
        "FAKE_DIGEST": DIGEST,
        "FAKE_PUBLISHED": "1",
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
    assert compose_up(box) == [f"docker compose up -d GIT_SHA={COMMIT}"]
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
    assert not (box / "deploys.log").exists()


def test_the_queue_is_read_after_the_image_is_fetched_and_just_before_the_restart(box):
    """A pull or a build takes minutes. Reading the queue before it would leave
    all of them for an upload to start a job that the restart then kills."""
    assert deploy(box).returncode == 0
    steps = [" ".join(line.split()[1:3]) for line in calls(box) if line.startswith("docker")]
    fetched, queue_read, restarted = (steps.index(step) for step in ("pull --quiet", "compose exec", "compose up"))
    assert fetched < queue_read < restarted


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


def test_a_queue_that_cannot_be_read_is_not_an_empty_one(box):
    result = deploy(box, FAKE_IN_FLIGHT_FAILS="1")
    assert result.returncode == 69
    assert "could not read the job queue" in result.stderr
    assert compose_up(box) == []


def test_a_queue_query_that_answers_nonsense_is_refused_too(box):
    result = deploy(box, FAKE_IN_FLIGHT="Traceback (most recent call last):")
    assert result.returncode == 69
    assert compose_up(box) == []


def test_forcing_a_deploy_goes_ahead_when_the_queue_cannot_be_read(box):
    result = deploy(box, FAKE_IN_FLIGHT_FAILS="1", SP_DEPLOY_FORCE="1")
    assert result.returncode == 0, result.stderr
    assert len(compose_up(box)) == 1


def test_the_image_ci_tested_is_pulled_and_tagged_instead_of_rebuilt(box):
    result = deploy(box)
    assert result.returncode == 0, result.stderr
    docker = [line.removesuffix(f" GIT_SHA={COMMIT}") for line in calls(box) if line.startswith("docker")]
    assert f"docker pull --quiet {PUBLISHED}" in docker
    assert f"docker tag {PUBLISHED} standardphysics:{COMMIT}" in docker
    assert not any(line.startswith("docker compose build") for line in docker)


def test_a_promoted_deploy_writes_down_the_digest_it_started(box):
    assert deploy(box).returncode == 0
    assert (box / "deploys.log").read_text().split()[1:] == [COMMIT, DIGEST]


def test_a_commit_without_a_tested_image_is_refused_rather_than_built(box):
    """CI publishes a commit's image only after every check passes, so a build
    on the box would ship something nothing has tested."""
    result = deploy(box, FAKE_PUBLISHED="")
    assert result.returncode == 66
    assert PUBLISHED in result.stderr and "SP_DEPLOY_BUILD=1" in result.stderr
    assert not any(line.startswith("docker compose build") for line in calls(box))
    assert compose_up(box) == []
    assert not (box / "deploys.log").exists()


def test_asking_for_a_build_never_pulls_and_says_the_image_is_untested(box):
    result = deploy(box, SP_DEPLOY_BUILD="1")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("docker pull") for line in calls(box))
    assert any(line.startswith("docker compose build") for line in calls(box))
    assert "untested" in result.stderr
    assert (box / "deploys.log").read_text().split()[1:] == [COMMIT, "untested-local-build"]


def test_the_deploy_is_written_down_only_once_the_new_commit_is_serving(stack, box):
    result = deploy(box)
    assert result.returncode == 0, result.stderr
    assert stack.asked == ["/health/ready", "/health/details", "/sign-in"]
    assert f"serving {COMMIT}" in result.stdout


def test_a_stack_that_becomes_ready_on_a_later_look_is_a_success(box):
    FakeStack.answers["/health/ready"] = lambda asked: (503, {"status": "degraded"}) if asked < 3 else (200, {})
    result = deploy(box, SP_DEPLOY_READY_SECONDS="30")
    assert result.returncode == 0, result.stderr
    assert (box / "deploys.log").read_text().split()[1] == COMMIT


def rollback_named(result: subprocess.CompletedProcess, commit: str) -> bool:
    return f"checkout {commit}" in result.stderr and f"GIT_SHA={commit} docker compose up -d" in result.stderr


def a_failed_deploy(box: pathlib.Path) -> subprocess.CompletedProcess:
    earlier = f"2026-09-01T00:00:00Z {PREVIOUS} {DIGEST}"
    (box / "deploys.log").write_text(earlier + "\n")
    result = deploy(box, SP_DEPLOY_READY_SECONDS="10")
    assert result.returncode == 70
    assert (box / "deploys.log").read_text().splitlines() == [earlier]
    assert rollback_named(result, PREVIOUS)
    return result


def test_an_api_that_never_gets_ready_is_not_written_down_and_names_the_rollback(box):
    FakeStack.answers["/health/ready"] = lambda asked: (503, {"status": "degraded", "problems": ["loop stalled"]})
    result = a_failed_deploy(box)
    assert "503" in result.stderr and "loop stalled" in result.stderr


def test_an_api_serving_another_commit_is_not_written_down(box):
    FakeStack.answers = healthy_answers(commit=PREVIOUS)
    result = a_failed_deploy(box)
    assert f"serving commit {PREVIOUS}, not {COMMIT}" in result.stderr


def test_a_workspace_that_does_not_answer_is_not_written_down(box):
    FakeStack.answers["/sign-in"] = lambda asked: (502, {})
    result = a_failed_deploy(box)
    assert "/sign-in answered 502" in result.stderr


def test_the_rollback_named_comes_from_a_history_that_was_just_rotated(box):
    FakeStack.answers["/health/ready"] = lambda asked: (503, {})
    (box / "deploys.log.1").write_text(f"2026-09-01T00:00:00Z {PREVIOUS} {DIGEST}\n")
    result = deploy(box, SP_DEPLOY_READY_SECONDS="0")
    assert result.returncode == 70
    assert rollback_named(result, PREVIOUS)


def test_a_failure_with_no_earlier_deploy_says_there_is_nothing_to_roll_back_to(box):
    FakeStack.answers["/health/ready"] = lambda asked: (503, {})
    result = deploy(box, SP_DEPLOY_READY_SECONDS="0")
    assert result.returncode == 70
    assert "No earlier deploy" in result.stderr
    assert not (box / "deploys.log").exists()
