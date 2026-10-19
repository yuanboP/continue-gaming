#!/usr/bin/env python3
"""Generic camera → concurrent native vision → literal timed action loop.

All selection and action-time choices are model outputs. The host only acquires
public pixels, queues independent requests and forwards validated literal taps.
No dataset, labels, masks, reference classifier or defect threshold is imported.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from native_client import SubscriptionClient
from timing import CaptureProtection, PublicClock, gate_ms, read_policy

SYSTEM = (
    "You are a visual inspection controller. Read the supplied public task rules, "
    "sensor metadata and camera pixels. Make the quality decision and every action "
    "time yourself. Return only the requested JSON. Do not use tools or inspect files. "
    "Describe only a short visible observation in visible_evidence; do not include reasoning steps."
)


def request(base, endpoint, payload=None, timeout=10):
    url = base.rstrip('/') + '/' + endpoint.lstrip('/')
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    req = Request(url, data=data, headers={} if data is None else {'Content-Type': 'application/json'})
    with urlopen(req, timeout=timeout) as response:
        return response.read(), dict(response.headers.items())


def api(base, endpoint, payload=None):
    return json.loads(request(base, endpoint, payload)[0])


def validate_decision(value, carrier, calibration=None):
    if not isinstance(value, dict) or set(value) != {'carrier_id', 'decision', 'visible_evidence', 'tap'}:
        raise ValueError('Decision must match the exact public schema')
    if value['carrier_id'] != carrier['carrier_id']:
        raise ValueError('Decision refers to a different captured carrier')
    if value['decision'] not in ('accept', 'reject', 'uncertain') or not isinstance(value['visible_evidence'], str):
        raise ValueError('Invalid quality decision')
    tap = value['tap']
    if value['decision'] != 'reject':
        if tap is not None:
            raise ValueError('Only a reject decision may carry an action')
        return None
    if not isinstance(tap, dict) or set(tap) != {'id', 'key', 'at_ms', 'expires_ms'}:
        raise ValueError('Reject requires one explicit finite timed tap')
    if tap['id'] != 'pick-' + carrier['carrier_id'] or tap['key'] != carrier['lane']:
        raise ValueError('Tap identity/key differs from the public carrier binding')
    for key in ('at_ms', 'expires_ms'):
        if isinstance(tap[key], bool) or not isinstance(tap[key], (int, float)) or not math.isfinite(tap[key]):
            raise ValueError('Action timestamps must be finite')
    if tap['at_ms'] < 0 or tap['expires_ms'] < tap['at_ms']:
        raise ValueError('Invalid time order')
    if calibration is not None:
        gate, width = gate_ms(carrier, calibration), calibration['grab_window_ms']
        if not gate-width <= tap['at_ms'] <= gate+width:
            raise ValueError('Literal dispatch is outside the public physical opportunity')
        # Numerical precision tolerance only: retain the literal value unchanged.
        # The world still applies its exact physical window, including its edge.
        if abs(tap['expires_ms']-(gate+width)) > 1:
            raise ValueError('Model expiry must use the full public window upper bound (1ms numeric tolerance)')
    return tap


def prompt_for(carrier, observation, frame_clock, rules, timing_policy=None, current_clock_ms=None):
    timing = None if timing_policy is None else {key: timing_policy[key] for key in
        ['recommended_lead_ms','delay_low_ms','delay_high_ms','margin_ms','profile_samples']}
    return f'''Inspect the attached camera image for this one carrier. It contains a query product and an independent normal reference. The normal reference is an example, not an exact pixel template.

PUBLIC QUALITY RULES:
{rules}

PUBLIC SENSOR DATA:
{json.dumps({'carrier': carrier, 'capture_clock_ms': frame_clock, 'calibration': observation['calibration']}, ensure_ascii=False, sort_keys=True)}

DEVELOPMENT TRANSPORT MEASUREMENT (finite samples; not guaranteed bounds):
{json.dumps({'timing': timing, 'request_clock_ms': current_clock_ms}, sort_keys=True)}

The conveyor keeps moving during your response. Decide accept, reject, or uncertain from the pixels. Do not infer quality from an anonymous ID, SKU, image orientation, or assumed class balance. If reject, compute G = entry_ms + 1000 * distance_to_gate_mm / lane_speed_mm_s[lane] and W = grab_window_ms from the public data. Return one literal tap with id "pick-{carrier['carrier_id']}" and key "{carrier['lane']}". Use the full physical opportunity for expiry: expires_ms = G + W (ordinary numerical rounding within 1ms is accepted; the physical window itself is exact). Do not shorten expiry to at_ms plus an arbitrary allowance. Transport takes time after the local queue fires. When a measured lead is supplied, the recommended dispatch is at_ms = G - recommended_lead_ms. Check that your chosen dispatch itself lies inside [G-W, G+W] and that dispatch plus the measured delay range remains inside that physical window. You must compute and write the actual numerical timestamps yourself; the host will never correct them. If your decision cannot still be safely delivered, return uncertain with tap null rather than a knowingly stale action. If accept or uncertain, tap must be null. Do not use tools. Return only the required JSON with one short visible observation, not a step-by-step explanation.'''


class Journal:
    def __init__(self, path):
        self.file = Path(path).open('x')
        self.lock = threading.Lock()

    def write(self, event, **detail):
        with self.lock:
            self.file.write(json.dumps({'event': event, 'unix': time.time(), **detail}, allow_nan=False) + '\n')
            self.file.flush()

    def close(self):
        self.file.close()


def run(args):
    if not 1 <= args.workers <= 4 or not 1 <= args.max_calls <= 100:
        raise ValueError('workers must be 1..4 and max-calls 1..100')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    journal = Journal(output / 'actor-calls.jsonl')
    schema = json.loads((HERE / 'decision.schema.json').read_text())
    rules = args.rules.read_text()
    client_pool = queue.Queue()
    clients = []
    process = None
    queue_url = f'http://127.0.0.1:{args.queue_port}'
    transaction_lock = threading.Lock()
    transaction_version = 0
    public_clock = PublicClock()
    timing_policy = None
    protection = None
    try:
        # Initialize native transports while the official world is still ready.
        # Initialization is not a model call and reads no credential content here.
        for index in range(args.workers):
            client = SubscriptionClient(output=output / f'transport-{index}',
                                        output_schema=schema, system=SYSTEM,
                                        model=args.model, effort=args.effort)
            client.timeout = args.call_timeout
            clients.append(client)
            client_pool.put(client)
        observation = api(args.url, '/observe')
        public_clock.update(observation)
        if not args.diagnose:
            if args.timing_policy is None:
                raise ValueError('Execution requires --timing-policy from a DEV probe and simulation')
            timing_policy, policy_hash = read_policy(args.timing_policy, observation['calibration'],
                                                    observation.get('clock_rate', 1))
            protection = CaptureProtection(timing_policy)
            journal.write('timing_policy_loaded', path=str(args.timing_policy), sha256=policy_hash,
                          policy=timing_policy, no_action_retiming=True)
        if args.begin and not args.diagnose and observation['phase'] == 'ready':
            observation = api(args.url, '/begin', {})
            public_clock.update(observation)
        if not args.diagnose and observation['phase'] != 'running':
            raise ValueError('Live/practice execution requires a running episode; use --begin for ready')
        if args.diagnose and observation['phase'] == 'ready' and not observation.get('carriers'):
            raise ValueError('No diagnostic imagery in ready world; begin/reset DEV before diagnosis')
        session_id = observation['session_id']
        journal.write('loop_start', session_id=session_id, model=args.model, effort=args.effort,
                      workers=args.workers, max_calls=args.max_calls, diagnose=args.diagnose,
                      input_information='Public pixels, reference images and visible carrier sensor metadata only')
        if not args.diagnose:
            process = subprocess.Popen(
                ['node', str(HERE.parent / 'plan_runtime/runner.js'), 'serve', '--url', args.url,
                 '--port', str(args.queue_port), '--log', str(output / 'plan-events.jsonl')],
                stdout=(output / 'queue.stdout.log').open('x'),
                stderr=(output / 'queue.stderr.log').open('x'))
            deadline = time.monotonic() + 15
            while True:
                try:
                    api(queue_url, '/snapshot')
                    break
                except Exception:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Timed executor did not start; inspect queue stderr')
                    time.sleep(.05)

        def infer(job):
            nonlocal transaction_version
            carrier, observation, frame_clock, png, call_id, directory = job
            client = client_pool.get()
            accepted = False

            def release_opportunity(reason):
                if protection is not None:
                    previous = protection.release_provisional('pick-'+carrier['carrier_id'])
                    journal.write('action_protection_released', call_id=call_id,
                                  carrier_id=carrier['carrier_id'], reason=reason,
                                  previous=previous, clock_ms=public_clock.now())

            def on_answer(answer, ready):
                nonlocal transaction_version, accepted
                value = json.loads(answer)
                try:
                    tap = validate_decision(value, carrier, observation['calibration'])
                except ValueError as error:
                    accepted = True
                    release_opportunity('invalid_decision')
                    journal.write('invalid_decision', call_id=call_id, carrier_id=carrier['carrier_id'],
                                  decision=value, error=str(error), **ready)
                    return  # Continue native turn to retain complete usage; no action.
                (directory / 'decision.json').write_text(json.dumps(value, indent=2) + '\n')
                journal.write('decision_ready', call_id=call_id, carrier_id=carrier['carrier_id'],
                              decision=value, **ready)
                accepted = True
                if tap is None:
                    release_opportunity(value['decision'])
                if tap is not None and not args.diagnose:
                    protection.add(tap)
                    journal.write('action_protection_added', call_id=call_id, literal_tap=tap,
                                  current_clock_ms=public_clock.now())
                    with transaction_lock:
                        transaction_version += 1
                        transaction = {'version': transaction_version, 'cancel_ids': [], 'taps': [tap]}
                        try:
                            receipt = api(queue_url, '/submit', transaction)
                            journal.write('transaction_submitted', call_id=call_id,
                                          transaction=transaction, receipt=receipt)
                        except Exception as error:
                            journal.write('transaction_transport_error', call_id=call_id,
                                          transaction=transaction, error=str(error))

            try:
                if getattr(client, 'broken', False):
                    client = SubscriptionClient(output=output / ('replacement-' + call_id),
                                                output_schema=schema, system=SYSTEM,
                                                model=args.model, effort=args.effort)
                    client.timeout = args.call_timeout
                    clients.append(client)
                journal.write('call_started', call_id=call_id, carrier_id=carrier['carrier_id'],
                              capture_clock_ms=frame_clock, model=args.model, effort=args.effort,
                              call_directory=str(directory.relative_to(output)))
                answer, meta = client.act(prompt_for(carrier, observation, frame_clock, rules,
                                                    timing_policy, public_clock.now()),
                                          [png], directory=directory, on_answer=on_answer)
                if not accepted:
                    on_answer(answer, {'thread_id': meta['thread_id'], 'turn_id': meta['turn_id'],
                                       'ready_unix': meta['completed_unix'],
                                       'ready_seconds': meta['wall_seconds']})
                journal.write('call_completed', call_id=call_id, carrier_id=carrier['carrier_id'],
                              status='completed', **meta)
            except Exception as error:
                if not accepted:
                    release_opportunity('call_failed')
                failure = directory / 'error.json'
                detail = json.loads(failure.read_text()) if failure.is_file() else {}
                journal.write('call_failed', call_id=call_id, carrier_id=carrier['carrier_id'],
                              error_type=type(error).__name__, error=str(error), native_detail=detail,
                              usage_complete=False)
            finally:
                client_pool.put(client)

        seen = set()
        protected_carriers = set()
        pending = []
        active = set()
        call_count = 0
        guard_active = False
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            while True:
                observation = api(args.url, '/observe')
                if timing_policy is not None and observation.get('clock_rate', 1) != timing_policy['clock_rate']:
                    raise ValueError('Public clock rate changed; refusing to reuse timing policy')
                public_clock.update(observation)
                if observation['session_id'] != session_id:
                    raise ValueError('Public session changed; refusing to reuse captured decisions')
                if observation['phase'] == 'finished':
                    journal.write('world_finished', pending_without_model_calls=len(pending),
                                  in_flight=len(active), clock_ms=observation['clock_ms'])
                    break
                if protection is not None:
                    try:
                        protection.reconcile(api(queue_url, '/snapshot'))
                    except Exception as error:
                        journal.write('protection_snapshot_error', error=str(error))
                for carrier in sorted(observation.get('carriers', []),
                                      key=lambda value: gate_ms(value, observation['calibration'])):
                    ident = carrier['carrier_id']
                    if ident in seen or len(seen) >= args.max_calls:
                        continue
                    if protection is not None and ident not in protected_carriers:
                        protection.reserve(carrier, observation['calibration'])
                        protected_carriers.add(ident)
                        journal.write('action_opportunity_reserved', carrier_id=ident,
                                      gate_ms=gate_ms(carrier, observation['calibration']),
                                      window_ms=observation['calibration']['grab_window_ms'],
                                      clock_ms=public_clock.now(), no_action_submitted=True)
                    blockers = [] if protection is None else protection.blockers(public_clock.now())
                    if blockers:
                        if not guard_active:
                            journal.write('capture_guard_enter', pending_actions=blockers,
                                          delayed_carrier_id=ident, clock_ms=public_clock.now())
                            guard_active = True
                        break  # Global shared resource; another lane is not exempt.
                    if guard_active:
                        journal.write('capture_guard_exit', clock_ms=public_clock.now())
                        guard_active = False
                    seen.add(ident)
                    try:
                        capture_started = time.monotonic()
                        capture_clock_start = public_clock.now()
                        journal.write('capture_request_started', carrier_id=ident,
                                      endpoint=carrier['inspection_url'], clock_ms=capture_clock_start)
                        png, headers = request(args.url, carrier['inspection_url'])
                        if not png.startswith(b'\x89PNG\r\n\x1a\n'):
                            raise ValueError('Inspection endpoint did not return PNG')
                        h = {key.lower(): value for key, value in headers.items()}
                        frame_clock = float(h.get('x-clock-ms', observation['clock_ms']))
                        if h.get('x-session-id', session_id) != session_id:
                            raise ValueError('Capture session changed')
                        call_id = f'call-{len(seen):04d}'
                        directory = output / 'calls' / call_id
                        directory.mkdir(parents=True, exist_ok=False)
                        (directory / 'observation.json').write_text(json.dumps(observation, indent=2) + '\n')
                        journal.write('capture', call_id=call_id, carrier_id=ident,
                                      capture_clock_ms=frame_clock, image_sha256=hashlib.sha256(png).hexdigest(),
                                      request_clock_ms=capture_clock_start, response_clock_ms=public_clock.now(),
                                      rtt_ms=1000*(time.monotonic()-capture_started))
                        overlap = [] if protection is None else protection.blockers(public_clock.now())
                        if overlap:
                            journal.write('capture_completed_inside_protection', carrier_id=ident,
                                          pending_actions=overlap, clock_ms=public_clock.now(),
                                          note='An already in-flight render cannot be cancelled by this client')
                        pending.append((carrier, observation, frame_clock, png, call_id, directory))
                    except Exception as error:
                        if protection is not None:
                            previous = protection.release_provisional('pick-'+ident)
                            journal.write('action_protection_released', carrier_id=ident,
                                          reason='capture_failed', previous=previous, clock_ms=public_clock.now())
                        journal.write('capture_failed', carrier_id=ident, error=str(error))
                active = {future for future in active if not future.done()}
                while pending and len(active) < args.workers and call_count < args.max_calls:
                    # Entry order is public; no class information affects dispatch order.
                    pending.sort(key=lambda job: gate_ms(job[0], job[1]['calibration']))
                    job = pending.pop(0)
                    active.add(executor.submit(infer, job))
                    call_count += 1
                if args.diagnose and call_count >= args.max_calls and not active:
                    break
                if args.diagnose and observation['phase'] == 'ready' and seen and not pending and not active:
                    break
                time.sleep(args.poll_ms / 1000)
            # Existing requests finish and retain their usage even if the world ended.
            # No unstarted request is counted as a model invocation.
        if not args.diagnose:
            exported, _ = request(args.url, '/export')
            json.loads(exported)
            args.artifact.parent.mkdir(parents=True, exist_ok=True)
            args.artifact.write_bytes(exported)
            journal.write('export', artifact=str(args.artifact), sha256=hashlib.sha256(exported).hexdigest())
            (output / 'queue-final.json').write_text(json.dumps(api(queue_url, '/snapshot'), indent=2) + '\n')
            api(queue_url, '/stop', {})
            process.wait(timeout=10)
        journal.write('loop_completed', calls_started=call_count, carriers_seen=len(seen))
        print(json.dumps({'completed': True, 'diagnose': args.diagnose,
                          'calls_started': call_count, 'output': str(output)}))
    finally:
        for client in clients:
            client.close()
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('OPS_URL'), required=not bool(os.environ.get('OPS_URL')))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rules', type=Path, default=HERE.parent / 'QUALITY_RULES.md')
    parser.add_argument('--artifact', type=Path, default=Path('/app/output/session.json'))
    parser.add_argument('--model', default='gpt-6-astra')
    parser.add_argument('--effort', default='medium', choices=['low', 'medium', 'high', 'xhigh'])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--max-calls', type=int, default=24)
    parser.add_argument('--call-timeout', type=float, default=60)
    parser.add_argument('--poll-ms', type=float, default=150)
    parser.add_argument('--queue-port', type=int, default=8766)
    parser.add_argument('--begin', action='store_true')
    parser.add_argument('--timing-policy', type=Path, help='DEV profile/simulation policy; required except in diagnose mode')
    parser.add_argument('--diagnose', action='store_true', help='Read available public pictures; call models but take no actions or begin/reset')
    args = parser.parse_args()
    if not 20 <= args.poll_ms <= 5000 or not 1 <= args.call_timeout <= 180:
        parser.error('poll-ms must be 20..5000 and call-timeout 1..180')
    run(args)


if __name__ == '__main__':
    main()
