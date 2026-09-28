"""ADA 2010 904.5.1. Condiments, lids, straws and drinks a customer takes for themselves.

904.5.1 sends self-service shelves and dispensers to 308, so each is read the
way a kiosk is: a box whose top is within reach passes, one wholly out of
reach fails, and one that runs past 48 inches asks where its lever or spout
sits.
"""

from __future__ import annotations

from ..tracing import traced
from . import roles
from .context import CheckContext
from .observation import Observation
from .reach import within_reach

RULE_ID = "self_service_reach"


@traced("checks.self_service_reach")
def self_service_reach(ctx: CheckContext) -> list[Observation]:
    return [within_reach(ctx, RULE_ID, node) for node in roles.self_service(ctx.graph)]
