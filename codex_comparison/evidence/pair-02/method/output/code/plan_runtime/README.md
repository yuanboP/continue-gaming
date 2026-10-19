# Sooool literal timed-key runtime

This runtime hosts the unchanged [Sooool timed-key executor](https://github.com/sooool-project/sooool/blob/458a14500fb4f4b52fcb5a31e9dbca76126350ab/environments/rhythm/dist/model-executor.js) in Node.js 18 or later. No npm installation is required.

It supplies a persistent queue between model turns. The model chooses every key, absolute execution time, expiry and ID. No detector, OCR, hidden state, target timetable or corrective timing rule is included. The baseline can implement its own queue with the same public atomic API.

## Start, submit, inspect, finish

The public world URL is in `OPS_URL`. The queue binds only to localhost. Begin the official session, then launch the queue:

```bash
python /app/ops.py begin
node /app/plan_runtime/runner.js serve --url "$OPS_URL" --port 8766 --log /app/output/plan-events.jsonl > /app/output/queue-start.json 2> /app/output/queue-stderr.log &
```

Write a transaction JSON file with your own observed action times, then submit it:

```bash
node /app/plan_runtime/runner.js submit --port 8766 --file /app/output/transaction.json
node /app/plan_runtime/runner.js snapshot --port 8766
```

The local endpoints are `POST /submit`, `GET /snapshot`, and `POST /stop`. A policy program may call them directly rather than spawning the CLI.

After the complete episode:

```bash
python /app/ops.py export --output /app/output/session.json
node /app/plan_runtime/runner.js stop --port 8766
```

The canonical evaluated artifact remains `/app/output/session.json`. The queue's JSONL log is supplementary evidence and cannot substitute for the signed server export. Logs are created exclusively; choose a new log filename for every queue run.

For a developer session, call `python /app/ops.py dev --seed 11 --speed 1`, then use `--url "$DEV_OPS_URL"` and a separate queue port and log. Each reset changes the session ID, so restart the queue after resetting. Public `clock_rate` is honored for accelerated developer sessions. No sampled latency is injected into actual-time execution.

## Unchanged upstream semantics

```json
{"version":1,"cancel_ids":[],"taps":[{"id":"chosen-by-model","key":"d","at_ms":12345,"expires_ms":12450}],"replace_pending":false}
```

Those values illustrate the schema and are not a task solution. The executor admits up to 12 taps per transaction. IDs use 1–64 letters, digits, underscores or hyphens. Versions must be nonnegative JavaScript safe integers.

- Timed keys execute at or after their literal `at_ms`, and never after local `expires_ms`.
- The server receives an immediate atomic key with the same ID and expiry. Network delay can still make a command arrive too late; the server independently checks expiry.
- Repeating an identical transaction is idempotent. An ID cannot be reused for a changed key or time, and an executed ID cannot fire again.
- A newer transaction can cancel pending IDs. Set `replace_pending` only when intentionally replacing the whole pending plan; exact copies retain their queued actions.
- An old destructive transaction is rejected. Old purely additive transactions are permitted when they do not conflict with existing IDs.
- Local `executed` means the upstream keyboard event fired. A successful physical action requires a successful server receipt; both are logged independently.

The host synchronizes the public session clock and never edits model-authored timestamps. It forwards `keydown` events by pairing them with the upstream execution receipts generated in the same synchronous operation. `keyup` has no additional physical effect. Session changes stop the old queue.

## Provenance and checks

The upstream revision is `458a14500fb4f4b52fcb5a31e9dbca76126350ab`. The unchanged executor SHA-256 is:

```text
3b0c38c81ae163fc1ff56dc26943484e8121a47f90ce4d3c7f2be221547e1b28
```

`provenance.json` also records the unchanged optional `DelayScheduler` module. That utility resamples paired action-ready and terminal delays for development; it does not supply decisions and is not part of the live timing path. Its original `load_profile` function is specific to the upstream workload, so only reuse `DelayScheduler` with an explicitly validated new profile.

Run pure protocol fixtures with:

```bash
node --test /app/plan_runtime/tests/*.test.js
```

They check time preservation, expiration, ID/version behavior, cancellation, key-to-ID correspondence and forwarding to a fake atomic HTTP sink. They execute no task episode. Actual task experiments must be launched through Harbor.
