"""Authenticate a private-world record and independently replay the native game."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
from pathlib import Path
import subprocess
import tempfile

from continuum_core.world.common import (ENGINE_SHA256, SCHEMA, canonical_json,
                                      strict_json, validate_controller_manifest)

MAX_SESSION_BYTES = 32 * 1024 * 1024


def authenticate(envelope, key, expected):
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ValueError("Invalid envelope")
    payload, signature = envelope["payload"], envelope["signature"]
    if not isinstance(payload, dict) or not isinstance(signature, str) or len(key) < 32:
        raise ValueError("Invalid signature types/key")
    calculated = hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, calculated):
        raise ValueError("World signature mismatch")
    for field in ("task_id", "seed", "contract"):
        if payload.get(field) != expected[field]:
            raise ValueError(f"Expected {field} mismatch")
    if payload.get("schema_version") != SCHEMA or payload.get("mode") != "live" or payload.get("run_kind") != "live":
        raise ValueError("Wrong schema or non-live record")
    if payload.get("source_sha256", {}).get("engine.mjs") != ENGINE_SHA256:
        raise ValueError("Signed engine source mismatch")
    source_expected = expected.get("source_sha256")
    if source_expected is not None and payload.get("source_sha256") != source_expected:
        raise ValueError("Registered source manifest mismatch")
    if expected.get("require_controller_commitment", False):
        commitment = payload.get("controller_commitment")
        if not isinstance(commitment, dict) or commitment.get("schema_version") != "native-controller-commitment-v1":
            raise ValueError("Missing controller precommitment")
        manifest = validate_controller_manifest(commitment.get("manifest"))
        if hashlib.sha256(canonical_json(manifest)).hexdigest() != commitment.get("manifest_sha256"):
            raise ValueError("Controller precommit hash mismatch")
        before, begun = commitment.get("received_monotonic"), payload.get("started_monotonic")
        if not number(before) or not number(begun) or before >= begun:
            raise ValueError("Controller commitment was not before episode start")
        if expected.get("expected_controller_manifest") is not None and manifest != expected["expected_controller_manifest"]:
            raise ValueError("Controller differs from registered holdout initial source")
    if expected.get("expected_method_bundle_sha256") is not None:
        binding = payload.get("method_bundle_binding")
        if not isinstance(binding, dict) or binding.get("canonical_sha256") != expected["expected_method_bundle_sha256"]:
            raise ValueError("Method bundle differs from registered canonical identity")
    return payload


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_clock(payload):
    """Pure structural/timing checks, with native cinematics explicitly allowed."""
    errors = []
    audit = payload.get("audit", {})
    contract = payload["contract"]
    initial, final = audit.get("initial_clock"), audit.get("final_clock")
    ticks, raf = audit.get("ticks"), audit.get("raf")
    if not audit.get("sealed") or not isinstance(initial, dict) or not isinstance(final, dict):
        return {"passed":False,"errors":["missing_sealed_clocks"]}
    if not isinstance(ticks, list) or not 1 <= len(ticks) <= contract["max_engine_ticks"]:
        return {"passed":False,"errors":["invalid_tick_count"]}
    if not isinstance(raf, list) or not raf:
        return {"passed":False,"errors":["missing_raf_trace"]}
    if audit.get("anomalies"):
        errors.append("world_recorded_anomalies")
    start, end = audit.get("started_at_ms"), audit.get("ended_at_ms")
    if not number(start) or not number(end) or end <= start:
        return {"passed":False,"errors":["invalid_perf_interval"]}
    if initial.get("frame") != 0 or initial.get("visual_frame") != 0:
        errors.append("nonzero_initial_frame")
    if final.get("frame") != len(ticks) or audit.get("final", {}).get("tick") != len(ticks):
        errors.append("final_frame_count")
    threshold = contract["max_raf_gap_ms"]
    if not number(threshold) or not 0 < threshold <= 500:
        errors.append("invalid_registered_gap")
        threshold = 500
    max_gap = 0.0
    previous_entered, previous_frame, previous_visual, previous_timestamp = start, 0, 0, None
    for index, row in enumerate(raf):
        if not isinstance(row, dict):
            errors.append("malformed_raf"); continue
        entered, completed = row.get("entered_ms"), row.get("completed_ms")
        timestamp, gap = row.get("timestamp_ms"), row.get("gap_ms")
        if row.get("index") != index or not all(number(x) for x in (entered, completed, timestamp, gap)):
            errors.append("invalid_raf_identity_or_time"); continue
        if entered < previous_entered or completed < entered or completed > end + 5:
            errors.append("nonmonotonic_raf")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            errors.append("nonmonotonic_raf_timestamp")
        if abs(gap-(entered-previous_entered)) > .001:
            errors.append("raf_gap_recompute_mismatch")
        max_gap = max(max_gap, gap)
        if gap > threshold:
            errors.append("raf_gap_exceeded")
        frame, visual = row.get("frame"), row.get("visual_frame")
        if type(frame) is not int or type(visual) is not int or frame < previous_frame or visual < previous_visual or frame > visual:
            errors.append("nonmonotonic_frame")
        else:
            previous_frame, previous_visual = frame, visual
        previous_entered, previous_timestamp = entered, timestamp
    for row in [initial, *raf, final]:
        if row.get("mode") != "realtime" or row.get("speed") != 1:
            errors.append("non_native_realtime_speed")
        if row.get("paused") is not False or row.get("blur_events") != 0:
            errors.append("external_pause_or_blur")
        gap = row.get("max_raf_gap_ms")
        if not number(gap) or gap > threshold:
            errors.append("native_raf_gap_exceeded")
    previous_tick_time = start
    for index, tick in enumerate(ticks):
        at = tick.get("at_ms")
        if tick.get("index") != index or tick.get("tick_before") != index or not number(at) or at < previous_tick_time or at > end:
            errors.append("invalid_tick_sequence")
        if number(at):
            previous_tick_time = at
    # 60Hz accumulator advances visual frames, while cinematics can withhold
    # logical Game.step calls. Allow one RAF and reset/draw overhead of <= gap.
    visual = final.get("visual_frame")
    wall = final.get("wall_ms")
    if not number(wall) or wall <= 0 or type(visual) is not int:
        errors.append("invalid_final_wall")
    elif abs(visual * 1000 / 60 - wall) > threshold + 1000 / 60:
        errors.append("visual_wall_clock_disagreement")
    if final.get("status") != audit.get("final", {}).get("status"):
        errors.append("final_status_mismatch")
    return {"passed":not errors,"errors":sorted(set(errors)),"max_observed_raf_gap_ms":max_gap,
            "raf_count":len(raf),"tick_count":len(ticks),"visual_frames":visual,"wall_ms":wall}


def grade(artifact, key_path, expected_path, engine_path):
    if artifact.stat().st_size > MAX_SESSION_BYTES:
        raise ValueError("Session exceeds 32MiB")
    data = artifact.read_bytes()
    expected = strict_json(expected_path.read_bytes())
    payload = authenticate(strict_json(data), key_path.read_bytes(), expected)
    if hashlib.sha256(engine_path.read_bytes()).hexdigest() != ENGINE_SHA256:
        raise ValueError("Private replay engine source mismatch")
    clock = validate_clock(payload)
    with tempfile.TemporaryDirectory(prefix="aster-replay-") as directory:
        source = Path(directory) / "payload.json"
        source.write_bytes(canonical_json(payload))
        completed = subprocess.run(["node", str(Path(__file__).with_name("replay.mjs")), str(source), str(engine_path)],
                                   capture_output=True, text=True, timeout=30, check=True)
    replay = strict_json(completed.stdout)
    passed = bool(clock["passed"] and replay["agreement"] and replay["status"] == "won")
    return {"schema_version":1,"integrity_passed":bool(clock["passed"] and replay["agreement"]),
            "passed":passed,"task_id":expected["task_id"],
            "artifact_sha256":hashlib.sha256(data).hexdigest(),
            "signed_payload_sha256":hashlib.sha256(canonical_json(payload)).hexdigest(),
            "scoring":{"passed":passed,"criteria":{"signature":True,"clock_integrity":clock["passed"],
                "replay_agreement":replay["agreement"],"won":replay["status"]=="won"},
                "clock":clock,"replay":replay}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=Path("/app/output/session.json"))
    parser.add_argument("--key", type=Path, default=Path("/tests/signing.key"))
    parser.add_argument("--expected", type=Path, default=Path("/tests/expected.json"))
    parser.add_argument("--engine", type=Path, default=Path("/opt/continuum/environments/aster/dist/engine.mjs"))
    parser.add_argument("--output", type=Path, default=Path("/logs/verifier"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        result = grade(args.artifact, args.key, args.expected, args.engine)
    except Exception as exc:
        result = {"schema_version":1,"passed":False,"integrity_passed":False,
                  "error_type":type(exc).__name__,"error":str(exc)}
    (args.output / "result.json").write_bytes(canonical_json(result)+b"\n")
    (args.output / "reward.json").write_bytes(canonical_json({"reward":float(result["passed"])})+b"\n")
    if args.artifact.is_file() and args.artifact.stat().st_size <= MAX_SESSION_BYTES:
        (args.output / "session.json").write_bytes(args.artifact.read_bytes())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
