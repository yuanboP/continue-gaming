# TraceLine — public interface

Canonical output: **`/app/output/session.json`**. The scene is an independent real-time service. The visual console and public client do not own its clock or grading rules.

## Task contract

Live lasts **180,000 ms** with **24 distinct query images**, comprising 12 normal and 12 defective boards. One carrier enters approximately every six seconds, alternating across four lanes. Sensors reveal only already-entered carriers and their anonymous IDs, lane, SKU alias, and entry time. Normal references are independent physical samples, never query images.

The sensor-to-gate distance is **1,000 mm**. Lane speeds are **d: 45, f: 50, j: 55, k: 48 mm/s**. A carrier reaches the gate at:

```
entry_ms + 1000 * distance_to_gate_mm / lane_speed_mm_s[lane]
```

Each lane key triggers one immediate physical command. The valid window is **±350 ms**. Its actuator cooldown is **800 ms**; an empty operation consumes cooldown, and another physical command during cooldown is a collision. A pass requires at least **11/12** defects rejected, at most **1/12** normal boards rejected, at most **two** empty operations, **zero** collisions, and a complete authenticated live recording.

## Observation and images

`GET /observe` returns `schema_version`, `phase` (`ready`, `running`, `finished`), `mode` (`live`, `dev`), opaque `session_id`, `clock_ms`, `clock_rate`, `horizon_ms`, public `calibration`, `episode_counts`, `frame_url`, `carriers`, and past action `receipts`.

Each carrier contains:

```json
{"carrier_id":"opaque-id","lane":"d","sku":"board_a","entry_ms":0,"inspection_url":"/inspection/opaque-id.png"}
```

Only entered carriers still in their gate opportunity appear in `carriers`. No future list is exposed.

- `GET /frame.png`: complete 1920×1200 dashboard with four inspection cards and a lane diagram.
- `GET /observe?inline=1`: the same observation with its timestamp-matched PNG as `image_base64`.
- `GET /inspection/<carrier_id>.png`: high-resolution whole query/reference comparison for an entered carrier; remains available after passing for diagnosis. Unknown or future IDs return 404.
- PNG headers: `X-Clock-Ms`, `X-Session-Id`, and `X-Carrier-Id` for an individual comparison.

Images are re-encoded without original paths, filenames, or EXIF. No class-dependent border, target label, defect box, or ground-truth crop appears. SKU aliases identify the relevant normal reference only.

## Actions and recording

`OPS_URL` is live; `DEV_OPS_URL` is a separate development service with disjoint query photographs. Normal references may be shared.

- `POST /begin` with `{}` starts the continuous clock. Repeating it does not reset the scene.
- `POST /act` accepts `{"key":"d","id":"unique-command-id","session_id":"current-session","expires_ms":12345}`. `key` and `id` are required; the remaining fields are optional guards. IDs are 1–128 characters. An identical repeated ID returns the same receipt without another action. A changed request reusing an ID is rejected. No scheduling or `at_ms` field is accepted.
- Receipts contain `id`, `key`, actual `clock_ms`, `session_id`, and `result`: `target_grab`, `wrong_grab`, `empty`, `collision`, `expired`, `wrong_session`, or `not_running`. Feedback is retrospective.
- `GET /export` or `POST /export` with `{}` returns the complete signed session after completion; before completion it returns HTTP 409. Save the exact bytes as `/app/output/session.json`.
- Development only: `POST /reset` with `{"seed":11,"speed":1}` resets and immediately starts practice. Speed may be 1–100. Live reset returns HTTP 403. Practice may have fewer unique query images and a shorter duration; use its `episode_counts` and `horizon_ms`. A developer recording cannot pass live verification.

Both experimental conditions may use the full shell, image processing, computer vision, OCR, background scripts, and the same available model within their shared budget.

## Verification

The export envelope has `payload` and an HMAC-SHA256 `signature`. Only the private world and verifier know the key. The verifier checks authentication, task identity, seed, frozen contract, and sampled images against the private catalog; it independently recomputes timing, cooldown, duplicate removal, correct/incorrect rejections, and missed defects. Submitted summaries are not trusted.

## Optional visual console

The console serves a `/api/` prefix that forwards the public observation, full image, individual comparison, begin, immediate action, and export endpoints to the same world. It does not expose reset or inline observation; use the public client for those. UI export downloads `session.json`; in the benchmark, ensure the authoritative artifact is saved to `/app/output/session.json`.
