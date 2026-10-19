# Visual inspection and timed rejection — v3 public contract

This is a simulated inspection station using real photographs of electronic boards. It evaluates visual defect decisions plus timed reject commands, not a physical robot's joint control. Canonical artifact: `/app/output/session.json`.

## Task and shared information

Compare each current query board to its independent known-normal reference. Reject visibly defective boards; let normal boards continue. Normal boards may differ in pose, printed markings, component appearance and reflections. Neither the reference nor an online filename reveals the query's class. No pretrained defect classifier, defect mask, target boolean or future image list is supplied.

The live world lasts **180 seconds** and contains **24 distinct query images: 12 normal and 12 defective**. One carrier enters approximately every six seconds, alternating across four lanes. An entry sensor provides the anonymous carrier ID, lane, SKU alias and entry timestamp. Each lane has a public fixed speed: d=45, f=50, j=55, k=48 mm/s. Distance from entry sensor to rejection gate is 1000mm. Therefore the predicted gate crossing is `entry_ms + 1000 * distance_to_gate_mm / lane_speed_mm_s[lane]`. This encoder information is identical for both experimental conditions.

Each key `d/f/j/k` triggers one immediate reject actuator in its corresponding lane. Its effective window is ±350ms around that carrier's crossing. The actuator requires 800ms between physical commands. Empty operations consume cooldown; a repeated physical command within cooldown is a collision. The server does not schedule future actions, classify a board, select a target, or repair a wrong timestamp.

A pass requires **at least 11/12 defective boards rejected**, **at most 1/12 normal boards wrongly rejected**, **at most two empty operations**, **zero collisions**, and a complete authenticated live session. Failed, late and duplicate actions remain visible in evidence. Both conditions may use shell, image processing, OCR, computer vision, background scripts and calls to the same available model, subject to the experiment's shared budget.

## Camera views and observations

`GET /observe` returns:

```
{
  "schema_version": "visual-inspection-v3.1",
  "phase": "ready|running|finished", "mode": "live|dev",
  "session_id": "opaque-session-id", "clock_ms": 1234.5, "clock_rate": 1,
  "horizon_ms": 180000,
  "calibration": {"...": "the public contract.json"},
  "episode_counts": {"defect": 12, "normal": 12},
  "frame_url": "/frame.png",
  "carriers": [
    {"carrier_id":"opaque-id", "lane":"d", "sku":"board_a", "entry_ms":0,
     "inspection_url":"/inspection/opaque-id.png"}
  ],
  "receipts": []
}
```

`carriers` includes only already-entered carriers still in their gate opportunity; it never lists future carriers. The 1920×1200 frame shows four inspection cards and a lane diagram. Each card contains the whole query photograph and an independent whole normal-reference photograph. There is no ground-truth crop, defect box, mask or class-dependent border. The card also prints its public sensor metadata.

- `GET /frame.png` returns the complete camera/dashboard PNG.
- `GET /observe?inline=1` returns the same PNG as `image_base64` captured at the observation's `clock_ms`.
- `GET /inspection/<carrier_id>.png` returns a high-resolution query/reference comparison for an already-entered carrier. It remains available after that carrier passes for diagnosis. Future or unknown IDs return 404.
- PNG headers include `X-Clock-Ms`, `X-Session-Id`, and, for inspection images, `X-Carrier-Id`.

Images are re-encoded to PNG without original filenames, directory paths or EXIF. SKU aliases identify which normal reference applies; they reveal no quality label. Carrier identities are independent opaque strings. The photograph dataset names, original paths and annotations are private until the completed evaluation export.

## Session and atomic-action API

`OPS_URL` is the live world; `DEV_OPS_URL` is a separate development world with **disjoint query photographs**. The normal references may be shared, but no reference image is used as a query.

- `POST /begin` with `{}` starts the live wall clock. Repeating it does not reset or change the scene.
- `POST /act` accepts `{"key":"d","id":"unique-command-id","session_id":"current-session","expires_ms":12345}`. `key` and `id` are required; other two fields are optional guards. IDs must be 1–128 characters. An exact repeated ID returns the original receipt without another action. Reusing the same ID with changed content is rejected. No `at_ms` or server-side scheduling field is accepted.
- The action receipt contains `id`, `key`, `clock_ms`, `session_id`, and `result`: `target_grab` (defective board correctly rejected), `wrong_grab` (normal board wrongly rejected), `empty`, `collision`, `expired`, `wrong_session`, or `not_running`. Outcome feedback is retrospective.
- `GET /export` or `POST /export` with `{}` returns the signed completed session. Save the entire envelope unchanged as `/app/output/session.json`. Before completion this endpoint returns HTTP 409.
- Development only: `POST /reset` with `{"seed":11,"speed":1}` resets and immediately starts a new practice episode. Live reset returns HTTP 403. Practice speed can be 1–100, but quality decisions intended for a live transfer should be tested at realistic timing.

Development may have fewer unique query images and a shorter duration; `episode_counts` and `horizon_ms` give its actual size. Images are not repeated to inflate the development set. No developer export can satisfy live evaluation.

## Verification boundary

The export is exactly `{"payload":{...},"signature":"HMAC-SHA256 hex"}`. Only the world and private verifier have the signing key. After signature, task identity, seed and frozen-contract checks, an independent reducer verifies every private sampled image against the frozen asset catalog, reobtains its official class, and recomputes actuator timing, cooldown, duplicate removal, correct rejection, mistaken rejection and missed defects. Submitted summaries are not trusted.
