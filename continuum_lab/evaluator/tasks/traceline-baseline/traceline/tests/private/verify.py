#!/usr/bin/env python3
"""Verify an exported world record before reducing its signed raw events.

Runs only in Harbor's separate verifier container.  No artifact code is imported.
The agent cannot access this directory, the scoring implementation, or the key.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

MAX_SESSION_BYTES = 32 * 1024 * 1024


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(data: bytes) -> Any:
    def nonfinite(value: str) -> None:
        raise ValueError(f"Nonfinite JSON number: {value}")
    return json.loads(data, object_pairs_hook=no_duplicate_keys, parse_constant=nonfinite)


def authenticate(envelope: dict, key: bytes, *, task_id: str,
                 seed: int | None = None) -> dict:
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ValueError("Session must contain exactly payload and signature")
    payload, signature = envelope["payload"], envelope["signature"]
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise ValueError("Invalid session envelope types")
    if len(key) < 32:
        raise ValueError("Verifier signing key is too short")
    expected = hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("World signature mismatch")
    if payload.get("task_id") != task_id:
        raise ValueError("Session belongs to a different task")
    if seed is not None and payload.get("seed") != seed:
        raise ValueError("Session belongs to a different seed")
    if payload.get("mode") != "live":
        raise ValueError("Only the single live episode may be submitted")
    return payload


def load_score(path: Path):
    spec = importlib.util.spec_from_file_location("private_task_score", path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load private scorer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.score_payload


def grade(artifact: Path, key_path: Path, expected_path: Path,
          scorer_path: Path) -> dict:
    if artifact.stat().st_size > MAX_SESSION_BYTES:
        raise ValueError("Session exceeds 32 MiB")
    data = artifact.read_bytes()
    if len(data) > MAX_SESSION_BYTES:
        raise ValueError("Session exceeds 32 MiB")
    expected = parse_json(expected_path.read_bytes())
    envelope = parse_json(data)
    payload = authenticate(envelope, key_path.read_bytes(),
                           task_id=expected["task_id"], seed=expected["seed"])
    result = load_score(scorer_path)(payload, expected["contract"])
    if not isinstance(result, dict) or type(result.get("passed")) is not bool:
        raise ValueError("Private scorer must return a dictionary with boolean passed")
    canonical_json(result)  # Reject nonfinite or non-JSON outputs.
    return {"schema_version": 1, "integrity_passed": True,
            "passed": result["passed"], "task_id": expected["task_id"],
            "artifact_sha256": hashlib.sha256(data).hexdigest(),
            "signed_payload_sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
            "scoring": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=Path("/app/output/session.json"))
    parser.add_argument("--key", type=Path, default=Path("/tests/signing.key"))
    parser.add_argument("--expected", type=Path, default=Path("/tests/expected.json"))
    parser.add_argument("--scorer", type=Path, default=Path("/tests/score.py"))
    parser.add_argument("--output", type=Path, default=Path("/logs/verifier"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        result = grade(args.artifact, args.key, args.expected, args.scorer)
    except Exception as exc:
        result = {"schema_version": 1, "integrity_passed": False, "passed": False,
                  "error_type": type(exc).__name__, "error": str(exc)}
    (args.output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    reward = {"reward": float(result["passed"])}
    (args.output / "reward.json").write_text(json.dumps(reward) + "\n")
    if args.artifact.is_file() and args.artifact.stat().st_size <= MAX_SESSION_BYTES:
        (args.output / "session.json").write_bytes(args.artifact.read_bytes())
    print(json.dumps(result, sort_keys=True))
    return 0  # A failed task is reward 0; verifier infrastructure still completed.


if __name__ == "__main__":
    raise SystemExit(main())
