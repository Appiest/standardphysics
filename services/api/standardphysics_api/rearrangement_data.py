"""Local append-only training records for accepted model rearrangements."""

from __future__ import annotations

import json
import uuid

from standardphysics_contracts import RearrangementSuggestion


def record_accepted(connection, scan_id: uuid.UUID, revision: int, source_hash: str,
                    suggestion: RearrangementSuggestion, chains: list[dict]) -> None:
    payload = {
        "source_graph_hash": source_hash,
        "suggested_graph_hash": suggestion.graph_hash,
        "provider": suggestion.provider,
        "model": suggestion.model,
        "chains": chains,
        "accepted_moves": [move.model_dump(mode="json") for move in suggestion.moves],
        "reward": suggestion.reward.model_dump(mode="json") if suggestion.reward else None,
        "rounds": suggestion.rounds,
        "prompt_tokens": suggestion.prompt_tokens,
        "completion_tokens": suggestion.completion_tokens,
        "cost_dollars": suggestion.cost_dollars,
    }
    connection.execute(
        "INSERT INTO rearrangement_teacher_events (suggestion_id,scan_id,revision,kind,payload_json)"
        " VALUES (?,?,?,?,?)",
        (suggestion.suggestion_id, str(scan_id), revision, "accepted", json.dumps(payload)),
    )


def accepted_suggestion(connection, scan_id: uuid.UUID, revision: int, suggestion_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM rearrangement_teacher_events WHERE suggestion_id=? AND scan_id=?"
        " AND revision=? AND kind='accepted'",
        (suggestion_id, str(scan_id), revision),
    ).fetchone() is not None


def record_outcome(connection, scan_id: uuid.UUID, revision: int, suggestion_id: str, kind: str,
                   payload: dict | None = None) -> bool:
    if kind not in {"saved", "put_back"}:
        raise ValueError("unknown rearrangement outcome")
    if not accepted_suggestion(connection, scan_id, revision, suggestion_id):
        return False
    connection.execute(
        "INSERT OR IGNORE INTO rearrangement_teacher_events"
        " (suggestion_id,scan_id,revision,kind,payload_json) VALUES (?,?,?,?,?)",
        (suggestion_id, str(scan_id), revision, kind, json.dumps(payload or {})),
    )
    return True


def suggested_hash(connection, scan_id: uuid.UUID, revision: int, suggestion_id: str) -> str | None:
    row = connection.execute(
        "SELECT payload_json FROM rearrangement_teacher_events WHERE suggestion_id=? AND scan_id=?"
        " AND revision=? AND kind='accepted'",
        (suggestion_id, str(scan_id), revision),
    ).fetchone()
    return json.loads(row["payload_json"])["suggested_graph_hash"] if row else None
