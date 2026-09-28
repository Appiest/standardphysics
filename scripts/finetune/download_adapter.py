"""Download a promoted Fireworks LoRA model's files once it is READY.

    python scripts/finetune/download_adapter.py accounts/<account>/models/<id> runs/finetune/room6/qwen3p8-27b/sft
    python scripts/finetune/download_adapter.py --progress docs/progress/PROGRESS_FINETUNE.json runs/finetune/room6/qwen3p8-27b

Uses the REST API's model download endpoint (signed URLs). Files already on
disk at the expected size are skipped, so a rerun only fetches what is missing.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.request

API = "https://api.fireworks.ai/v1"
READY_TIMEOUT_SECONDS = 3600


def _get(path: str) -> dict:
    request = urllib.request.Request(f"{API}/{path}", headers={"Authorization": f"Bearer {os.environ['FIREWORKS_API_KEY']}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def wait_until_ready(model: str) -> dict:
    deadline = time.time() + READY_TIMEOUT_SECONDS
    while True:
        described = _get(model)
        if described.get("state") == "READY" or time.time() > deadline:
            return described
        time.sleep(30)


def download(model: str, destination: pathlib.Path) -> list[str]:
    described = wait_until_ready(model)
    if described.get("state") != "READY":
        raise RuntimeError(f"{model} is {described.get('state')}, not READY")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "model.json").write_text(json.dumps(described, indent=2))
    urls = _get(f"{model}:getDownloadEndpoint").get("filenameToSignedUrls", {})
    for filename, url in urls.items():
        target = destination / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            urllib.request.urlretrieve(url, target)
    return sorted(urls)


def download_promoted(progress_path: pathlib.Path, out: pathlib.Path) -> None:
    """Every promoted adapter recorded in docs/progress/PROGRESS_FINETUNE.json that is not downloaded yet."""
    from progress import Progress

    progress = Progress(progress_path)
    for stage in ("sft", "rl"):
        promoted = progress.get(f"promote_{stage}")
        if promoted.get("status") != "done" or progress.done(f"download_{stage}"):
            continue
        files = download(promoted["model"], out / stage)
        progress.record(f"download_{stage}", status="done", model=promoted["model"], path=str(out / stage), files=files)


def main() -> None:
    if sys.argv[1] == "--progress":
        download_promoted(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
        return
    model, destination = sys.argv[1], pathlib.Path(sys.argv[2])
    print(json.dumps({"model": model, "files": download(model, destination)}))


if __name__ == "__main__":
    main()
