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


def test_the_report_carries_the_owners_choices_and_the_plan_they_kept(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        first = client.get(f"/api/scans/{scan_id}/checklist").json()["items"][0]["finding_id"]
        client.put(f"/api/scans/{scan_id}/checklist/{first}", json={"status": "needs_pro"})
        chair = next(node for node in client.get(f"/api/scans/{scan_id}/scene").json()["nodes"] if node["movable"])
        move = {"node_id": chair["id"], "delta_translation": {"x": 0.05, "y": 0.0, "z": 0.0}, "delta_rotation_z_degrees": 0.0}
        kept = client.post(f"/api/scans/{scan_id}/plans", json={"base_revision": 0, "moves": [move]}).json()
        report = client.get(f"/api/scans/{scan_id}/report").json()
        statuses = {item["finding_id"]: item["status"] for item in report["checklist"]["items"]}
        assert statuses[first] == "needs_pro"
        assert report["plan"]["id"] == kept["id"] and report["plan"]["moves"][0]["node_id"] == chair["id"]


def test_a_report_without_a_plan_says_so(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        assert client.get(f"/api/scans/{scan_id}/report").json()["plan"] is None
