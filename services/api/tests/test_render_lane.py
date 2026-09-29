"""Display renders run on the Blender lane, beside texture bakes and never on the jobs lane.

A render used to hold the jobs lane for as long as Blender took, so a shop's
re-checks and a fresh upload waited behind stills for a scan from days before.
"""

import uuid
from datetime import UTC, datetime

from standardphysics_contracts import Assessment, Citation, Finding

from conftest import create_scan
from standardphysics_api import repository as repo


def _assessment(scan_id: uuid.UUID, title: str) -> Assessment:
    finding = Finding(id=uuid.uuid4(), check_id="route_clear_width", outcome="question", title=title, detail="",
                      citation=Citation(authority="ADA_2010", edition="2010 ADA Standards", section="403.5.1"))
    return Assessment(id=uuid.uuid4(), scan_id=scan_id, graph_revision=0, graph_hash="h", rulepack_version="1",
                      pass_number=1, created_at=datetime.now(UTC), findings=[finding])


def test_the_jobs_lane_leaves_a_render_for_the_blender_lane(client):
    shop, new_upload = uuid.UUID(create_scan(client)), uuid.UUID(create_scan(client))
    with client.app.state.database.transaction() as connection:
        repo.enqueue_job(connection, shop, "display", 0)
        repo.enqueue_job(connection, new_upload, "process", 0)

        from_jobs_lane = repo.claim_job(connection, False)
        from_blender_lane = repo.claim_job(connection, True)

    assert from_jobs_lane["kind"] == "process"
    assert from_blender_lane["kind"] == "display"


def test_a_render_overtaken_by_a_recheck_draws_again_for_the_new_findings(client, monkeypatch, tmp_path):
    scan_id = uuid.UUID(create_scan(client))
    database, worker = client.app.state.database, client.app.state.worker
    first, recheck = _assessment(scan_id, "first"), _assessment(scan_id, "recheck")
    with database.transaction() as connection:
        repo.save_assessment(connection, first)
    drawn: list[uuid.UUID] = []

    def renders(graph, assessment, directory, url_for):
        drawn.append(assessment.id)
        if len(drawn) == 1:
            with database.transaction() as connection:
                repo.save_assessment(connection, recheck)
        stills = [finding.model_copy(update={"title": f"{finding.title} drawn"}) for finding in assessment.findings]
        return assessment.model_copy(update={"findings": stills})

    monkeypatch.setattr(worker.stages, "renders", renders)
    worker._render_until_current(scan_id, 0, None, first, tmp_path)

    with database.connect() as connection:
        latest = repo.assessment_for_revision(connection, scan_id, 0)
    assert drawn == [first.id, recheck.id]
    assert latest is not None
    assert (latest.id, latest.findings[0].title) == (recheck.id, "recheck drawn")
