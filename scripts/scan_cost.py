#!/usr/bin/env python3
"""What one scan costs in model calls, measured rather than estimated.

OpenRouter reports what an account has actually spent. This reads that number,
runs the work you name, and reads it again, so the figure is the provider's
own accounting and not a guess from token counts and a price list that may be
out of date.

    scripts/scan_cost.py -- .venv/bin/python scripts/import_scan.py datasets/phone/shop-01

The one thing it assumes is that nothing else on the same OpenRouter key is
running while it measures. Anything else spending against that key lands in
the total, so measure on a quiet key, and prefer a key made for this.

Without a command it prints the balance and exits, which is the way to check
the key works before a shop is standing in front of you.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

CREDITS_URL = "https://openrouter.ai/api/v1/credits"
TIMEOUT_SECONDS = 30
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class CreditsUnavailable(RuntimeError):
    pass


def load_key() -> str:
    """The key from the environment, falling back to the repo-root .env."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            name, _, value = line.strip().partition("=")
            if name == "OPENROUTER_API_KEY" and value:
                return value.strip().strip('"').strip("'")
    raise CreditsUnavailable("set OPENROUTER_API_KEY, or put it in .env")


def spent_so_far(key: str) -> float:
    """Total dollars this key's account has ever used."""
    request = urllib.request.Request(CREDITS_URL, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise CreditsUnavailable(f"OpenRouter answered {error.code}: check the key") from error
    except urllib.error.URLError as error:
        raise CreditsUnavailable(f"could not reach OpenRouter: {error.reason}") from error
    try:
        return float(payload["data"]["total_usage"])
    except (KeyError, TypeError, ValueError) as error:
        raise CreditsUnavailable(f"unexpected answer from OpenRouter: {payload}") from error


def remaining(key: str) -> float | None:
    request = urllib.request.Request(CREDITS_URL, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.loads(response.read())["data"]
        return float(data["total_credits"]) - float(data["total_usage"])
    except (urllib.error.URLError, KeyError, TypeError, ValueError):
        return None


def report(cost: float, seconds: float) -> None:
    print()
    print(f"  model spend   ${cost:.4f}")
    print(f"  wall clock    {seconds:.1f}s")
    if cost > 0:
        print(f"  100 scans     ${cost * 100:.2f}")
    else:
        print("  nothing was spent: no model ran, or the key is not the one it used")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="the work to measure, after --")
    arguments = parser.parse_args()

    try:
        key = load_key()
    except CreditsUnavailable as error:
        print(f"{error}", file=sys.stderr)
        return 2

    command = [word for word in arguments.command if word != "--"]
    if not command:
        try:
            left = remaining(key)
        except CreditsUnavailable as error:
            print(f"{error}", file=sys.stderr)
            return 2
        print(f"${left:.2f} left on this key" if left is not None else "key works, balance unreadable")
        return 0

    try:
        before = spent_so_far(key)
    except CreditsUnavailable as error:
        print(f"{error}", file=sys.stderr)
        return 2

    started = time.monotonic()
    completed = subprocess.run(command, cwd=REPO_ROOT)
    elapsed = time.monotonic() - started

    try:
        after = spent_so_far(key)
    except CreditsUnavailable as error:
        print(f"the work finished but the bill could not be read: {error}", file=sys.stderr)
        return completed.returncode or 2

    report(after - before, elapsed)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
