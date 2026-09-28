"""A model trained on Fireworks, served as an OpenAI-compatible chat endpoint for the web app.

The fine-tuned rearranger lives as a saved training state that only the
Fireworks training API can sample (`fireworks_sampler.FireworksSampler`), and
that SDK is installed on compute-box, not beside the web API. This small
server wraps the sampler in `POST /v1/chat/completions`, so the API's
`ModelChooser` talks to it like any other model server:

    python fireworks_chat_server.py --model <account>/<run>/sft-state --spend-file spend.json --port 8095

Every reply adds its prompt and sampled tokens to the spend file, in the format
`spend_watchdog` reads, so the night's ledger can stop it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fireworks_sampler import FireworksSampler


def _handler(sampler: FireworksSampler, model_name: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/v1/models":
                return self._send(200, {"object": "list", "data": [{"id": model_name, "object": "model"}]})
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._send(404, {"error": "not found"})
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            text = sampler(request.get("messages", []))
            self._send(200, {"id": f"chat-{time.time_ns()}", "object": "chat.completion", "model": model_name,
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": text}}]})

        def log_message(self, form: str, *args) -> None:
            print(f"{self.address_string()} {form % args}", flush=True)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="`base` or a saved training state reference")
    parser.add_argument("--spend-file", type=pathlib.Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()
    sampler = FireworksSampler(args.model, args.spend_file, args.temperature, args.max_tokens)
    server = ThreadingHTTPServer((args.host, args.port), _handler(sampler, args.model))
    print(f"serving {args.model} on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        sampler.close()


if __name__ == "__main__":
    main()
