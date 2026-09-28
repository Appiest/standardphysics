"""A deploy that changes what decides a result checks every shop again, and leaves current results alone."""

from conftest import drain
from standardphysics_agents.checks_version import checks_version
from standardphysics_api.worker import ASSESS


def _versions(client):
    with client.app.state.database.connect() as connection:
        return [row["checks_version"] for row in connection.execute("SELECT checks_version FROM assessments")]


def _assess_jobs(client, state):
    with client.app.state.database.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM jobs WHERE kind = ? AND state = ?", (ASSESS, state)).fetchone()[0]


def test_an_assessment_records_the_checks_it_was_made_under(make_client):
    with make_client(seed=True) as client:
        drain(client)
        assert _versions(client) and set(_versions(client)) == {checks_version()}


def test_results_made_under_older_checks_are_checked_again_at_startup(make_client):
    with make_client(seed=True) as client:
        drain(client)
        with client.app.state.database.transaction() as connection:
            connection.execute("UPDATE assessments SET checks_version = 'older'")
        assert client.app.state.worker.recheck_stale_results() == 1
        assert _assess_jobs(client, "queued") == 1
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        assert client.get(f"/api/scans/{scan_id}").json()["state"] == "ready"
        with client.app.state.database.connect() as connection:
            latest = connection.execute("SELECT checks_version FROM assessments ORDER BY created_at DESC LIMIT 1").fetchone()
        assert latest["checks_version"] == checks_version()


def test_current_results_are_left_alone(make_client):
    with make_client(seed=True) as client:
        drain(client)
        assert client.app.state.worker.recheck_stale_results() == 0
        assert _assess_jobs(client, "queued") == 0
