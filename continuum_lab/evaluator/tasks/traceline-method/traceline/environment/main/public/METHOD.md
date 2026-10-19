---
name: inspection-timing
description: Use public latency measurements, timing simulation, concurrent native visual decisions, and a finite timed-action queue when an environment keeps moving during model inference.
---

Read `/app/API_CONTRACT.md` and `/app/QUALITY_RULES.md`. Both are shared with standard Codex. You retain full tools and may adapt the supplied workflow within the task budget. Quality decisions and literal action times come from the same model/effort as the outer agent. No image classifier, label map, defect crop, or answer table is provided.

The workflow separates visual inference from timed actuation. Four native workers inspect public images while the timed-key executor holds future literal taps. Before capture, the helper reserves the carrier's public possible-action window; this is resource protection, not a quality decision or queued action. Accept, uncertain, invalid and failed decisions release it; a reject binds the same reservation to the model's original literal tap. Image acquisition pauses near these windows because camera rendering and actuator handling may share resources. An already in-flight capture cannot be cancelled reliably, and a delay beyond the measured budget remains possible. A speed-1 policy is refused in an accelerated developer world.

First measure the separate developer world's control/capture transport. These model-free timing probes deliberately issue immediate DEV actions; they are not quality judgments or a scored practice attempt. The probe refuses the live world. Preserve the raw requests, receipts, PNG responses, timing samples, and failures:

```sh
python3 /app/ops.py dev --seed 11 --speed 1
python3 /app/runtime/actor/timing.py probe --url "$DEV_OPS_URL" --output /app/output/timing-probe --samples 8
node /app/runtime/actor/simulate_timing.mjs --profile /app/output/timing-probe/profile.json --output /app/output/timing-search
```

The simulation actually runs the timed-key executor under a virtual clock for a finite dispatch-lead grid. It selects a recommendation only if recorded delays, zero delay, and declared margin scenarios fit the original physical window. This local schedule simulation does not measure visual accuracy or native-model request latency, and finite samples are not tail guarantees. Read `timing-search/timing-policy.json` and `simulation.json`; an infeasible result is a measured limitation, not permission to widen the task window. Profile artifacts must come from development, never from replaying private live labels.

Reset the developer world after the probes. A single real-time practice is usually enough to inspect model judgments, timing behavior, and usage. Keep the model `gpt-6-astra` and effort `medium` for the matched experiment:

```sh
python3 /app/ops.py dev --seed 11 --speed 1
python3 /app/runtime/actor/actor_loop.py --url "$DEV_OPS_URL" --output /app/output/practice --artifact /app/output/practice-session.json --timing-policy /app/output/timing-search/timing-policy.json --workers 4 --max-calls 24
```

The prompt supplies only public calibration and aggregate DEV timing. The model computes gate crossing G, chooses a dispatch point inside the original `[G-W,G+W]` opportunity, and uses **expires_ms = G+W**, the complete physical upper bound. The measured lead recommends an earlier send point; it is not silently applied by the host. The validator rejects invalid literals rather than repairing them. Inspect visible evidence and errors before changing a semantic prompt; neither schema validity nor correct timing proves that a board was classified correctly.

Use the 900-second total agent allowance deliberately: roughly 90 seconds for reading/probes/simulation, 180–240 seconds for one practice, and a modest development reserve. Aim to start the official 180-second shift with at least 300 seconds remaining for initialization, outstanding calls, and export. All development, nested calls and failures consume this allowance. Additional practice is available, but repeating a full practice costs time needed by the official shift.

Run the official shift with a new output directory. `--begin` initializes native transports before starting the stopped official clock:

```sh
python3 /app/runtime/actor/actor_loop.py --url "$OPS_URL" --output /app/output/live --artifact /app/output/session.json --timing-policy /app/output/timing-search/timing-policy.json --begin --workers 4 --max-calls 24
```

Keep the process alive until it exports the complete signed `/app/output/session.json`. After a successful export, submit the artifact; rereading every live call is not required. Preserve code edits and all call/probe/simulation/queue logs. `actor-calls.jsonl` records the loaded policy, public capture timing, capture guard intervals, model readiness/completion, literal decisions and native usage. `plan-events.jsonl` connects executor receipts to server actions. A failed call or uncertain decision causes no inferred reject; no failed object is silently retried. There is no default hedging in this candidate. Equal wall allowance does not imply equal actual model calls or tokens.
