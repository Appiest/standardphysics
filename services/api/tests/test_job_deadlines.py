"""Every job kind has a deadline, and a job past it stops at the next stage boundary without saving what it made."""

from evidence_uploads import complete_geometry, complete_semantics, discovering_stages, job_of_kind
from standardphysics_pipeline.discovery import DiscoveryResult

from conftest import create_scan, drain


def _seeded_shop(client) -> str:
    return client.get("/api/scans").json()["scans"][0]["id"]


def test_a_process_job_past_its_deadline_stops_before_saving_a_revision(make_client):
    stages = discovering_stages(lambda inputs: DiscoveryResult())
    with make_client(stages=stages, evidence_settle_seconds=0.0, process_timeout_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        job = job_of_kind(client, scan_id, "process")
        with client.app.state.database.connect() as connection:
            revisions = connection.execute("SELECT 1 FROM revisions WHERE scan_id = ?", (scan_id,)).fetchall()
    assert job["state"] == "failed"
    assert "The process job did not finish within 0 seconds and was stopped" in job["error"]
    assert revisions == []


def test_an_assess_job_past_its_deadline_stops_before_saving_its_assessment(make_client):
    with make_client(seed=True, assess_timeout_seconds=0.0) as client:
        scan_id = _seeded_shop(client)
        drain(client)
        job = job_of_kind(client, scan_id, "assess")
        with client.app.state.database.connect() as connection:
            assessments = connection.execute("SELECT 1 FROM assessments WHERE scan_id = ?", (scan_id,)).fetchall()
    assert job["state"] == "failed"
    assert "The assess job did not finish within 0 seconds" in job["error"]
    assert assessments == []


def test_a_display_job_past_its_deadline_stops_before_drawing_stills(make_client):
    from conftest import no_blender_stages

    rendered = []

    def render_finding(graph, locus, out):
        rendered.append(out)
        return out

    stages = no_blender_stages(render_finding=render_finding)
    with make_client(seed=True, display_timeout_seconds=0.0, stages=stages) as client:
        scan_id = _seeded_shop(client)
        drain(client)
        job = job_of_kind(client, scan_id, "display")
    assert job["state"] == "failed"
    assert "The display job did not finish within 0 seconds" in job["error"]
    assert rendered == []


def test_a_simulation_past_its_deadline_is_stopped_and_says_so(make_client):
    with make_client(seed=True, team=True, simulate_timeout_seconds=0.0) as client:
        scan_id = _seeded_shop(client)
        drain(client)
        response = client.post(f"/api/scans/{scan_id}/simulations", json={"base_revision": 0, "samples": 2})
        assert response.status_code == 202, response.text
        drain(client)
        state = client.get(f"/api/scans/{scan_id}/simulations?revision=0").json()
    assert state["state"] == "failed"
    assert "The simulate job did not finish within 0 seconds" in state["error"]
    assert state.get("result") is None


def test_every_job_kind_has_a_configurable_deadline(monkeypatch):
    from standardphysics_api.settings import Settings

    for name in ("PROCESS", "ASSESS", "DISPLAY", "SIMULATE", "BAKE"):
        monkeypatch.delenv(f"SP_{name}_TIMEOUT_SECONDS", raising=False)
    defaults = Settings.from_environment()
    kinds = ("process", "assess", "display", "simulate", "texture")
    assert [defaults.job_deadline_seconds(kind) for kind in kinds] == [3600, 1200, 1800, 14400, 2700]
    monkeypatch.setenv("SP_DISPLAY_TIMEOUT_SECONDS", "600")
    assert Settings.from_environment().job_deadline_seconds("display") == 600
