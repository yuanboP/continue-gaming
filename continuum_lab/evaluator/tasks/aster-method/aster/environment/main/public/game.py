#!/usr/bin/env python3
"""Shared public pixel/action client. No policy and no access to private state."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import urllib.request


class Game:
    def __init__(self, url=None):
        self.url = (url or os.environ.get("GAME_URL", "http://world:8080")).rstrip("/")

    def request(self, path, data=None, timeout=30):
        req = urllib.request.Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)

    def begin(self):
        return self.request("/begin", {})

    def freeze(self, directory):
        root = Path(directory).resolve()
        if not (root / "solve.sh").is_file():
            raise ValueError("Controller needs solve.sh")
        manifest = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in {".git", "__pycache__", ".DS_Store"} for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError("Controller must be self contained; no symlinks")
            if path.is_file():
                manifest[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return self.request("/freeze_controller", {"manifest": manifest})

    def observe(self, path=None):
        result = self.request("/observe")
        if path:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(result["image_base64"], validate=True))
            result = {k: v for k, v in result.items() if k != "image_base64"}
            result["image_path"] = str(target)
        return result

    def act(self, plan, request_id=None):
        return self.request("/act", {"plan": plan, **({"id": request_id} if request_id else {})})

    def status(self):
        return self.request("/status")

    def export(self, directory=None, include_video=None):
        directory = Path(directory or os.environ.get("OUTDIR", "/app/output"))
        directory.mkdir(parents=True, exist_ok=True)
        self.observe(directory / "final.png")
        envelope = self.request("/export")
        target = directory / "session.json"
        target.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
        with urllib.request.urlopen(self.url + "/trace", timeout=60) as response:
            (directory / "world-trace.zip").write_bytes(response.read())
        if include_video is None:
            include_video = self.request("/health").get("role") == "live"
        result = {"artifact": str(target), "world_trace": str(directory / "world-trace.zip")}
        if include_video:
            with urllib.request.urlopen(self.url + "/video", timeout=60) as response:
                (directory / "native-game.webm").write_bytes(response.read())
            result["native_recording"] = str(directory / "native-game.webm")
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=("health", "freeze", "begin", "observe", "act", "status", "export"))
    p.add_argument("--url")
    p.add_argument("--image", default="/app/output/observation.png")
    p.add_argument("--plan", help="Literal JSON array of native action chunks")
    p.add_argument("--output")
    p.add_argument("--controller", default="/app/output/controller")
    a = p.parse_args()
    game = Game(a.url)
    if a.operation == "health":
        result = game.request("/health")
    elif a.operation == "freeze":
        result = game.freeze(a.controller)
    elif a.operation == "observe":
        result = game.observe(a.image)
    elif a.operation == "act":
        result = game.act(json.loads(a.plan))
    elif a.operation == "export":
        result = game.export(a.output)
    else:
        result = getattr(game, a.operation)()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
