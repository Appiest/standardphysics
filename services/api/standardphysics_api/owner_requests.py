"""What the app asks an owner for, what they said, and what their answers change.

A scan can't see a handle's shape, feel a door's push or know whether customers
use the restroom. Those checks are known before the shop is measured, so the
app asks them while the owner is still standing in the shop (docs/UX.md,
screens 5 to 7). A check that finds a gap only after measuring adds a
follow-up, like the doorway width.

Answers change the findings when they are read, not by running the checks
again. The stored assessment stays what the scan measured, and `apply_answers`
lays the owner's answers over it. A number is checked against its rule at once.
A photo waits for a person on the team to look at it.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from standardphysics_agents import VerificationLedger, load_pack
from standardphysics_agents.copy import QUESTIONS, REQUESTS
from standardphysics_contracts import AnswerRequest, Assessment, Finding, OwnerRequest, RequestAnswer
from standardphysics_contracts.owner import RequestKind, RequestStatus, RequestTiming, Unit

from .errors import ApiProblem

FOLLOW_UP_PREFIX = "finding-"
PHOTO_TYPES = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png"}
MAX_PHOTO_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class Ask:
    id: str
    kind: RequestKind
    title: str
    detail: str
    timing: RequestTiming = "in_shop"
    rule_id: str | None = None
    unit: Unit | None = None
    needs_yes_to: str | None = None
    """Asked only when the owner didn't answer no to this other request."""


def _photo(rule_id: str, needs_yes_to: str | None = None) -> Ask:
    words = QUESTIONS[rule_id]
    return Ask(rule_id, "photo", words.title, words.detail, rule_id=rule_id, needs_yes_to=needs_yes_to)


IN_SHOP: tuple[Ask, ...] = (
    Ask("restroom", "yes_no", "Do customers use a restroom?", "Count it if customers can ask to use it."),
    Ask(
        "inside_doors", "yes_no", "Do customers go through any doors inside the shop, like a restroom door?",
        "Doors between rooms count. The front door doesn't.",
    ),
    _photo("entrance_threshold"),
    _photo("door_hardware"),
    _photo("floor_surface"),
    _photo("restroom_turning_space", needs_yes_to="restroom"),
    Ask(
        "door_opening_force", "number", "How hard are the inside doors to push open?",
        "A door pressure gauge from a hardware store measures it. Push each inside door open with it and send the "
        "highest number.",
        rule_id="door_opening_force", unit="lb", needs_yes_to="inside_doors",
    ),
)
"""In the order the app asks them: the yes or no questions first, because a no
removes a photo or a number that would otherwise be asked for."""

FOLLOW_UP_KINDS: dict[str, tuple[RequestKind, Unit | None]] = {
    "measurement": ("number", "in"),
    "photo": ("photo", None),
    "another_look": ("another_look", None),
}
"""What the owner does for each kind of follow-up. A door's swing is asked by a
tier 2 rule, which the server doesn't run yet, so it has no request."""


@dataclass(frozen=True)
class Stored:
    status: RequestStatus
    yes: bool | None
    number: float | None
    photo_name: str | None
    answered_at: datetime | None
    review: str | None


def enabled_rule_ids(ledger: VerificationLedger) -> frozenset[str]:
    return frozenset(rule.id for rule in load_pack().enabled(ledger, max_tier=1))


def in_shop_asks(enabled: frozenset[str]) -> list[Ask]:
    """The in-shop asks whose rule runs, and the yes or no questions they depend on."""
    asked = [ask for ask in IN_SHOP if ask.rule_id in enabled]
    needed = {ask.needs_yes_to for ask in asked}
    return [ask for ask in IN_SHOP if ask in asked or ask.id in needed]


def _legacy_asks(finding: Finding) -> str | None:
    """What an assessment saved before findings said what answers them was asking for."""
    rule = load_pack().by_id(finding.check_id)
    if not rule.measurable:
        return rule.evidence
    written = REQUESTS.get(finding.check_id)
    if written is not None and written.title == finding.title:
        return "measurement" if finding.check_id == "door_clear_width" else "swing"
    return "another_look"


def follow_up_asks(assessment: Assessment | None, in_shop_rules: set[str]) -> list[Ask]:
    asks = []
    for finding in assessment.findings if assessment else []:
        if finding.outcome != "question" or finding.check_id in in_shop_rules:
            continue
        kind, unit = FOLLOW_UP_KINDS.get(finding.asks or _legacy_asks(finding) or "", (None, None))
        if kind is not None:
            asks.append(Ask(
                f"{FOLLOW_UP_PREFIX}{finding.id}", kind, finding.title, finding.detail,
                timing="follow_up", rule_id=finding.check_id, unit=unit,
            ))
    return asks


def stored_answers(connection: sqlite3.Connection, scan_id: uuid.UUID) -> dict[str, Stored]:
    rows = connection.execute("SELECT * FROM owner_requests WHERE scan_id = ?", (str(scan_id),)).fetchall()
    return {row["request_id"]: _stored(row) for row in rows}


def _stored(row: sqlite3.Row) -> Stored:
    yes = row["answer_yes"]
    answered = row["answered_at"]
    return Stored(
        status=row["status"],
        yes=None if yes is None else bool(yes),
        number=row["answer_number"],
        photo_name=row["photo_name"],
        answered_at=datetime.fromisoformat(answered) if answered else None,
        review=row["review"],
    )


def _said_no(stored: Stored | None) -> bool:
    return stored is not None and stored.yes is False


def _status(ask: Ask, stored: dict[str, Stored]) -> RequestStatus:
    if ask.needs_yes_to and _said_no(stored.get(ask.needs_yes_to)):
        return "not_applicable"
    row = stored.get(ask.id)
    return row.status if row else "open"


def _answer(ask: Ask, row: Stored | None, scan_id: uuid.UUID) -> RequestAnswer | None:
    if row is None or row.answered_at is None:
        return None
    photo_url = f"/api/scans/{scan_id}/requests/{ask.id}/photo" if row.photo_name else None
    return RequestAnswer(yes=row.yes, number=row.number, photo_url=photo_url, answered_at=row.answered_at)


def _finding_for(ask: Ask, assessment: Assessment | None) -> uuid.UUID | None:
    if ask.timing == "follow_up":
        return uuid.UUID(ask.id.removeprefix(FOLLOW_UP_PREFIX))
    for finding in assessment.findings if assessment else []:
        if finding.check_id == ask.rule_id and finding.outcome == "question":
            return finding.id
    return None


def owner_requests(
    connection: sqlite3.Connection, scan_id: uuid.UUID, enabled: frozenset[str], assessment: Assessment | None
) -> list[OwnerRequest]:
    """Every request for one shop, in the order to ask them."""
    in_shop = in_shop_asks(enabled)
    asks = in_shop + follow_up_asks(assessment, {ask.rule_id for ask in in_shop if ask.rule_id})
    stored = stored_answers(connection, scan_id)
    return [
        OwnerRequest(
            id=ask.id, kind=ask.kind, timing=ask.timing, title=ask.title, detail=ask.detail, unit=ask.unit,
            status=_status(ask, stored), answer=_answer(ask, stored.get(ask.id), scan_id),
            finding_id=_finding_for(ask, assessment), review=_review(stored.get(ask.id)),
        )
        for ask in asks
    ]


def _review(row: Stored | None) -> str | None:
    return row.review if row else None


def find(requests: Iterable[OwnerRequest], request_id: str) -> OwnerRequest:
    for request in requests:
        if request.id == request_id:
            return request
    raise ApiProblem(404, "no request")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _require_open(request: OwnerRequest) -> None:
    if request.status == "not_applicable":
        raise ApiProblem(409, "This one doesn't apply to your shop.")
    if request.kind == "another_look":
        raise ApiProblem(409, "Walk this part of the shop again to answer it.")


def record_answer(
    connection: sqlite3.Connection, scan_id: uuid.UUID, request: OwnerRequest, body: AnswerRequest
) -> None:
    """Save a yes or no, or a number. Both settle the request at once."""
    _require_open(request)
    if request.kind == "yes_no" and body.yes is None:
        raise ApiProblem(400, "Answer yes or no.")
    if request.kind == "number" and body.number is None:
        raise ApiProblem(400, "Send a number.")
    if request.kind == "photo":
        raise ApiProblem(400, "Send a photo for this one.")
    _upsert(connection, scan_id, request.id, status="checked", answer_yes=body.yes, answer_number=body.number)


def photo_extension(head: bytes) -> str:
    for magic, extension in PHOTO_TYPES.items():
        if head.startswith(magic):
            return extension
    raise ApiProblem(415, "Send the photo as a JPEG or a PNG.")


def record_photo(connection: sqlite3.Connection, scan_id: uuid.UUID, request: OwnerRequest, name: str) -> None:
    """A photo is answered, and waits for a person on the team to check it."""
    _require_open(request)
    if request.kind != "photo":
        raise ApiProblem(400, "This one doesn't take a photo.")
    _upsert(connection, scan_id, request.id, status="answered", photo_name=name, review=None)


def record_skip(connection: sqlite3.Connection, scan_id: uuid.UUID, request: OwnerRequest) -> None:
    _require_open(request)
    if request.status in {"answered", "checked"}:
        return
    connection.execute(
        "INSERT INTO owner_requests (scan_id, request_id, status) VALUES (?, ?, 'skipped')"
        " ON CONFLICT (scan_id, request_id) DO UPDATE SET status = 'skipped'",
        (str(scan_id), request.id),
    )


def record_review(
    connection: sqlite3.Connection, scan_id: uuid.UUID, request: OwnerRequest, outcome: str, reviewer: str
) -> None:
    if request.kind != "photo" or request.status not in {"answered", "checked"}:
        raise ApiProblem(409, "There's no photo to check yet.")
    connection.execute(
        "UPDATE owner_requests SET status = 'checked', review = ?, reviewed_by = ?, reviewed_at = ?"
        " WHERE scan_id = ? AND request_id = ?",
        (outcome, reviewer, _now(), str(scan_id), request.id),
    )


def _upsert(connection: sqlite3.Connection, scan_id: uuid.UUID, request_id: str, **values) -> None:
    columns = {"status": values["status"], "answer_yes": values.get("answer_yes"),
               "answer_number": values.get("answer_number"), "photo_name": values.get("photo_name"),
               "review": values.get("review"), "answered_at": _now()}
    names = ", ".join(columns)
    updates = ", ".join(f"{name} = excluded.{name}" for name in columns)
    connection.execute(
        f"INSERT INTO owner_requests (scan_id, request_id, {names}) VALUES (?, ?, {', '.join('?' * len(columns))})"
        f" ON CONFLICT (scan_id, request_id) DO UPDATE SET {updates}",
        (str(scan_id), request_id, *columns.values()),
    )


def pending_reviews(connection: sqlite3.Connection) -> list[tuple[uuid.UUID, str]]:
    """Every photo waiting for a person on the team, oldest first."""
    rows = connection.execute(
        "SELECT scan_id, request_id FROM owner_requests WHERE status = 'answered' AND photo_name IS NOT NULL"
        " ORDER BY answered_at"
    ).fetchall()
    return [(uuid.UUID(row["scan_id"]), row["request_id"]) for row in rows]


def carry_answers(connection: sqlite3.Connection, store, source: uuid.UUID, destination: uuid.UUID) -> None:
    """A new walk of the same shop keeps what the owner said and sent in it.

    Only the in-shop requests carry over: they're about the shop, not about one
    walk of it. Follow-ups belong to the findings of the walk that raised them.
    """
    in_shop = [ask.id for ask in IN_SHOP]
    rows = connection.execute(
        f"SELECT * FROM owner_requests WHERE scan_id = ? AND request_id IN ({', '.join('?' * len(in_shop))})",
        (str(source), *in_shop),
    ).fetchall()
    for row in rows:
        values = dict(row) | {"scan_id": str(destination)}
        columns = ", ".join(values)
        connection.execute(
            f"INSERT INTO owner_requests ({columns}) VALUES ({', '.join('?' * len(values))})", tuple(values.values())
        )
        _copy_photo(store, source, destination, row["photo_name"])


def _copy_photo(store, source: uuid.UUID, destination: uuid.UUID, name: str | None) -> None:
    if not name:
        return
    original = store.scan_dir(source) / "requests" / name
    if original.exists():
        target = store.scan_dir(destination) / "requests" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
