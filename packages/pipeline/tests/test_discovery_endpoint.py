"""Discovery sends each host its own key and a model that host serves, as deploy/digitalocean/env.example sets it up."""

import pytest
from standardphysics_pipeline.discovery import detector_transport

FIREWORKS = "https://api.fireworks.ai/inference/v1"


@pytest.fixture
def unset(monkeypatch):
    for name in ("DISCOVERY_MODEL", "DISCOVERY_API_KEY", "DISCOVERY_BASE_URL", "OPENROUTER_BASE_URL",
                 "OPENROUTER_API_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_pointed_at_fireworks_with_the_model_left_blank_it_uses_the_fireworks_key_and_a_fireworks_model(unset):
    unset.setenv("DISCOVERY_BASE_URL", FIREWORKS)
    unset.setenv("DISCOVERY_MODEL", "")
    unset.setenv("OPENROUTER_API_KEY", "openrouter-key")
    unset.setenv("FIREWORKS_API_KEY", "fireworks-key")
    assert detector_transport.configured_api_key() == "fireworks-key"
    assert detector_transport.answer_model().startswith("accounts/fireworks/models/")


def test_left_unset_it_goes_to_openrouter_with_the_openrouter_key(unset):
    unset.setenv("OPENROUTER_API_KEY", "openrouter-key")
    unset.setenv("FIREWORKS_API_KEY", "fireworks-key")
    assert detector_transport.base_url() == "https://openrouter.ai/api/v1"
    assert detector_transport.configured_api_key() == "openrouter-key"
    assert detector_transport.answer_model() == "google/gemini-3.8-flash"


def test_a_discovery_key_and_model_given_outright_win(unset):
    unset.setenv("DISCOVERY_BASE_URL", FIREWORKS)
    unset.setenv("DISCOVERY_API_KEY", "discovery-key")
    unset.setenv("FIREWORKS_API_KEY", "fireworks-key")
    unset.setenv("DISCOVERY_MODEL", "accounts/fireworks/models/some-vision-model")
    assert detector_transport.configured_api_key() == "discovery-key"
    assert detector_transport.answer_model() == "accounts/fireworks/models/some-vision-model"
