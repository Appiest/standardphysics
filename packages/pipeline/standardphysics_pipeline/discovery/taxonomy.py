"""The fixed classes a photo's free-text finding is mapped onto.

A detector writes whatever word the model chose: "tv", "television", "monitor",
"screen", "whiteboard", "switch", "socket". Those are not the same thing, and
the difference matters. A television mounted over a counter is a target; the
monitor behind the register is not; a light switch is not an outlet. Treating
one as the other is how a suite reports an outlet that was never there.

So every name is resolved here, once, against explicit sets. The resolution is
deliberately conservative: a word that is genuinely ambiguous ("screen",
"display", "counter") resolves to a neutral class rather than being forced into
a target. The four pilot targets are outlet, television, service counter and
restroom entrance. A service counter or a restroom entrance is only ever a
candidate until the owner confirms its role and public use, so this module says
which classes need that confirmation; it never confirms them itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

OBJECT = "object"
PERSON = "person"
OUTLET = "outlet"
TELEVISION = "television"
MONITOR = "monitor"
WHITEBOARD = "whiteboard"
SOFA = "sofa"
TABLE = "table"
SERVICE_COUNTER = "service_counter"
RESTROOM_ENTRANCE = "restroom_entrance"
SWITCH = "switch"
SIGN = "sign"

TARGET_CLASSES: FrozenSet[str] = frozenset({OUTLET, TELEVISION, SERVICE_COUNTER, RESTROOM_ENTRANCE})
"""The four classes the pilot must locate or honestly leave unobserved."""

SURFACE_TARGET_CLASSES: FrozenSet[str] = frozenset({OUTLET, TELEVISION, WHITEBOARD})
"""Classes that hang off a measured vertical surface rather than standing on the floor."""

OWNER_CONFIRMATION_CLASSES: FrozenSet[str] = frozenset({SERVICE_COUNTER, RESTROOM_ENTRANCE})
"""Classes whose role and public use only the owner can settle."""

CONFUSER_CLASSES: FrozenSet[str] = frozenset({SWITCH, SIGN})
"""Classes that look like a target but deliberately are not one."""


@dataclass(frozen=True)
class SemanticClass:
    key: str
    label: str
    target: bool
    needs_owner_confirmation: bool
    confusers: FrozenSet[str]
    """Names that resemble this class without being it, so a bare name is not promoted."""


_NAMES: dict[str, frozenset[str]] = {
    OUTLET: frozenset({
        "outlet", "electrical outlet", "power outlet", "wall outlet",
        "receptacle", "electrical receptacle", "power strip", "extension lead",
        "extension cord", "socket", "plug socket", "duplex outlet",
    }),
    TELEVISION: frozenset({
        "tv", "television", "television set", "flat screen", "flat screen tv",
        "flatscreen", "smart tv", "television screen",
    }),
    MONITOR: frozenset({
        "monitor", "computer monitor", "pc monitor", "display monitor",
        "computer screen", "laptop screen",
    }),
    WHITEBOARD: frozenset({
        "whiteboard", "white board", "chalkboard", "dry erase board",
        "dry erase board", "writing board", "blackboard", "presentation board",
    }),
    SOFA: frozenset({
        "sofa", "couch", "loveseat", "sectional", "armchair", "lounge chair",
    }),
    TABLE: frozenset({
        "table", "dining table", "coffee table", "side table", "conference table",
        "desk", "work table", "countertop table",
    }),
    SERVICE_COUNTER: frozenset({
        "counter", "service counter", "sales counter", "checkout counter",
        "order counter", "service desk", "reception counter", "cash wrap",
    }),
    RESTROOM_ENTRANCE: frozenset({
        "restroom", "restroom entrance", "restroom door", "bathroom",
        "bathroom door", "washroom", "toilet", "toilet door", "lavatory",
        "customer restroom",
    }),
    SWITCH: frozenset({
        "switch", "light switch", "dimmer switch", "toggle switch",
        "dimmer", "circuit breaker",
    }),
    SIGN: frozenset({
        "sign", "signage", "exit sign", "fire alarm", "smoke detector",
        "thermostat", "data port", "ethernet port", "network port",
        "cable plate", "blank plate", "phone jack", "coaxial port", "usb port",
        "hdmi port", "electrical panel",
    }),
    PERSON: frozenset({
        "person", "people", "human", "man", "woman", "child", "customer",
        "shopper", "employee", "staff", "worker", "hand", "arm", "leg", "face",
    }),
}

_CONFUSERS: dict[str, frozenset[str]] = {
    TELEVISION: frozenset({
        "monitor", "computer monitor", "whiteboard", "white board",
        "chalkboard", "window", "mirror", "picture", "painting", "poster",
        "projector screen", "laptop", "tablet",
    }),
    MONITOR: frozenset({
        "tv", "television", "television set", "whiteboard", "white board",
        "window", "laptop", "tablet",
    }),
    WHITEBOARD: frozenset({
        "tv", "television", "monitor", "window", "poster", "painting", "mirror",
    }),
    OUTLET: frozenset({
        "switch", "light switch", "dimmer", "sign", "exit sign", "fire alarm",
        "smoke detector", "thermostat", "data port", "ethernet port",
        "network port", "cable plate", "blank plate", "phone jack",
        "coaxial port", "usb port", "hdmi port", "electrical panel",
    }),
    SOFA: frozenset({"table", "bench", "bed", "stool", "chair"}),
    TABLE: frozenset({"sofa", "couch", "bed", "shelf", "shelving", "cabinet"}),
    SERVICE_COUNTER: frozenset({
        "table", "desk", "shelf", "shelving", "cabinet", "countertop",
        "cash register", "pos terminal", "card reader", "display case",
    }),
    RESTROOM_ENTRANCE: frozenset({
        "sign", "restroom sign", "storage room", "storage door", "office door",
        "closet", "back door", "utility room",
    }),
}

_CLASSES: dict[str, SemanticClass] = {
    key: SemanticClass(
        key=key,
        label=key.replace("_", " ").title(),
        target=key in TARGET_CLASSES,
        needs_owner_confirmation=key in OWNER_CONFIRMATION_CLASSES,
        confusers=_CONFUSERS.get(key, frozenset()),
    )
    for key in _NAMES
}

_BY_NAME: dict[str, str] = {
    name: key for key, names in _NAMES.items() for name in names
}


def normalize(name: str) -> str:
    """A detector's phrase in the form the sets are written in."""
    return " ".join(str(name).strip().lower().replace("_", " ").split())


def classify(name: str) -> str:
    """The class a detector's word belongs to, or the neutral object class.

    Ambiguous words never resolve to a target. An unknown word is an object,
    which is the one answer that cannot manufacture a finding.
    """
    return _BY_NAME.get(normalize(name), OBJECT)


_NEUTRAL = SemanticClass(
    key=OBJECT, label="Object", target=False, needs_owner_confirmation=False, confusers=frozenset(),
)


def semantic_class(key: str) -> SemanticClass:
    return _CLASSES.get(key, _NEUTRAL)


def is_target(name: str) -> bool:
    return classify(name) in TARGET_CLASSES


def needs_owner_confirmation(name: str) -> bool:
    return classify(name) in OWNER_CONFIRMATION_CLASSES


def is_confuser_of(name: str, target: str) -> bool:
    """Whether a name is a known look-alike of a target class without being it."""
    return normalize(name) in _CONFUSERS.get(target, frozenset())


def names_for(key: str) -> frozenset[str]:
    return _NAMES.get(key, frozenset())


TARGET_NAME_SETS: dict[str, frozenset[str]] = {key: _NAMES[key] for key in TARGET_CLASSES}
