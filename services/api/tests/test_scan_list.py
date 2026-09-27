"""The shop list stays small however many photos a walk kept.

A library floor keeps thousands of photos per walk, and the list used to carry
a record for every one: ten Moffitt scans came to 2.3 MB and 3.7 seconds before
the home page could draw. The list leaves photos out; the scan itself keeps them.
"""

from conftest import create_scan, put_artifact


def test_the_list_leaves_out_photos_the_scan_still_has(client):
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "frame-0000", b"frame-0000", "frames")
    put_artifact(client, scan_id, "frame-0001", b"frame-0001", "frames")
    put_artifact(client, scan_id, "poses", b"poses", "poses")

    listed = next(scan for scan in client.get("/api/scans").json()["scans"] if scan["id"] == scan_id)
    fetched = client.get(f"/api/scans/{scan_id}").json()

    assert [artifact["id"] for artifact in listed["artifacts"]] == ["poses"]
    assert sorted(artifact["id"] for artifact in fetched["artifacts"]) == ["frame-0000", "frame-0001", "poses"]
