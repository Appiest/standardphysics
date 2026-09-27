"""One ledger for the night's Fireworks spend, and a watchdog that stops paid jobs before the limit.

Fireworks has no account spend cap, so every paid job registers the file where
it keeps its running estimate, and its process ID, under `<ledger>/jobs/`:

    python spend_watchdog.py register --ledger DIR --job sft-1 --file PROGRESS.json --key spend.estimated_dollars --pid 123
    nohup python spend_watchdog.py watch --ledger DIR --stop-at 45 &

`watch` sums every registered estimate each minute, writes `total.json` and a
line to `watchdog.log`, and at the stop line sends SIGTERM, then SIGKILL a
minute later, to every registered process still alive. A job's estimate keeps
counting after it exits, so a new job's budget is always the night's total.
`admit` is the launch gate: it exits non-zero unless the total plus the job's
pessimistic estimate stays under the admission line.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import sys
import time

GRACE_SECONDS = 60
POLL_SECONDS = 60


def _read(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _dig(document: dict, key: str) -> float:
    value: object = document
    for part in key.split("."):
        value = value.get(part, {}) if isinstance(value, dict) else {}
    return float(value) if isinstance(value, (int, float)) else 0.0


def jobs(ledger: pathlib.Path) -> list[dict]:
    return [_read(path) for path in sorted((ledger / "jobs").glob("*.json"))]


def job_dollars(job: dict) -> float:
    """A job's latest estimate, never below the highest one seen, so a rewritten file cannot shrink it."""
    return max(_dig(_read(pathlib.Path(job["file"])), job["key"]), float(job.get("floor", 0.0)))


def total(ledger: pathlib.Path) -> float:
    return round(sum(job_dollars(job) for job in jobs(ledger)), 4)


def register(ledger: pathlib.Path, job: str, file: pathlib.Path, key: str, pid: int | None) -> None:
    (ledger / "jobs").mkdir(parents=True, exist_ok=True)
    entry = {"job": job, "file": str(file.resolve()), "key": key, "pid": pid, "registered": time.time()}
    (ledger / "jobs" / f"{job}.json").write_text(json.dumps(entry))


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _signal_all(ledger: pathlib.Path, signum: int) -> list[int]:
    hit = []
    for job in jobs(ledger):
        pid = job.get("pid")
        if _alive(pid):
            os.kill(pid, signum)
            hit.append(pid)
    return hit


def _raise_floors(ledger: pathlib.Path) -> None:
    for path in sorted((ledger / "jobs").glob("*.json")):
        job = _read(path)
        if job:
            job["floor"] = job_dollars(job)
            path.write_text(json.dumps(job))


def _log(ledger: pathlib.Path, **fields) -> None:
    fields = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
    with (ledger / "watchdog.log").open("a") as handle:
        handle.write(json.dumps(fields) + "\n")


def check(ledger: pathlib.Path, stop_at: float) -> bool:
    """One watchdog pass; True once the stop line is reached and every paid job has been told to stop."""
    _raise_floors(ledger)
    spent = total(ledger)
    (ledger / "total.json").write_text(json.dumps({"estimated_dollars": spent, "stop_at": stop_at,
                                                   "checked_at": time.time()}))
    if spent < stop_at:
        _log(ledger, estimated_dollars=spent)
        return False
    terminated = _signal_all(ledger, signal.SIGTERM)
    _log(ledger, estimated_dollars=spent, action="SIGTERM", pids=terminated)
    time.sleep(GRACE_SECONDS)
    killed = _signal_all(ledger, signal.SIGKILL)
    _log(ledger, estimated_dollars=spent, action="SIGKILL", pids=killed)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("register", "watch", "admit", "total"))
    parser.add_argument("--ledger", type=pathlib.Path, required=True)
    parser.add_argument("--job")
    parser.add_argument("--file", type=pathlib.Path)
    parser.add_argument("--key", default="estimated_dollars")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--stop-at", type=float, default=45.0)
    parser.add_argument("--estimate", type=float, default=0.0)
    parser.add_argument("--admit-under", type=float, default=40.0)
    args = parser.parse_args()
    args.ledger.mkdir(parents=True, exist_ok=True)
    if args.command == "register":
        register(args.ledger, args.job, args.file, args.key, args.pid)
    elif args.command == "total":
        print(total(args.ledger))
    elif args.command == "admit":
        spent = total(args.ledger)
        admitted = spent + args.estimate <= args.admit_under
        print(json.dumps({"spent": spent, "estimate": args.estimate, "admitted": admitted}))
        sys.exit(0 if admitted else 4)
    else:
        while not check(args.ledger, args.stop_at):
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
