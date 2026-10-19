"""Pure validation/serialization helpers; importing never launches a world."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from pathlib import PurePosixPath

ENGINE_SHA256 = "348e81340d4a09f8b488193daf0b4e395afa2755d34f249585ff5e1ffe787aa4"
SCHEMA = "aster-native-v1"
PUBLIC_CLOCK_KEYS = ("frame", "visual_frame", "intro", "mode", "speed", "wall_ms",
                     "max_raf_gap_ms", "actual_visual_speed", "blur_events", "paused",
                     "status", "held", "remaining", "active_plan", "pending_plan")


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate key: {key}")
            result[key] = value
        return result
    def bad(value):
        raise ValueError(f"Nonfinite JSON value: {value}")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=bad)


def sign(payload, key: bytes):
    if len(key) < 32:
        raise ValueError("Signing key must be at least 32 bytes")
    return {"payload": payload, "signature": hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()}


def validate_plan(plan):
    if not isinstance(plan, list) or len(plan) > 16:
        raise ValueError("Expected at most 16 native action chunks")
    for chunk in plan:
        if (not isinstance(chunk, list) or len(chunk) != 5 or
                any(type(v) is not int for v in chunk) or
                not 0 <= chunk[0] <= 8 or any(v not in (0, 1) for v in chunk[1:4]) or
                not 1 <= chunk[4] <= 600):
            raise ValueError("Invalid native action chunk")
    return plan


def validate_seed(value):
    if type(value) is not int or not 0 <= value <= 2**32 - 1:
        raise ValueError("Seed must be a uint32")
    return value


def validate_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        raise ValueError("Invalid identifier")
    return value


def validate_controller_manifest(manifest):
    if not isinstance(manifest, dict) or not 1 <= len(manifest) <= 2000 or "solve.sh" not in manifest:
        raise ValueError("Controller manifest requires solve.sh and at most 2000 files")
    forbidden = {"__pycache__", ".git", ".ds_store", ".codex", "auth.json", "credentials", "signing.key", "sessions"}
    for name, digest in manifest.items():
        if (not isinstance(name, str) or not name or len(name) > 512 or "\\" in name or any(ord(c) < 32 for c in name) or
                PurePosixPath(name).is_absolute() or any(part in ("", ".", "..") for part in name.split("/")) or
                any(part.lower() in forbidden for part in name.split("/"))):
            raise ValueError("Unsafe controller manifest path")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid controller SHA256")
    if len(canonical_json(manifest)) > 512*1024:
        raise ValueError("Controller manifest exceeds 512KiB")
    return dict(sorted(manifest.items()))


def public_clock(clock):
    return {k: clock[k] for k in PUBLIC_CLOCK_KEYS if k in clock}


def contract_from_config(config):
    gap = config.get("max_raf_gap_ms", 500)
    if type(gap) not in (int, float) or not math.isfinite(gap) or not 0 < gap <= 500:
        raise ValueError("Registered max_raf_gap_ms must be >0 and <=500")
    return {"game":"aster", "engine_sha256":ENGINE_SHA256,
            "fps":60,"mode":"realtime","speed":1,"success_status":"won",
            "max_raf_gap_ms":gap,"allow_external_pause":False,"allow_blur":False,
            "max_engine_ticks":3600,"canonical_artifact":"/app/output/session.json"}
