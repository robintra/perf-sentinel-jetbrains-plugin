#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "src/test/resources/rider-smoke/file-line.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve one Perf Sentinel findings fixture.")
    parser.add_argument("fixture", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--port", type=int, default=4318)
    args = parser.parse_args()

    payload = args.fixture.read_bytes()
    json.loads(payload)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if urlsplit(self.path).path != "/api/findings":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Serving {args.fixture} at http://127.0.0.1:{args.port}/api/findings")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
