#!/usr/bin/env python3
"""Public atomic client, identical in both experiment arms.

This module performs HTTP requests and saves observations. It contains no action
queue, image analysis, target matching, timing prediction or control policy.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class OperationsClient:
    def __init__(self, url: str | None = None, timeout: float = 10):
        self.url = (url or os.environ.get("OPS_URL") or os.environ.get("WORLD_URL") or "").rstrip("/")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("Provide an http(s) --url, OPS_URL or WORLD_URL")
        self.timeout = timeout

    def request(self, endpoint: str, *, payload=None, method: str = "GET"):
        encoded = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        headers = {} if encoded is None else {"Content-Type": "application/json"}
        request = Request(self.url + "/" + endpoint.lstrip("/"),
                          data=encoded, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read(), dict(response.headers.items())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {detail[:2000]}") from None

    def json(self, endpoint: str, *, payload=None, method: str = "GET"):
        data, _ = self.request(endpoint, payload=payload, method=method)
        return json.loads(data)

    def observe(self, *, inline: bool = False):
        return self.json("observe?inline=1" if inline else "observe")

    def capture(self, output: str | Path):
        data, headers = self.request("frame.png")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Frame endpoint did not return PNG")
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lower = {key.lower(): value for key, value in headers.items()}
        clock = lower.get("x-clock-ms")
        return {"frame_path": str(target.resolve()),
                "frame_sha256": hashlib.sha256(data).hexdigest(),
                "clock_ms": None if clock is None else float(clock),
                "session_id": lower.get("x-session-id")}

    def act(self, key: str, command_id: str, *, expires_ms=None, session_id=None):
        payload = {"key": key, "id": command_id}
        if expires_ms is not None:
            payload["expires_ms"] = expires_ms
        if session_id is not None:
            payload["session_id"] = session_id
        return self.json("act", payload=payload, method="POST")

    def begin(self):
        return self.json("begin", payload={}, method="POST")

    def reset(self, seed=1, speed=1):
        """Developer endpoints only. The live server rejects reset."""
        return self.json("reset", payload={"seed": seed, "speed": speed}, method="POST")

    def export(self, output: str | Path = "/app/output/session.json"):
        data, _ = self.request("export")
        json.loads(data)  # Fail before writing if the server did not return JSON.
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)  # Preserve the exact authoritative export bytes.
        return {"output": str(target.resolve()), "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Public world endpoint; defaults to OPS_URL or WORLD_URL")
    parser.add_argument("--timeout", type=float, default=10)
    commands = parser.add_subparsers(dest="command", required=True)
    observation = commands.add_parser("observe", help="Read public metadata; optionally retain its exact PNG")
    observation.add_argument("--save-frame", type=Path)
    frame = commands.add_parser("frame", help="Save a current PNG with capture time returned on stdout")
    frame.add_argument("--output", type=Path, required=True)
    action = commands.add_parser("act", help="Execute one key immediately; never schedule a future key")
    action.add_argument("key", choices=("d", "f", "j", "k"))
    action.add_argument("--id", required=True)
    action.add_argument("--expires-ms", type=float)
    action.add_argument("--session-id")
    commands.add_parser("begin", help="Begin the official wall-clock session")
    development = commands.add_parser("dev", help="Reset the separate DEV_OPS_URL practice world")
    development.add_argument("--seed", type=int, default=1)
    development.add_argument("--speed", type=float, default=1)
    export = commands.add_parser("export", help="Save the authoritative signed session artifact")
    export.add_argument("--output", type=Path, default=Path("/app/output/session.json"))
    args = parser.parse_args(argv)
    try:
        endpoint = args.url
        if args.command == "dev" and endpoint is None:
            endpoint = os.environ.get("DEV_OPS_URL")
            if not endpoint:
                raise ValueError("dev requires DEV_OPS_URL or an explicit --url to the developer world")
        client = OperationsClient(endpoint, args.timeout)
        if args.command == "observe":
            result = client.observe(inline=args.save_frame is not None)
            if args.save_frame is not None:
                encoded = result.pop("image_base64", None)
                if not isinstance(encoded, str):
                    raise ValueError("Inline observation did not contain image_base64")
                image = base64.b64decode(encoded, validate=True)
                if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Inline image is not PNG")
                args.save_frame.parent.mkdir(parents=True, exist_ok=True)
                args.save_frame.write_bytes(image)
                result.update(frame_path=str(args.save_frame.resolve()),
                              frame_sha256=hashlib.sha256(image).hexdigest())
        elif args.command == "frame":
            result = client.capture(args.output)
        elif args.command == "act":
            result = client.act(args.key, args.id, expires_ms=args.expires_ms, session_id=args.session_id)
        elif args.command == "begin":
            result = client.begin()
        elif args.command == "dev":
            result = client.reset(args.seed, args.speed)
        else:
            result = client.export(args.output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Public API error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
