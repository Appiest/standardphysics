"""Serve the repository's mock upload contract on a reachable development host."""

import argparse
import runpy
from http.server import ThreadingHTTPServer
from pathlib import Path

mock_path = Path(__file__).resolve().parents[3] / "packages" / "fixtures" / "standardphysics_fixtures" / "mock_api.py"
Handler = runpy.run_path(str(mock_path))["Handler"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Your Mac's private LAN IP address")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    print(f"Development upload server: http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
