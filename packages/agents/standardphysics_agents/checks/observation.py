"""What a check measured, before it becomes a sentence.

A check answers a geometric question and says which rule it answered it
against. Turning that into a title, a measurement line and a fix happens in
copy.py, and deciding whether it is a problem or a request happens in
findings.py. Keeping those apart means a check never has to know how it reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from standardphysics_contracts import Locus
from standardphysics_contracts.findings import Asks


@dataclass(frozen=True)
class Observation:
    rule_id: str
    satisfied: bool
    measured_inches: float | None = None
    required_inches: float | None = None
    relied_on: tuple[UUID, ...] = ()
    """Nodes whose geometry this answer rests on.

    A node marked needs_another_look turns the finding into a request.
    """

    locus: Locus | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    """Named inputs the copy needs: stop names, node labels, a deficit."""

    dedupe_key: tuple = ()
    """Two legs through the same gap are one finding, not two."""

    reason: str = ""
    """Which branch of the rule decided this, for tracing and for copy."""

    seen_directly: bool = False
    """The check measured a rule whose evidence is usually a photo, because this graph shows the thing.

    A phone scan rarely captures a restroom's fixtures, so `restroom_turning_space`
    is normally asked about; when the graph does hold a toilet the check measures
    the room itself and the answer is a finding, not a request.
    """

    asks_for: Asks | None = None
    """Set when the answer needs something a person has to supply.

    A door's clear width is measured with the door open 90 degrees, and a scan
    catches the doorway rather than the swing. When the provider says so, the
    finding becomes a request for that one number instead of a pass on a
    measurement of the wrong thing.
    """


@dataclass(frozen=True)
class Unevaluated:
    """A rule we hold and cannot answer yet, and what it is waiting on.

    This never reaches the owner. It reaches the team, so a missing capability
    is visible instead of looking like a clean pass.
    """

    rule_id: str
    waiting_on: str
