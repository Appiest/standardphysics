"""Blind human rating tool for the 30 layout-quality pairs in rating_pairs.jsonl.

Serves one page at http://127.0.0.1:8791 showing two top-down floor plans side
by side: "which rearrangement looks better?" Which one lands on the left is
randomised per pair with a fixed seed, so this file's own contents settle it
rather than the reviewer's guess about it, and the page never shows the
source, Q, or reward that produced either layout.

    python scripts/finetune/rate_pairs.py            # start the server
    python scripts/finetune/rate_pairs.py --score     # print agreement with Q

Every answer is appended to RUN/ratings.jsonl the moment it's made, so closing
the tab loses nothing; reloading resumes at the first pair without an answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from rate_pairs_page import PAGE_TEMPLATE
from rate_pairs_plan import Scale, _build_layout, _dominant_wall_rotation, _room_points, _room_svg
from standardphysics_contracts import SceneGraph

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/finetune/multiroom"
PAIRS_PATH = RUN / "rating_pairs.jsonl"
VARIANTS_PATH = RUN / "variants.jsonl"
RATINGS_PATH = RUN / "ratings.jsonl"

HOST = "127.0.0.1"
PORT = 8791

LEFT_SEED = "rate-pairs-left-assignment-v1"
"""Mixed into the pair id to pick which side is shown on the left. Fixed, so a
rerun of the server reproduces the same layout instead of reshuffling it."""

DOOR_SWING_TERMS = ("wall", "pairs", "sight")


def _load_pairs() -> list[dict]:
    with PAIRS_PATH.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_variant_graphs() -> dict[str, dict]:
    graphs: dict[str, dict] = {}
    with VARIANTS_PATH.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if "graph" in row:
                graphs[row["variant_id"]] = row["graph"]
    return graphs


def left_is_a(pair_id: str) -> bool:
    """Deterministic, fixed-seed coin flip: whether side `a` renders on the left."""
    digest = hashlib.sha256(f"{LEFT_SEED}:{pair_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 2 == 0


class RatingStore:
    """Every answer ever given, keeping only the latest per pair so "back" can overwrite one."""

    def __init__(self, path: pathlib.Path):
        self.path = path

    def latest(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        latest: dict[str, dict] = {}
        with self.path.open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                latest[row["pair_id"]] = row
        return latest

    def append(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(json.dumps(entry) + "\n")


def _picked_side(picked_lr: str, left_was: str) -> str:
    """Translate a "left"/"right"/"tie" click back into "a"/"b"/"tie"."""
    if picked_lr == "tie":
        return "tie"
    right_was = "b" if left_was == "a" else "a"
    return left_was if picked_lr == "left" else right_was


def render_pair(pair: dict, base_graph_json: dict) -> dict:
    base_graph = SceneGraph.model_validate(base_graph_json)
    layout_a = _build_layout(pair["pair_id"], base_graph, pair["a"])
    layout_b = _build_layout(pair["pair_id"], base_graph, pair["b"])
    points = _room_points(layout_a.graph) + _room_points(layout_b.graph)
    rotation = _dominant_wall_rotation(base_graph)
    scale = Scale(points, rotation)
    left_a = left_is_a(pair["pair_id"])
    left_layout, right_layout = (layout_a, layout_b) if left_a else (layout_b, layout_a)
    return {
        "pair_id": pair["pair_id"],
        "left_was": "a" if left_a else "b",
        "left_svg": _room_svg(left_layout, scale),
        "right_svg": _room_svg(right_layout, scale),
    }


# --- scoring --------------------------------------------------------------


def _agreement(rows: list[dict], term: str) -> tuple[int, int]:
    matches, total = 0, 0
    for row in rows:
        if row["picked"] == "tie":
            continue
        pair = row["_pair"]
        picked_value = pair[row["picked"]]["q"][term]
        other_side = "b" if row["picked"] == "a" else "a"
        other_value = pair[other_side]["q"][term]
        if picked_value == other_value:
            continue
        total += 1
        matches += picked_value > other_value
    return matches, total


def run_score() -> None:
    pairs_by_id = {row["pair_id"]: row for row in _load_pairs()}
    ratings = RatingStore(RATINGS_PATH).latest()
    rows = []
    ties = 0
    for pair_id, entry in ratings.items():
        if pair_id not in pairs_by_id:
            continue
        picked = entry["picked"]
        if picked == "tie":
            ties += 1
        rows.append({"picked": picked, "_pair": pairs_by_id[pair_id]})

    print(f"{len(rows)} rated pairs, {ties} ties")
    for term in ("q", *DOOR_SWING_TERMS):
        matches, total = _agreement(rows, term)
        label = "overall" if term == "q" else term
        if total == 0:
            print(f"  {label}: no non-tie pairs with a difference")
            continue
        share = matches / total
        flag = "OK" if share >= 0.75 else "below 0.75 bar"
        print(f"  {label}: {matches}/{total} = {share:.1%} ({flag})")


# --- http server ------------------------------------------------------------


class Server:
    def __init__(self):
        self.pairs = _load_pairs()
        self.variant_graphs = _load_variant_graphs()
        self.ratings = RatingStore(RATINGS_PATH)
        self._rendered: dict[str, dict] = {}

    def render(self, index: int) -> dict:
        pair = self.pairs[index]
        if pair["pair_id"] not in self._rendered:
            self._rendered[pair["pair_id"]] = render_pair(pair, self.variant_graphs[pair["variant_id"]])
        return self._rendered[pair["pair_id"]]

    def state(self) -> dict:
        answered = self.ratings.latest()
        index = 0
        for pair in self.pairs:
            if pair["pair_id"] not in answered:
                break
            index += 1
        return {"index": index, "total": len(self.pairs), "done": index >= len(self.pairs)}

    def record_answer(self, pair_id: str, picked_lr: str, left_was: str) -> None:
        picked = _picked_side(picked_lr, left_was)
        self.ratings.append(
            {
                "pair_id": pair_id,
                "picked": picked,
                "left_was": left_was,
                "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            }
        )


def make_handler(server: Server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - stdlib method name
            path = urlparse(self.path).path
            if path == "/":
                body = PAGE_TEMPLATE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/state":
                self._json(server.state())
                return
            if path.startswith("/api/pair/"):
                try:
                    index = int(path.rsplit("/", 1)[-1])
                    payload = server.render(index)
                except (ValueError, IndexError, KeyError):
                    self._json({"error": "no such pair"}, status=404)
                    return
                self._json(payload)
                return
            self._json({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802 - stdlib method name
            path = urlparse(self.path).path
            if path != "/api/answer":
                self._json({"error": "not found"}, status=404)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            server.record_answer(body["pair_id"], body["picked"], body["left_was"])
            self._json({"ok": True})

    return Handler


def run_server() -> None:
    server = Server()
    httpd = ThreadingHTTPServer((HOST, PORT), make_handler(server))
    print(f"rate_pairs serving on http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true", help="print agreement between human picks and Q, then exit")
    args = parser.parse_args()
    if args.score:
        run_score()
        return
    run_server()


if __name__ == "__main__":
    main()
