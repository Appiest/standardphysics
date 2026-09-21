"""Keeping the test suite off the network.

Almost everything here checks arithmetic over a measured room, and arithmetic
does not need a model. Until recently that was true by accident: the credentials
live in `.env`, nobody exported them before running pytest, and every path that
would have called out quietly fell back to something local.

It stopped being true when composition started working. A shell with `.env`
sourced makes the same tests call a model, take nine minutes instead of two, and
disagree with themselves depending on what a server said. Tests that pass or fail
on whether a key happens to be exported are not testing anything.

So the keys are cleared for every test. A test that wants a model asks for the
`with_models` fixture and says so, which also makes the ones that do stand out.
"""

from __future__ import annotations

import pytest

MODEL_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "DISCOVERY_API_KEY",
    "DISCOVERY_BASE_URL",
    "TYPESAFE_API_KEY",
    "TYPESAFE_BASE_URL",
)


@pytest.fixture(autouse=True)
def _no_models(request, monkeypatch):
    """Every test runs without credentials unless it asks for them."""
    if "with_models" in request.fixturenames:
        return
    for name in MODEL_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def with_models():
    """Opt back in, for a test that means to reach a real endpoint."""
    return True
