"""Recompute receipt assertions from artifact bytes.

Every assertion declares a ``measurement_method`` that names a handler here.
The verifier runs the handler against the resolved artifacts and compares the
recomputed value with the receipt's ``expected``. A method with no handler is
not silently trusted: the receipt is rejected as unverifiable. This is what
stops a fabricated hash, an empty graph "claimed to contain an outlet", or an
artifact from the wrong revision from passing.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Callable

from .evidence import ArtifactError, sha256_file

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"fw_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9._-]{16,}"),
)
ENV_PRINT_MARKERS = ("os.getenv", "os.environ", "printenv", "env |", "echo $")


class AssertionError(ValueError):
    """Raised when an assertion cannot be recomputed or does not hold."""


def _nested_get(document: Any, dotted: str) -> Any:
    node = document
    for part in dotted.split("."):
        if not part:
            continue
        if isinstance(node, dict):
            if part not in node:
                raise AssertionError(f"path {dotted!r} missing segment {part!r}")
            node = node[part]
        elif isinstance(node, list):
            node = node[int(part)]
        else:
            raise AssertionError(f"path {dotted!r} descends into {type(node).__name__}")
    return node


def _named_artifact(assertion: dict[str, Any], key: str | None) -> dict[str, Any]:
    artifacts = assertion.get("_resolved_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise AssertionError("assertion has no resolved evidence artifacts")
    explicit = assertion.get("params", {}).get("artifact")
    if explicit is not None:
        key = explicit
    if key is None:
        return artifacts[0]
    for artifact in artifacts:
        if artifact.get("relative_path") == key or pathlib.Path(artifact["path"]).name == key:
            return artifact
    raise AssertionError(f"no resolved artifact named {key!r}")


def _read_text(assertion: dict[str, Any], key: str | None = None) -> str:
    artifact = _named_artifact(assertion, key)
    return pathlib.Path(artifact["path"]).read_text(encoding="utf-8", errors="replace")


def _read_json(assertion: dict[str, Any], key: str | None = None) -> Any:
    return json.loads(_read_text(assertion, key))


def measure_file_sha256(assertion: dict[str, Any], context: dict[str, Any]) -> Any:
    artifact = _named_artifact(assertion, assertion.get("params", {}).get("artifact"))
    return sha256_file(pathlib.Path(artifact["path"]))


def measure_json_field(assertion: dict[str, Any], context: dict[str, Any]) -> Any:
    params = assertion.get("params", {})
    document = _read_json(assertion, params.get("artifact"))
    return _nested_get(document, params["path"])


def measure_graph_kind_count(assertion: dict[str, Any], context: dict[str, Any]) -> int:
    params = assertion.get("params", {})
    document = _read_json(assertion, params.get("artifact"))
    nodes = _nested_get(document, params.get("nodes_path", "nodes"))
    if not isinstance(nodes, list):
        raise AssertionError("graph nodes are not a list")
    kind = params["kind"]
    return sum(1 for node in nodes if isinstance(node, dict) and node.get("kind") == kind)


def measure_identity_consistent(assertion: dict[str, Any], context: dict[str, Any]) -> bool:
    params = assertion.get("params", {})
    document = _read_json(assertion, params.get("artifact"))
    expected = context.get("receipt_identity", {})
    if not expected:
        raise AssertionError("no receipt identity available to cross-check")
    for field in params.get("fields", ["scan_id", "revision_id", "owner_id"]):
        if field not in expected:
            continue
        actual = _nested_get(document, params.get("prefix", "") + field)
        if str(actual) != str(expected[field]):
            return False
    return True


def measure_command_matches(assertion: dict[str, Any], context: dict[str, Any]) -> bool:
    command = context.get("command") or ""
    params = assertion.get("params", {})
    lowered = command.lower()
    if any(marker in lowered for marker in ENV_PRINT_MARKERS):
        return False
    required = params.get("required_substrings", [])
    return all(needle.lower() in lowered for needle in required)


def measure_text_contains(assertion: dict[str, Any], context: dict[str, Any]) -> bool:
    return assertion.get("params", {}).get("substring", "") in _read_text(assertion)


def measure_not_text_contains(assertion: dict[str, Any], context: dict[str, Any]) -> bool:
    return assertion.get("params", {}).get("substring", "") not in _read_text(assertion)


def measure_text_clean(assertion: dict[str, Any], context: dict[str, Any]) -> bool:
    text = _read_text(assertion)
    return not any(pattern.search(text) for pattern in SECRET_PATTERNS)


MEASUREMENTS: dict[str, Callable[[dict[str, Any], dict[str, Any]], Any]] = {
    "file_sha256": measure_file_sha256,
    "json_field": measure_json_field,
    "graph_kind_count": measure_graph_kind_count,
    "identity_consistent": measure_identity_consistent,
    "command_matches": measure_command_matches,
    "text_contains": measure_text_contains,
    "not_text_contains": measure_not_text_contains,
    "text_clean": measure_text_clean,
}


def _compare(expected: Any, observed: Any, comparator: str) -> bool:
    if comparator == ">=":
        return observed >= expected
    if comparator == ">":
        return observed > expected
    if comparator == "in":
        return observed in expected
    if comparator == "approx":
        return abs(observed - expected) <= 1e-9
    return observed == expected


def recompute_assertion(
    assertion: dict[str, Any], context: dict[str, Any]
) -> tuple[bool, str, Any]:
    """Recompute one assertion and compare with its declared expectation."""
    if not isinstance(assertion, dict):
        return False, "assertion is not an object", None
    method = assertion.get("measurement_method")
    handler = MEASUREMENTS.get(method)
    if handler is None:
        return False, f"no independent recompute handler for method {method!r}", None
    try:
        observed = handler(assertion, context)
    except (AssertionError, ArtifactError, KeyError, ValueError, TypeError, IndexError) as exc:
        return False, f"recompute failed: {exc}", None
    comparator = assertion.get("comparator", "==")
    if not _compare(assertion.get("expected"), observed, comparator):
        return (
            False,
            f"expected {assertion.get('expected')!r} {comparator} recomputed {observed!r}",
            observed,
        )
    declared = assertion.get("observed")
    if declared is not None and declared != observed and comparator == "==":
        return False, f"declared observed {declared!r} != recomputed {observed!r}", observed
    return True, "recomputed from artifact bytes", observed
