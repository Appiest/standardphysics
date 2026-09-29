"""deploy/digitalocean/monitor.sh, run against a stand-in API and webhook on a local port and a stand-in df.

One http.server plays both parts: it answers /health/ready and /health/details
the way each test sets them, and records every POST to /hook as the alert the
monitor sent.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MONITOR = REPO / "deploy/digitalocean/monitor.sh"
KIB_PER_GIB = 1 << 20
VOLUME_KIB = 100 * KIB_PER_GIB

FAKE_DF = """#!/usr/bin/env bash
available="$FAKE_DF_AVAILABLE"
[ "${@: -1}" = "${SP_MONITOR_SYSTEM_PATH:-}" ] && available="${FAKE_DF_SYSTEM_AVAILABLE:-$available}"
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/sda $FAKE_DF_SIZE 0 $available 0% ${@: -1}"
"""


class StandInServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), StandInHandler)
        self.ready_status = 200
        self.oldest_queued_job_seconds: int | None = None
        self.tracing: dict = {"active": False, "off_because": "WANDB_PROJECT is not set", "delivery_errors": 0}
        self.hook_status = 200
        self.alerts: list[tuple[dict[str, str], bytes]] = []


class StandInHandler(BaseHTTPRequestHandler):
    server: StandInServer

    def do_GET(self):
        if self.path == "/health/ready":
            self.answer(self.server.ready_status, {"status": "ready" if self.server.ready_status == 200 else "degraded"})
        elif self.path == "/health/details":
            self.answer(
                200, {"oldest_queued_job_seconds": self.server.oldest_queued_job_seconds, "tracing": self.server.tracing}
            )
        else:
            self.answer(404, {})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.server.hook_status == 200:
            self.server.alerts.append((dict(self.headers), body))
        self.answer(self.server.hook_status, {})

    def answer(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server():
    stand_in = StandInServer()
    thread = threading.Thread(target=stand_in.serve_forever, daemon=True)
    thread.start()
    yield stand_in
    stand_in.shutdown()
    stand_in.server_close()


class Monitor:
    def __init__(self, tmp_path: pathlib.Path, server: StandInServer):
        self.server = server
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        df = bin_dir / "df"
        df.write_text(FAKE_DF)
        df.chmod(0o755)
        (tmp_path / "scans").mkdir()
        (tmp_path / "system").mkdir()
        self.backups = tmp_path / "backups"
        url = f"http://127.0.0.1:{server.server_port}"
        self.environment = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "SP_ALERT_WEBHOOK": f"{url}/hook",
            "SP_ALERT_FORMAT": "",
            "SP_MONITOR_URL": url,
            "SP_MONITOR_STATE": str(tmp_path / "state/failing"),
            "SP_MONITOR_PYTHON": sys.executable,
            "SCANS_PATH": str(tmp_path / "scans"),
            "SP_MONITOR_SYSTEM_PATH": str(tmp_path / "system"),
            "SP_BACKUP_DEST": "",
            "SP_BACKUPS_NOT_WANTED": "1",
            "WANDB_API_KEY": "",
            "FAKE_DF_SIZE": str(VOLUME_KIB),
            "FAKE_DF_AVAILABLE": str(VOLUME_KIB // 2),
        }

    def run(self) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(MONITOR)], env=self.environment, capture_output=True, text=True, timeout=60)

    def run_ok(self) -> subprocess.CompletedProcess:
        result = self.run()
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    def alert_texts(self) -> list[str]:
        return [json.loads(body)["text"] for _, body in self.server.alerts]

    def take_snapshot(self, hours_ago: float) -> None:
        taken = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
        (self.backups / taken.strftime("%Y-%m-%dT%H%M%SZ")).mkdir(parents=True)
        self.environment["SP_BACKUP_DEST"] = str(self.backups)
        self.environment["SP_BACKUPS_NOT_WANTED"] = ""


@pytest.fixture
def monitor(tmp_path, server) -> Monitor:
    return Monitor(tmp_path, server)


def test_one_outage_sends_one_alert_and_its_recovery_sends_one_more(monitor, server):
    monitor.run_ok()
    assert monitor.alert_texts() == []

    server.ready_status = 503
    for _ in range(3):
        monitor.run_ok()
    assert len(monitor.alert_texts()) == 1
    assert "Failing: readiness" in monitor.alert_texts()[0]
    assert "answered 503" in monitor.alert_texts()[0]

    server.ready_status = 200
    for _ in range(2):
        monitor.run_ok()
    alerts = monitor.alert_texts()
    assert len(alerts) == 2
    assert "Recovered: readiness" in alerts[1]
    assert "Every check passes now." in alerts[1]


def test_nothing_is_sent_or_checked_until_a_webhook_is_set(monitor, server):
    monitor.environment["SP_ALERT_WEBHOOK"] = ""
    server.ready_status = 503
    result = monitor.run_ok()
    assert "Monitoring is off" in result.stdout
    assert server.alerts == []


def test_a_stuck_queue_and_a_full_disk_arrive_in_one_message(monitor, server):
    server.oldest_queued_job_seconds = 4000
    monitor.environment["FAKE_DF_AVAILABLE"] = str(VOLUME_KIB // 20)
    monitor.run_ok()
    (alert,) = monitor.alert_texts()
    assert "Failing: queue: the oldest queued job has waited 4000s" in alert
    assert "Failing: disk:" in alert and "5% free" in alert


def test_a_backup_older_than_the_limit_is_reported_and_a_fresh_one_is_not(monitor):
    monitor.take_snapshot(hours_ago=2)
    monitor.run_ok()
    assert monitor.alert_texts() == []

    for stale in monitor.backups.iterdir():
        stale.rmdir()
    monitor.take_snapshot(hours_ago=50)
    monitor.run_ok()
    (alert,) = monitor.alert_texts()
    assert "Failing: backup:" in alert and "over the 26-hour limit" in alert


def test_a_box_with_no_backup_destination_fails_unless_it_says_it_wants_none(monitor):
    monitor.environment["SP_BACKUPS_NOT_WANTED"] = ""
    monitor.run_ok()
    (alert,) = monitor.alert_texts()
    assert "Failing: backup: SP_BACKUP_DEST is not set" in alert

    monitor.environment["SP_BACKUPS_NOT_WANTED"] = "1"
    monitor.run_ok()
    assert "Recovered: backup" in monitor.alert_texts()[1]


def test_tracing_that_is_off_is_reported_only_when_a_wandb_key_is_set(monitor):
    monitor.run_ok()
    assert monitor.alert_texts() == []

    monitor.environment["WANDB_API_KEY"] = "a-key"
    monitor.run_ok()
    (alert,) = monitor.alert_texts()
    assert "Failing: tracing: WANDB_API_KEY is set but tracing is off: WANDB_PROJECT is not set" in alert


def test_traces_weave_failed_to_deliver_are_reported(monitor, server):
    monitor.environment["WANDB_API_KEY"] = "a-key"
    server.tracing = {"active": True, "off_because": None, "delivery_errors": 3, "last_delivery_error": "batch dropped"}
    monitor.run_ok()
    (alert,) = monitor.alert_texts()
    assert "Failing: tracing: Weave failed to deliver traces 3 time(s); the last: batch dropped" in alert


def test_an_ntfy_topic_gets_plain_text_with_a_title(monitor, server):
    monitor.environment["SP_ALERT_FORMAT"] = "ntfy"
    server.ready_status = 503
    monitor.run_ok()
    ((headers, body),) = server.alerts
    assert headers["Title"] == "Standard Physics monitor"
    assert headers["Priority"] == "high"
    assert body.decode().startswith("Standard Physics at ")
    assert "Failing: readiness" in body.decode()


def test_an_alert_the_webhook_refused_is_sent_again_on_the_next_run(monitor, server):
    server.ready_status = 503
    server.hook_status = 500
    result = monitor.run()
    assert result.returncode == 1
    assert "Could not deliver" in result.stderr

    server.hook_status = 200
    monitor.run_ok()
    assert len(monitor.alert_texts()) == 1


def test_a_full_system_disk_is_reported_while_the_scans_volume_has_room(monitor):
    monitor.environment["FAKE_DF_SYSTEM_AVAILABLE"] = str(VOLUME_KIB * 4 // 100)
    monitor.run_ok()
    (alert,) = monitor.alert_texts()
    assert "Failing: system_disk:" in alert and "4% free" in alert
    assert "Failing: disk:" not in alert
