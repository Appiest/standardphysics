"""A stand-in OpenAI-compatible model server that answers however a test tells it to."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def completion(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()


@dataclass
class Reply:
    body: bytes = field(default_factory=lambda: completion(""))
    delay: float = 0.0
    """Seconds before the status line is sent."""
    drip: float = 0.0
    """Seconds between each byte of the body, for a server that answers but never finishes."""
    declare_length: bool = True


@dataclass
class ModelProvider:
    url: str
    reply: Reply = field(default_factory=Reply)
    answer: object = None
    """When set, called with the request's messages to build each reply instead of `reply`."""
    received: threading.Event = field(default_factory=threading.Event)
    release: threading.Event | None = None
    """When set, every request waits for it before answering."""

    def reply_to(self, messages: list[dict]) -> Reply:
        self.received.set()
        if self.release is not None:
            self.release.wait(10)
        return self.answer(messages) if callable(self.answer) else self.reply


def _handler(provider: ModelProvider) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = provider.reply_to(request["messages"])
            time.sleep(reply.delay)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if reply.declare_length:
                self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            self._send(reply)

        def _send(self, reply: Reply) -> None:
            try:
                if not reply.drip:
                    self.wfile.write(reply.body)
                    return
                for byte in reply.body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(reply.drip)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def serve_provider() -> Iterator[ModelProvider]:
    provider = ModelProvider(url="")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(provider))
    server.daemon_threads = True
    provider.url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield provider
    finally:
        if provider.release is not None:
            provider.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
