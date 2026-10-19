#!/usr/bin/env python3
"""Serve the TraceLine presentation and proxy only its public scene API.

This adapter has no world engine, data catalog, labels, model, or action queue.
The authoritative scene server remains responsible for clocks and scoring.
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

ROOT = Path(__file__).resolve().parent
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/inspection-preview.png": ("assets/inspection-preview.png", "image/png"),
}
MAX_BODY = 16384
MAX_RESPONSE = 40 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_world_url(value):
    if not value:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("world URL must be an http(s) origin without credentials, path, query, or fragment")
    return value.rstrip("/")


def allowed_endpoint(method, path):
    if method == "GET":
        return path in ("/observe", "/frame.png", "/export") or bool(re.fullmatch(r"/inspection/[A-Za-z0-9_-]{1,128}\.png", path))
    return method == "POST" and path in ("/begin", "/act", "/export")


class Handler(BaseHTTPRequestHandler):
    server_version = "SignalLine/1.0"

    def log_message(self, fmt, *args):
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'self'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def error(self, status, message):
        self.respond(status, json.dumps({"error": message}).encode())

    def dispatch(self):
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            return self.error(400, "absolute request targets are not accepted")
        if self.command == "GET" and parsed.path in STATIC:
            relative, content_type = STATIC[parsed.path]
            return self.respond(200, (self.server.site_root / relative).read_bytes(), content_type)
        if not parsed.path.startswith("/api/"):
            return self.error(404, "unknown endpoint")
        endpoint = parsed.path[4:]
        if not allowed_endpoint(self.command, endpoint) or parsed.query or parsed.fragment:
            return self.error(404, "endpoint is not exposed by this presentation")
        if not self.server.world_url:
            return self.error(503, "No scene configured. Open /?preview=1 for the static design preview.")
        # Prevent an unrelated web origin from firing an actuator through a local proxy.
        origin = self.headers.get("Origin")
        if self.command == "POST" and origin and origin != "http://" + self.headers.get("Host", ""):
            return self.error(403, "cross-origin action is not accepted")
        body = None
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.error(400, "invalid content length")
            if length < 0 or length > MAX_BODY:
                return self.error(413, "request body is too large")
            body = self.rfile.read(length)
            try:
                if not isinstance(json.loads(body or b"{}"), dict):
                    raise ValueError("request must be a JSON object")
            except (ValueError, TypeError):
                return self.error(400, "request must be a JSON object")
        request = Request(self.server.world_url + endpoint, data=body,
                          headers={"Content-Type": "application/json"} if body is not None else {},
                          method=self.command)
        try:
            response = self.server.opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        except (URLError, TimeoutError, OSError):
            return self.error(502, "scene service is unavailable")
        with response:
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                return self.error(502, "scene response exceeds presentation size limit")
            public_headers = {name: response.headers[name] for name in
                              ("X-Clock-Ms", "X-Session-Id", "X-Carrier-Id") if name in response.headers}
            # Preserve exact world response bytes, including signed session exports.
            return self.respond(response.code, data, response.headers.get("Content-Type", "application/octet-stream"), public_headers)

    def do_GET(self):
        try:
            self.dispatch()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        self.do_GET()


def make_server(host, port, world_url=None, site_root=None):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.world_url = validate_world_url(world_url)
    server.site_root = Path(site_root or ROOT / "site").resolve()
    server.opener = build_opener(ProxyHandler({}), NoRedirect())
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8138)
    parser.add_argument("--world-url", default=os.environ.get("OPS_URL"))
    parser.add_argument("--site-root", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    server = make_server(args.host, args.port, args.world_url, args.site_root)
    mode = "public scene connected" if server.world_url else "static preview only"
    print(f"TraceLine: http://{args.host}:{server.server_port}/?preview={0 if server.world_url else 1} ({mode})", flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
