import json
import subprocess
import sys

import spend_watchdog as watchdog


def _job(ledger, tmp_path, name: str, dollars: float, pid=None):
    file = tmp_path / f"{name}.json"
    file.write_text(json.dumps({"spend": {"estimated_dollars": dollars}}))
    watchdog.register(ledger, name, file, "spend.estimated_dollars", pid)
    return file


def test_total_sums_every_registered_estimate(tmp_path):
    ledger = tmp_path / "ledger"
    _job(ledger, tmp_path, "eval", 1.5)
    _job(ledger, tmp_path, "sft", 10.25)
    assert watchdog.total(ledger) == 11.75


def test_a_rewritten_estimate_cannot_shrink_what_was_already_counted(tmp_path):
    ledger = tmp_path / "ledger"
    file = _job(ledger, tmp_path, "sft", 12.0)
    assert watchdog.check(ledger, stop_at=45) is False
    file.write_text(json.dumps({"spend": {"estimated_dollars": 0.0}}))
    assert watchdog.total(ledger) == 12.0


def test_stop_line_terminates_registered_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "GRACE_SECONDS", 0)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    ledger = tmp_path / "ledger"
    _job(ledger, tmp_path, "rl", 46.0, pid=child.pid)
    assert watchdog.check(ledger, stop_at=45) is True
    assert child.wait(timeout=10) != 0
    log = [json.loads(line) for line in (ledger / "watchdog.log").read_text().splitlines()]
    assert log[0]["action"] == "SIGTERM" and log[0]["pids"] == [child.pid]
