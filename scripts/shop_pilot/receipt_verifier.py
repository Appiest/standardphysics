"""Independent verifier for shop-pilot evidence receipts.

Status fields, booleans and self-reported hashes are never sufficient. This
verifier opens every referenced artifact, recomputes its digest, re-evaluates
every assertion from those bytes, and refuses a pass when an identity or a
frozen-policy hash cannot be independently confirmed.

It exists because the pre-existing verifier at
``scripts/verify_outlet_repair_acceptance.py`` only checks that a status string
is not "skipped" and that an ``evidence_kind`` label matches. The malformed G9
receipts (``artifact_paths_and_hashes: {"rebuilt_graph": "100"}`` from a
command that printed an API key) passed that shape of check.
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any

from .assertions import recompute_assertion
from .evidence import (
    ArtifactError,
    is_sha256_hex,
    load_json,
    resolve_artifact,
    sha256_file,
)

RECEIPT_REQUIRED_FIELDS = (
    "receipt_id", "gate_id", "run_id", "lane_id", "evidence_kind",
    "source_commit", "dirty_source_digest", "contract_hash", "policy_hash",
    "input_artifacts", "output_artifacts", "scan_id", "revision_id",
    "scenario_hash", "scope_manifest_hash", "evidence_manifest_hash",
    "command_or_recorded_ui_steps", "working_directory", "environment_versions",
    "started_at", "finished_at", "exit_code", "assertions", "raw_log_path",
    "evaluator_identity",
)
EVIDENCE_KINDS = (
    "synthetic_component", "synthetic_production_path", "saved_real_capture",
    "fresh_physical_phone", "new_shop_field", "human_control_measurement",
    "human_rule_review", "actual_render", "actual_device_runtime",
)
# Evidence kinds whose claims rest on real physical provenance. A synthetic
# substitute or an unverifiable identity can never carry these to a pass.
PHYSICAL_KINDS = (
    "saved_real_capture", "fresh_physical_phone", "new_shop_field",
    "human_control_measurement", "human_rule_review", "actual_device_runtime",
)
HUMAN_KINDS = ("human_control_measurement", "human_rule_review")
STATUS_INVALID = "invalid"
STATUS_BLOCKED = "externally_blocked"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_VALID = "valid"


def _is_explicit_null(value: Any) -> bool:
    return isinstance(value, dict) and value.get("value", "missing") is None and bool(value.get("reason"))


def _check_required_fields(receipt: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for field in RECEIPT_REQUIRED_FIELDS:
        if field not in receipt:
            problems.append(f"missing required field {field}")
        elif receipt[field] is None and field not in (
            "scan_id", "revision_id", "scenario_hash", "scope_manifest_hash",
            "evidence_manifest_hash",
        ) and not _is_explicit_null(receipt[field]):
            problems.append(f"required field {field} is null without a stated reason")
    return problems


def _resolve_artifact_list(records: Any, base_dir: pathlib.Path, label: str) -> tuple[list[dict[str, Any]], list[str]]:
    resolved: list[dict[str, Any]] = []
    problems: list[str] = []
    if records is None or records == []:
        return resolved, problems
    if not isinstance(records, list):
        return resolved, [f"{label} must be a list of artifact records"]
    for index, record in enumerate(records):
        try:
            resolved.append(resolve_artifact(record, base_dir))
        except ArtifactError as exc:
            problems.append(f"{label}[{index}]: {exc}")
    return resolved, problems


def _git_commit_exists(workdir: pathlib.Path, commit: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=workdir, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _verify_policy_hashes(
    receipt: dict[str, Any], policy_path: pathlib.Path | None, contract_path: pathlib.Path | None
) -> list[str]:
    problems: list[str] = []
    for label, path, claimed in (
        ("policy_hash", policy_path, receipt.get("policy_hash")),
        ("contract_hash", contract_path, receipt.get("contract_hash")),
    ):
        if path is None:
            continue
        if not path.is_file():
            problems.append(f"{label} cannot be checked: {path} is not a file")
            continue
        actual = sha256_file(path)
        if claimed != actual:
            problems.append(f"{label} {claimed!r} != frozen file hash {actual}")
    return problems


def _verify_assertions(receipt: dict[str, Any], resolved: list[dict[str, Any]]) -> list[str]:
    assertions = receipt.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        return ["receipt has no recomputable assertions"]
    context = {
        "command": receipt.get("command_or_recorded_ui_steps"),
        "receipt_identity": {
            key: receipt.get(key)
            for key in ("scan_id", "revision_id", "owner_id", "run_id")
        },
    }
    problems: list[str] = []
    for index, assertion in enumerate(assertions):
        enriched = dict(assertion) if isinstance(assertion, dict) else {"raw": assertion}
        enriched["_resolved_artifacts"] = resolved
        ok, detail, _ = recompute_assertion(enriched, context)
        if not ok:
            problems.append(f"assertion[{index}] {assertion.get('id') if isinstance(assertion, dict) else '?'}: {detail}")
    return problems


def _verify_evidence_kind(receipt: dict[str, Any]) -> list[str]:
    kind = receipt.get("evidence_kind")
    if kind not in EVIDENCE_KINDS:
        return [f"evidence_kind {kind!r} is not an accepted class"]
    if kind in PHYSICAL_KINDS:
        if not receipt.get("scan_id") and kind != "human_rule_review":
            return [f"kind {kind!r} requires a real scan_id"]
        if not receipt.get("revision_id") and kind != "human_rule_review":
            return [f"kind {kind!r} requires a real revision_id"]
    return []


def _verify_human_reviewer(receipt: dict[str, Any]) -> list[str]:
    if receipt.get("evidence_kind") not in HUMAN_KINDS:
        return []
    reviewer = receipt.get("evaluator_identity")
    if not isinstance(reviewer, dict):
        return ["human evidence requires an evaluator_identity object"]
    actor = str(reviewer.get("actor", ""))
    if not actor or actor.lower() in ("self_review", "agent", "auto") or actor == receipt.get("lane_id"):
        return ["human evidence requires an attributable non-implementation reviewer"]
    if not reviewer.get("attestation"):
        return ["human evidence requires a review attestation"]
    return []


def _has_identity_assertion(receipt: dict[str, Any]) -> bool:
    assertions = receipt.get("assertions") or []
    return any(
        isinstance(item, dict) and item.get("measurement_method") == "identity_consistent"
        for item in assertions
    )


def verify_receipt(
    receipt: dict[str, Any],
    *,
    artifacts_dir: pathlib.Path,
    policy_path: pathlib.Path | None = None,
    contract_path: pathlib.Path | None = None,
    git_worktree: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Verify one receipt independently and return a status with reasons."""
    hard: list[str] = []
    blocked: list[str] = []

    hard.extend(_check_required_fields(receipt))
    hard.extend(_verify_evidence_kind(receipt))
    hard.extend(_verify_human_reviewer(receipt))
    hard.extend(_verify_policy_hashes(receipt, policy_path, contract_path))
    hard.extend(_check_raw_log(receipt, artifacts_dir))

    source_commit = receipt.get("source_commit")
    if isinstance(source_commit, str) and len(source_commit) >= 7 and git_worktree is not None:
        if not _git_commit_exists(git_worktree, source_commit):
            hard.append(f"source_commit {source_commit!r} does not exist in the repository")

    digest = receipt.get("dirty_source_digest")
    if digest is not None and not _is_explicit_null(digest) and not is_sha256_hex(digest):
        hard.append("dirty_source_digest is not a sha256 hex string")

    output_records = receipt.get("output_artifacts")
    resolved_inputs, input_problems = _resolve_artifact_list(
        receipt.get("input_artifacts"), artifacts_dir, "input_artifacts"
    )
    resolved_outputs, output_problems = _resolve_artifact_list(
        output_records, artifacts_dir, "output_artifacts"
    )
    hard.extend(input_problems)
    hard.extend(output_problems)

    if not resolved_outputs and not output_records and not _is_explicit_null(output_records):
        hard.append("receipt references no output artifact at all")
    elif not resolved_outputs and _is_explicit_null(output_records):
        blocked.append("no output artifact; receipt declares a nonapplicable artifact with reason")

    all_resolved = resolved_inputs + resolved_outputs
    hard.extend(_verify_assertions(receipt, all_resolved))

    if receipt.get("evidence_kind") in PHYSICAL_KINDS and not _has_identity_assertion(receipt):
        blocked.append(
            "physical evidence has no identity_consistent assertion to cross-check scan/revision/owner"
        )

    if hard:
        status = STATUS_INVALID
    elif blocked:
        status = STATUS_BLOCKED
    else:
        status = STATUS_VALID

    return {
        "receipt_id": receipt.get("receipt_id"),
        "gate_id": receipt.get("gate_id"),
        "evidence_kind": receipt.get("evidence_kind"),
        "status": status,
        "invalid_reasons": hard,
        "blocked_reasons": blocked,
        "verified_artifacts": [entry["relative_path"] for entry in all_resolved],
        "assertions_checked": len(receipt.get("assertions") or []),
    }


def _check_raw_log(receipt: dict[str, Any], base_dir: pathlib.Path) -> list[str]:
    raw_log = receipt.get("raw_log_path")
    if raw_log is None:
        return ["raw_log_path is null"]
    if _is_explicit_null(raw_log):
        return []
    candidate = pathlib.Path(raw_log)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if not candidate.is_file():
        return [f"raw_log_path {raw_log!r} does not exist"]
    return []


def verify_receipt_file(receipt_path: pathlib.Path, **kwargs: Any) -> dict[str, Any]:
    receipt = load_json(receipt_path)
    kwargs.setdefault("artifacts_dir", receipt_path.parent)
    return verify_receipt(receipt, **kwargs)


def load_policy(path: pathlib.Path) -> dict[str, Any]:
    return load_json(path)
