"""With a model configured, "See a layout that fixes this" returns the model's pick from the menu."""

import json
import re

from standardphysics_api.model_chooser import ModelChooser

from conftest import drain


def _sample(make_client):
    client = make_client(seed=True).__enter__()
    drain(client)
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    findings = client.get(f"/api/scans/{scan_id}/assessment").json()["findings"]
    aisle = next(f for f in findings if f["title"] == "The path to the counter is too narrow")
    return client, scan_id, aisle["id"]


def _propose(client, scan_id, finding_id):
    return client.post(f"/api/scans/{scan_id}/proposals", json={"base_revision": 0, "finding_ids": [finding_id]}).json()


def _first_option(messages):
    options = json.loads(messages[-1]["content"])["options"]
    return json.dumps({"choose": [options[0]["option"]], "why": "It opens the aisle with one short slide."})


def test_no_model_configured_keeps_the_search(make_client, monkeypatch):
    monkeypatch.delenv("SP_MENU_MODEL_URL", raising=False)
    client, scan_id, finding_id = _sample(make_client)
    assert not _propose(client, scan_id, finding_id)["message"].startswith("The model picked")


def test_a_configured_model_picks_from_the_menu_for_the_finding_asked_about(make_client, monkeypatch):
    asked = []

    def ask(self, messages):
        asked.append(messages)
        return _first_option(messages)

    monkeypatch.setenv("SP_MENU_MODEL_URL", "http://model.test/v1")
    monkeypatch.setenv("SP_MENU_MODEL", "test-model")
    monkeypatch.setattr(ModelChooser, "ask", ask)
    client, scan_id, finding_id = _sample(make_client)
    result = _propose(client, scan_id, finding_id)
    assert asked and result["proposal"]["moves"]
    assert re.match(r"The model picked: .+\. Its reason: It opens the aisle", result["message"])
    assert result["explanation"]["fixed"]


def test_a_model_that_picks_nothing_falls_back_to_the_search(make_client, monkeypatch):
    monkeypatch.setenv("SP_MENU_MODEL_URL", "http://model.test/v1")
    monkeypatch.setenv("SP_MENU_MODEL", "test-model")
    monkeypatch.setattr(ModelChooser, "ask", lambda self, messages: "I am not sure.")
    client, scan_id, finding_id = _sample(make_client)
    result = _propose(client, scan_id, finding_id)
    assert result["proposal"] is not None and not result["message"].startswith("The model picked")
