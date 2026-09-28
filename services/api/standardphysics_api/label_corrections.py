"""Append-only training records of what an owner corrected about a found piece.

Each row keeps the whole node before and after, so the labelling model's own
answer (`labeled_by` "astra"), the photo evidence it saw, and the owner's
correction can be rebuilt into a fine-tuning example without the scan changing
underneath it. A removal has no after; putting a piece back has no before, and
tells a training set to drop the removal it undoes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from standardphysics_contracts import SceneNode


@dataclass(frozen=True)
class Correction:
    kind: str
    before: SceneNode | None
    after: SceneNode | None

    @property
    def node_id(self) -> uuid.UUID:
        node = self.before or self.after
        assert node is not None
        return node.id


def record_correction(connection, scan_id: uuid.UUID, revision: int, correction: Correction) -> None:
    connection.execute(
        "INSERT INTO label_corrections (scan_id,revision,node_id,kind,before_json,after_json)"
        " VALUES (?,?,?,?,?,?)",
        (str(scan_id), revision, str(correction.node_id), correction.kind,
         _snapshot(correction.before), _snapshot(correction.after)),
    )


def corrections_for(connection, scan_id: uuid.UUID) -> list[dict]:
    rows = connection.execute(
        "SELECT revision,node_id,kind,before_json,after_json,created_at FROM label_corrections"
        " WHERE scan_id=? ORDER BY id",
        (str(scan_id),),
    ).fetchall()
    return [{
        "revision": row["revision"], "node_id": row["node_id"], "kind": row["kind"],
        "before": json.loads(row["before_json"]), "after": json.loads(row["after_json"]),
        "created_at": row["created_at"],
    } for row in rows]


def _snapshot(node: SceneNode | None) -> str:
    return json.dumps(node.model_dump(mode="json") if node is not None else None)
