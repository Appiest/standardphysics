"""Recompute certificate verdicts from the frozen 04-hard-gates.json lists.

No hardcoded dispositions and no hand-edited blocker lists. Gate statuses
are read from the current matrix gate_matrix; a gate is granted only when
its own status is one of the accepted pass labels AND every gate in its
transitive depends_on closure is granted (per the frozen policy). Unknown,
failed, pending or blocked gates are conservatively not granted.

Certificates leave the policy document untouched: their `all_of` lists are
read at run time and blocked_by is derived as all_of minus granted.
"""

from __future__ import annotations

import pathlib
import sys

MATRIX = pathlib.Path("scripts/shop_pilot/assets/certificate-matrix-opencode-20260921-170615.json")
POLICY = pathlib.Path("docs/deepseek-shop-pilot/04-hard-gates.json")

PASS_STATUSES = {
    "passed",
    "passed_with_named_limitation",
    "passed_targeted_with_note",
}


def _status_of(matrix: dict) -> dict[str, str]:
    return {gate["id"]: gate.get("status", "unknown") for gate in matrix.get("gate_matrix", [])}


def _dependency_closure(gate_id: str, depends_of: dict[str, list[str]]) -> set[str]:
    closure: set[str] = set()
    pending = [gate_id]
    while pending:
        current = pending.pop()
        for dependency in depends_of.get(current, []):
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def compute(matrix: dict, policy: dict) -> dict:
    statuses = _status_of(matrix)
    depends_of = {
        gate["id"]: list(gate.get("depends_on", []))
        for gate in policy.get("gates", [])
    }

    def granted(gate_id: str) -> bool:
        if statuses.get(gate_id, "unknown") not in PASS_STATUSES:
            return False
        return all(granted(dependency) for dependency in depends_of.get(gate_id, []))

    certificates = {}
    for name, definition in policy.get("certificates", {}).items():
        if not isinstance(definition, dict) or "all_of" not in definition:
            continue
        members = list(definition["all_of"])
        blocked = [gate for gate in members if not granted(gate)]
        certificates[name] = {
            "status": "awarded" if not blocked else "not_awarded",
            "all_of": members,
            "blocked_by": blocked,
            "derived_from_policy_lists": True,
        }
    certificates["whole_site_ADA_compliance"] = {"automated_certificate_allowed": False}
    return {
        "gate_statuses_read_from_matrix": sorted(statuses.items()),
        "dependency_closure_respected": True,
        "certificates": certificates,
    }


def main() -> int:
    policy = _load(POLICY)
    matrix = _load(MATRIX)
    derived = compute(matrix, policy)
    matrix["certificates"] = derived["certificates"]
    matrix["certificate_derivation"] = derived
    MATRIX.write_text(_dump(matrix), encoding="utf-8")
    print(_dump(derived["certificates"]))
    return 0


def _load(path: pathlib.Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _dump(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2)


if __name__ == "__main__":
    sys.exit(main())
