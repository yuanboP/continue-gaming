# ASTER public interface

## Public HTTP

- `GET /health`: `{ready,schema_version,role:'dev'|'live'}`; does not start an episode. Common exporters can use the role to skip DEV video finalization while more DEV work is possible.
- `POST /begin {}`: once-only live start, or one DEV reset. Returns public clock. Live uses native `realtime`, speed 1, private configured seed.
- `POST /freeze_controller {manifest:{relative_path:sha256,...}}`: common live controller precommit, before begin. Requires root `solve.sh`, safe relative paths and lowercase SHA256 values. The first manifest is immutable; identical resubmission is idempotent. Returns the received UTC/monotonic/public clock and manifest hash. The world signs this commitment into the terminal session; it does not assert that main's writable filesystem becomes read-only. Registered holdouts require exact expected manifest agreement.
- `POST /reset {seed?,mode?,speed?}`: DEV only, bounded reset quota; modes `sim` and `realtime`, speed 1. This is an episode and must be run inside Harbor.
- `POST /advance {frames:1..600}`: DEV sim only, using the original native stepping implementation; both arms may implement their own simulation timing. Denied in live or realtime mode. Simulated outcomes are development evidence, never a live score.
- `GET /observe`: atomic original 960×720 capture, `{image_base64,image_sha256,image_id,frame,held,clock,status}`. All clock fields describe time or the agent's own plan; no player/bullet coordinates or future chart.
- `POST /act {plan:[[direction,Z,Shift,X,logical_frames],...],id?}`: literal native plan, ≤16 chunks; directions 0–8, binary keys, duration 1–600. Returns receipt with request identity and exact public application clock. No retiming or hidden solver.
- `GET /status`: public lifecycle and clock; no diagnostics or private seed.
- `GET /images/<image_id>.png`: only previously captured image IDs.
- `GET /export`: `{payload,signature}` after terminal state only. Save exactly as `/app/output/session.json`. Running export is denied because it contains evaluator truth.
- `GET /trace`: ZIP after terminal only: canonical session, actual input captures, public gateway event log, and explicitly registered safe method artifacts. No arbitrary path download, key, browser profile, private reasoning, or source tree.
- `GET /video`: after terminal and coordinator completion only; closes the browser context to finalize the original 960×720 Playwright WebM, then downloads it. Both arms record identically. Perform final observation, session export and trace export first. Further captures/resets are unavailable after video finalization; status/export remain available. This is visual evidence, not the source of the score.
- `POST /workflow/run {operation,run_id?,...}`: method-arm-only trusted coordinator hook, starts asynchronous method work; `GET /workflow/status?id=<id>` gives `{run_id,status,result?}`. Operations and budget enforcement are implemented by `continuum_core.method.coordinator`. Baseline requests are rejected.

Health/observation are allowed before begin. The ready screen is not a started episode. No public pause, speed control, debug, diagnostics, live simulation advance, or arbitrary JavaScript endpoint exists. No world routes proxy arbitrary URLs.

