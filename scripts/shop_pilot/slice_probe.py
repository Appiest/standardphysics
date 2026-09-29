"""Probe the I1 integration slice at the current code and record what it does.

I1 is: authenticated upload -> complete-evidence receipt -> worker -> evidence
graph. This script runs that journey against the real FastAPI app with real
fixture bytes and NO external provider (detection is expected to be
unavailable in the probe; it records the truth). The output is a JSON summary
plus a raw HTTP/dB observation log, saved under the assets directory. It is a
read-only observation of current behavior: it invents no shared schemas and
waits for K's frozen contracts to assert the required evidence-closure
behavior.
"""

from __future__ import annotations

import json
import subprocess
import sys

from scripts.shop_pilot.slice_probe_common import OUT_DIR
from scripts.shop_pilot.slice_probe_i1 import run_probe_i1, write_receipt_i1
from scripts.shop_pilot.slice_probe_i2 import run_probe_i2, write_receipt_i2
from scripts.shop_pilot.slice_probe_i3 import run_probe_i3, write_receipt_i3

PHASES = {
    "i1": (run_probe_i1, write_receipt_i1),
    "i2": (run_probe_i2, write_receipt_i2),
    "i3": (run_probe_i3, write_receipt_i3),
}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="i1", choices=tuple(PHASES))
    args = parser.parse_args()
    run_probe, write_receipt = PHASES[args.phase]
    report = run_probe()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{args.phase}-probe.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    write_receipt(report, target, head)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
