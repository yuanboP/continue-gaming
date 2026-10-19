# Shared native model broker

Both arms may use this helper, including concurrent Python controllers. It
provides no game solver, timing policy, hidden state, or automatic action. The
outer Codex retains its normal tools. Inner visual calls use the original
provided runtime `runtime/subscription.py` with native ChatGPT login, adapted to
`gpt-6-astra` and `low` effort, fresh ephemeral threads, and no inner tools or
API fallback. The original file remains byte-identical: after checking its
SHA-256, the broker replaces exactly five `'gpt-6.1-sol'` literals in memory.
Model-return and low-effort checks remain active. Health, request and result
metadata record original/adapted source SHA-256 and replacement count; this
transport is explicitly adapted, not claimed to execute unchanged source.

The controller must start the broker if `http://localhost:8090/health` is not
ready. Start it from the native task environment so it inherits `CODEX_HOME`;
do not inspect or copy login contents. The explicit entry is:

```sh
python /app/model_broker.py serve > /app/output/model-broker-server.log 2>&1 &
```

Wait for `/health` before sending calls. Include this start-or-reuse logic in
the frozen controller: every fresh holdout has a fresh container and cannot
reuse a development process. Do not start a second broker when one is already
healthy. Submitting a visual call uses a unique request ID and run ID. Calls receive literal
prompt text and one to three supplied PNGs; a JSON output schema is optional.

```python
import base64, json, urllib.request
payload = {
    "request_id": "practice-call-001",
    "run_id": "practice",
    "phase": "dev",  # use live for every official-game call
    "prompt": "Your visual policy and required response format",
    "images_base64": [base64.b64encode(open("/app/output/frame.png", "rb").read()).decode()],
}
request = urllib.request.Request(
    "http://localhost:8090/act", data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=300) as response:
    result = json.load(response)
```

`result.status` is `completed` or `failed`; only a completed result has a usable
`answer`. `meta` retains observed numeric token usage, thread identities, timing,
and explicit unknown usage. A failed call reports a fixed `failure_kind`, without
copying native error bodies. Do not treat absent usage as zero. The broker does
not execute the returned answer: the controller must validate it and call the
public game `/act` itself.

There are at most four native workers. DEV has 160 admitted calls and LIVE has
100; frozen holdouts set the DEV allowance to zero. Failed and unresolved
admissions consume their allowance. A duplicate ID with the same payload can
retrieve its already completed result; it never starts a replacement call.
Conflicting or still unresolved IDs are rejected. Use `/status` for limits and
admitted counts. The benchmark also accounts for any other model calls made by
the outer agent; using a different invocation does not create a free quota.

Public evidence is retained under `/app/output/model-broker/`: admission and
completion journal, exact prompts and PNG inputs, final responses, sanitized
native lifecycle/usage events, and request metadata. Private native reasoning
events remain in temporary directories and are not public artifacts. Existing
login credentials are used in place; this helper does not copy them.

Configuration: `MODEL_BROKER_DEV_LIMIT`, `MODEL_BROKER_LIVE_LIMIT`,
`MODEL_BROKER_WORKERS`, `MODEL_BROKER_OUTPUT`, and
`NATIVE_SUBSCRIPTION_SOURCE`. The installed original transport must match the
SHA-256 recorded in `model_broker.py`; import and `--help` make no model calls.
