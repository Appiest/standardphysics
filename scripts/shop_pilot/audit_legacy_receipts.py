"""Audit the legacy G9 receipts in PROGRESS_MOFFETT_OUTLET_REPAIR.json.

Extracts REAL-01..REAL-04 exactly as stored, redacts any secret-printing
command (the originals materially print an API key; we never repeat it),
writes a sanitized immutable copy, and runs the independent verifier against
each. The expected result is ``invalid`` for all four, proving that the old
status-and-prose receipts cannot carry a certificate under the new harness.

Sanitized copies land in scripts/shop_pilot/assets/legacy/; the original
PROGRESS file is never modified.
"""

from __future__ import annotations

import json
import pathlib
import sys

from .receipt_verifier import verify_receipt

PROGRESS_SOURCE = "PROGRESS_MOFFETT_OUTLET_REPAIR.json"
LEGACY_CASES = ("REAL-01", "REAL-02", "REAL-03", "REAL-04")
REDACTED_COMMAND = "[REDACTED: original command printed an API key; not reproduced]"


def redact_command(command: object) -> str:
    text = str(command)
    lowered = text.lower()
    if any(marker in lowered for marker in ("os.getenv", "os.environ", "printenv", "env |")):
        return REDACTED_COMMAND
    return text


def audit(root: pathlib.Path, out_dir: pathlib.Path) -> dict:
    source = root / PROGRESS_SOURCE
    if not source.is_file():
        raise FileNotFoundError(f"{source} missing")
    progress = json.loads(source.read_text(encoding="utf-8"))
    receipts = progress.get("receipts", {})
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for case_id in LEGACY_CASES:
        original = receipts.get(case_id)
        if original is None:
            results.append({"case": case_id, "missing": True, "status": "missing"})
            continue
        sanitized = dict(original)
        sanitized["command_or_ui_action"] = redact_command(sanitized["command_or_ui_action"])
        target = out_dir / f"{case_id}-sanitized.json"
        target.write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
        verdict = verify_receipt(sanitized, artifacts_dir=root)
        results.append({
            "case": case_id,
            "sanitized_copy": str(target.relative_to(root)),
            "status": verdict["status"],
            "invalid_reasons": verdict["invalid_reasons"],
            "blocked_reasons": verdict["blocked_reasons"],
        })

    report = {
        "kind": "legacy_g9_receipt_audit",
        "source": PROGRESS_SOURCE,
        "policy": "existing_G9_rebuilt_graph_100_receipts must be invalid_evidence",
        "results": results,
    }
    report_path = out_dir / "legacy-g9-audit.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[2]
    out_dir = root / "scripts" / "shop_pilot" / "assets" / "legacy"
    report = audit(root, out_dir)
    print(json.dumps(report, indent=2))
    bad = [r for r in report["results"] if r.get("status") != "invalid"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
