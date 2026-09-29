"""Pieces every command shares: providers, the fixture shop and the router."""

from __future__ import annotations

import sys

from ..router import LocalPolicyRouter, TypeSafeRouter

PROVIDERS = ("stub", "pipeline")


ROUTERS = ("typesafe", "local")


def measurements(name: str):
    if name == "pipeline":
        from standardphysics_pipeline import PipelineMeasurements

        return PipelineMeasurements()
    from standardphysics_fixtures import FixtureMeasurements

    return FixtureMeasurements()


def fixture_shop():
    from standardphysics_fixtures import build_graph, build_scenario

    return build_graph(), build_scenario()


def count_noun(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def router_for(name: str):
    """TypeSafe when it is configured, and a labelled local policy when not."""
    if name == "local":
        return LocalPolicyRouter()
    router = TypeSafeRouter()
    if router.configured:
        return router
    print(
        "TYPESAFE_API_KEY and TYPESAFE_BASE_URL are not set. "
        "Running the local policy, labelled as one.",
        file=sys.stderr,
    )
    return LocalPolicyRouter()


NOTHING_ENABLED = (
    'No checks are enabled. Run: rules review --by "<name>"'
)


def nothing_enabled(pack, ledger) -> bool:
    if pack.enabled(ledger, max_tier=1):
        return False
    print(NOTHING_ENABLED, file=sys.stderr)
    return True
