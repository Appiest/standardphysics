from standardphysics_agents import VerificationLedger

from conftest import drain, no_blender_stages


def test_the_report_carries_findings_and_who_verified_each_rule(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        report = client.get(f"/api/scans/{scan_id}/report").json()
        assert report["scan"]["name"] == "Sample boba shop"
        assert report["scenario"]["stops"][0]["name"] == "Entrance"
        assert report["assessment"]["findings"]
        route = next(r for r in report["rules"] if r["check"]["id"] == "route_clear_width")
        assert route["check"]["citation"]["section"] == "403.5.1"
        assert route["verified_by"].startswith("unverified preview")


def test_an_unreviewed_rule_pack_lists_no_rules(make_client):
    with make_client(seed=True, stages=no_blender_stages(ledger_factory=VerificationLedger)) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        assert client.get(f"/api/scans/{scan_id}/report").json()["rules"] == []


def test_a_preview_report_says_so_and_claims_no_human_review(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        report = client.get(f"/api/scans/{scan_id}/report").json()
        assert report["preview"] is True
        assert not any(rule["check"]["verified_by_human"] for rule in report["rules"])
