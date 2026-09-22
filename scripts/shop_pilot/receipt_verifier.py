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
    canonical_dirty_digest,
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
    contact = reviewer.get("contact")
    if not isinstance(contact, dict) or not (contact.get("email") or contact.get("name")):
        return ["human evidence requires attributable contact details for the reviewer"]
    return []


def _corroborate_human_review(receipt: dict[str, Any], resolved: list[dict[str, Any]]) -> list[str]:
    """Human receipt claims need corroborating provenance beyond receipt prose."""
    if receipt.get("evidence_kind") not in HUMAN_KINDS:
        return []
    attestation = str(receipt.get("evaluator_identity", {}).get("attestation", ""))
    artifact_hash = receipt.get("review_attestation", {}).get("artifact_sha256") if isinstance(
        receipt.get("review_attestation"), dict
    ) else None
    found = False
    for artifact in resolved:
        if artifact_hash is not None and artifact.get("sha256") != artifact_hash:
            continue
        try:
            text = pathlib.Path(artifact["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [f"cannot open review attestation artifact: {exc}"]
        if attestation and attestation in text:
            found = True
            break
    if not found:
        return ["human review attestation is not corroborated by any referenced artifact bytes"]
    return []


def _verify_source_state(
    receipt: dict[str, Any], git_worktree: pathlib.Path | None
) -> tuple[list[str], list[str]]:
    """Recompute the declared dirty-source digest against declarated files.

    A sha256-shaped string is not source freshness. The receipt must declare
    exactly which files were dirty (path -> sha256) and the canonical digest
    over that map must equal ``dirty_source_digest``; each file is then
    re-hashed in the worktree. When the worktree HEAD no longer matches the
    receipt's source_commit, the receipt is bound to stale source and cannot
    prove current behavior.
    """
    hard: list[str] = []
    blocked: list[str] = []
    declared_digest = receipt.get("dirty_source_digest")
    declared_files = receipt.get("dirty_source_files", {})
    if not isinstance(declared_files, dict):
        return ["dirty_source_files must be a path->sha256 map"], blocked
    if declared_digest is not None:
        recomputed = canonical_dirty_digest(declared_files)
        if declared_digest != recomputed:
            hard.append(f"dirty_source_digest {declared_digest!r} != recomputed {recomputed!r}")
    if git_worktree is None:
        return hard, blocked
    for relative, declared_hash in declared_files.items():
        candidate = git_worktree / relative
        if not candidate.is_file():
            hard.append(f"dirty source file {relative!r} does not exist in the worktree")
            continue
        actual = sha256_file(candidate)
        if actual != declared_hash:
            hard.append(f"dirty source file {relative!r} hash {actual} != declared {declared_hash}")
    source_commit = receipt.get("source_commit")
    if source_commit:
        head = _git_head(git_worktree)
        if head is not None and source_commit != head:
            blocked.append(
                f"receipt is bound to source_commit {source_commit} but worktree HEAD is {head}; "
                "stale source cannot prove current behavior"
            )
    return hard, blocked


def _git_head(workdir: pathlib.Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True
        )
    except FileNotFoundError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


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

    source_hard, source_blocked = _verify_source_state(receipt, git_worktree)
    hard.extend(source_hard)
    blocked.extend(source_blocked)

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
    blocked.extend(_corroborate_human_review(receipt, all_resolved))

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
        "mutation_id": receipt.get("mutation_id"),
        "outcome": receipt.get("outcome"),
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
