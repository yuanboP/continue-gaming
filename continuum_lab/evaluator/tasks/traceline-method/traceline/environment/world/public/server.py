"""HTTP boundary for the private photographic inspection world."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import PUBLIC_CONTRACT, World, canonical_json


class Handler(BaseHTTPRequestHandler):
    server_version = "VisualInspection/3.1"

    def log_message(self, fmt, *args):
        # No request bodies, signing material or private state enter access logs.
        pass

    def send_json(self, status, value):
        body = canonical_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 16384:
            raise ValueError("request body is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request must be an object")
        return value

    def export(self):
        try:
            payload = self.server.world.export_payload()
        except ValueError as exc:
            self.send_json(409, {"error": str(exc)})
            return
        signature = hmac.new(self.server.signing_key, canonical_json(payload), hashlib.sha256).hexdigest()
        self.send_json(200, {"payload": payload, "signature": signature})

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/observe":
                self.send_json(200, self.server.world.observe(inline=parse_qs(url.query).get("inline") == ["1"]))
            elif url.path == "/frame.png" or (url.path.startswith("/inspection/") and url.path.endswith(".png")):
                world = self.server.world
                carrier_id = url.path[len("/inspection/"):-4] if url.path.startswith("/inspection/") else None
                try:
                    now, png = world.capture(carrier_id)
                except KeyError:
                    self.send_json(404, {"error": "carrier image is not available"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.send_header("X-Clock-Ms", str(now))
                self.send_header("X-Session-Id", world.session_id)
                if carrier_id:
                    self.send_header("X-Carrier-Id", carrier_id)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(png)
            elif url.path == "/export":
                self.export()
            else:
                self.send_json(404, {"error": "unknown endpoint"})
        except (ValueError, TypeError, KeyError) as exc:
            self.send_json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            body = self.body()
            path = urlparse(self.path).path
            if path == "/begin":
                if body:
                    raise ValueError("live/developer begin takes an empty object; use developer reset to choose a seed")
                self.send_json(200, self.server.world.begin())
            elif path == "/act":
                extra = set(body) - {"key", "id", "expires_ms", "session_id"}
                if extra:
                    raise ValueError("unsupported action fields: " + ", ".join(sorted(extra)))
                self.send_json(200, self.server.world.act(body))
            elif path == "/export":
                self.export()
            elif path == "/reset":
                if self.server.world.mode != "dev":
                    self.send_json(403, {"error": "live episodes cannot reset"})
                    return
                speed = body.get("speed", self.server.world.speed)
                if not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 1 <= speed <= 100:
                    raise ValueError("developer speed must be between 1 and 100")
                seed = body.get("seed", self.server.world.seed)
                if isinstance(seed, bool) or not isinstance(seed, int):
                    raise ValueError("seed must be an integer")
                with self.server.reset_lock:
                    prior = self.server.world
                    self.server.world = World(seed, prior.task_id, "dev", speed, prior.contract, prior.asset_catalog_path, prior.asset_root)
                    self.send_json(200, self.server.world.begin())
            else:
                self.send_json(404, {"error": "unknown endpoint"})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--mode", choices=["live", "dev"], default="live")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--contract-file")
    parser.add_argument("--asset-catalog", required=True)
    parser.add_argument("--asset-root")
    args = parser.parse_args()
    key = Path(args.key_file).read_bytes()
    if len(key) != 32:
        raise SystemExit("signing key must contain exactly 32 raw bytes")
    if not 1 <= args.speed <= 100 or (args.mode == "live" and args.speed != 1):
        raise SystemExit("live speed is exactly one; developer speed is 1..100")
    contract = json.loads(Path(args.contract_file).read_text()) if args.contract_file else PUBLIC_CONTRACT
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    server.world = World(args.seed, args.task_id, args.mode, args.speed, contract, args.asset_catalog, args.asset_root)
    server.signing_key = key
    server.reset_lock = threading.Lock()
    server.serve_forever(poll_interval=.05)


if __name__ == "__main__":
    main()
