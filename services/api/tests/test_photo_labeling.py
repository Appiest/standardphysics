import base64
import io
import json
import uuid

from PIL import Image

from conftest import create_scan, drain, put_artifact


def test_ingest_passes_uploaded_frames_to_the_default_astra_labeler(client, monkeypatch):
    room_object = {
        "identifier": str(uuid.uuid4()),
        "category": "chair",
        "confidence": "high",
        "dimensions": [0.5, 0.9, 0.5],
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -2, 1],
    }
    image = io.BytesIO()
    Image.new("RGB", (1600, 900), "red").save(image, format="JPEG")
    poses = [{
        "image": "frames/frame_0000.jpg",
        "orientation": "portrait",
        "intrinsics": [900, 0, 800, 0, 900, 450, 800, 450, 1],
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    }]
    captured = {}

    def transport(url, body, headers, *, deadline=None):
        assert deadline is not None
        captured["url"] = url
        captured["body"] = body
        content = body["messages"][1]["content"]
        context = json.loads(content[0]["text"])
        assert any(item["type"] == "image_url" for item in content)
        node = context["objects"][0]
        response = {"nodes": [{
            "id": node["id"], "label": "Chair", "movable": True,
            "quality": "measured", "appearance": None,
        }]}
        return {"choices": [{"message": {"content": json.dumps(response)}}]}

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("standardphysics_pipeline.astra._openrouter_post", transport)
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", json.dumps({"objects": [room_object]}).encode(), "room_json")
    put_artifact(client, scan_id, "room-usdz", b"usdz", "room_usdz")
    put_artifact(client, scan_id, "poses", json.dumps(poses).encode(), "poses")
    put_artifact(client, scan_id, "frame-0000", image.getvalue(), "frames")

    assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
    drain(client)
    scene = client.get(f"/api/scans/{scan_id}/scene").json()
    node = scene["nodes"][0]

    content = captured["body"]["messages"][1]["content"]
    image_url = next(item["image_url"]["url"] for item in content if item["type"] == "image_url")
    encoded = base64.b64decode(image_url.split(",", 1)[1])
    normalized = Image.open(io.BytesIO(encoded))
    assert normalized.height > normalized.width
    assert max(normalized.size) <= 1024
    assert node["labeled_by"] == "astra"
