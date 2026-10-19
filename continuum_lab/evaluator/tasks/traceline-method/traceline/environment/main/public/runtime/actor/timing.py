#!/usr/bin/env python3
"""DEV-only public transport probes and model-neutral capture protection.

No model calls, labels, private paths, or live task mutation are used here.
The separate simulator selects a measured timing recommendation, not a decision.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
import uuid


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def gate_ms(carrier, calibration):
    return carrier['entry_ms'] + 1000 * calibration['distance_to_gate_mm'] / calibration['lane_speed_mm_s'][carrier['lane']]


def read_policy(path, calibration, clock_rate=1):
    raw = Path(path).read_bytes()
    policy = json.loads(raw)
    if policy.get('schema') != 'inspection-transport-policy-v1' or policy.get('profile_mode') != 'dev':
        raise ValueError('Expected an explicitly DEV-derived timing policy')
    if (policy.get('window_ms') != calibration['grab_window_ms'] or policy.get('clock_rate') != 1
            or not finite(clock_rate) or policy['clock_rate'] != clock_rate):
        raise ValueError('Timing policy does not match the public real-time calibration')
    if policy.get('feasible') is not True:
        raise ValueError('No feasible dispatch recommendation in the measured simulation')
    for name in ['recommended_lead_ms', 'delay_low_ms', 'delay_high_ms', 'capture_budget_ms', 'settle_budget_ms', 'margin_ms']:
        if not finite(policy.get(name)):
            raise ValueError('Nonfinite timing policy field: ' + name)
    if not 0 <= policy['recommended_lead_ms'] <= policy['window_ms'] - policy['margin_ms']:
        raise ValueError('Recommended dispatch leaves no zero-delay margin')
    if policy['delay_low_ms'] > policy['delay_high_ms']:
        raise ValueError('Invalid measured delay interval')
    if min(policy['capture_budget_ms'], policy['settle_budget_ms'], policy['margin_ms']) <= 0:
        raise ValueError('Timing protection budgets must be positive')
    return policy, hashlib.sha256(raw).hexdigest()


class PublicClock:
    """Same conservative response-receipt anchor convention as the queue host."""
    def __init__(self):
        self.lock = threading.Lock()
        self.anchor = self.monotonic = 0.0
        self.rate = 1.0

    def update(self, observation):
        with self.lock:
            self.anchor, self.monotonic = observation['clock_ms'], time.monotonic()
            self.rate = observation.get('clock_rate', 1)

    def now(self):
        with self.lock:
            return self.anchor + 1000 * (time.monotonic() - self.monotonic) * self.rate


class CaptureProtection:
    """Protect public model-authored pending actions across all lanes."""
    def __init__(self, policy):
        self.policy = policy
        self.lock = threading.Lock()
        self.pending = {}

    def add(self, tap):
        with self.lock:
            self.pending[tap['id']] = dict(tap, protection_kind='model_literal')

    def reserve(self, carrier, calibration):
        """Reserve a public opportunity before its picture/request exists.

        This is a resource reservation, never a queued or inferred action.
        A later valid model tap replaces it without changing the tap.
        """
        gate, width = gate_ms(carrier, calibration), calibration['grab_window_ms']
        with self.lock:
            self.pending.setdefault('pick-'+carrier['carrier_id'], {
                'id':'pick-'+carrier['carrier_id'], 'key':carrier['lane'],
                'at_ms':gate-width, 'expires_ms':gate+width,
                'protection_kind':'public_opportunity'})

    def release_provisional(self, ident):
        with self.lock:
            current = self.pending.get(ident)
            if current is not None and current.get('protection_kind') == 'public_opportunity':
                return self.pending.pop(ident)
            return None  # A model-bound tap needs a server receipt or finite expiry.

    def reconcile(self, snapshot):
        # A failed/unknown transport does not establish that the world applied it.
        settled = {row['id'] for row in snapshot.get('server_receipts', [])
                   if isinstance(row.get('body'), dict) and finite(row['body'].get('clock_ms'))}
        with self.lock:
            for ident in settled:
                self.pending.pop(ident, None)

    def blockers(self, now):
        pre, post = self.policy['capture_budget_ms'], self.policy['settle_budget_ms']
        with self.lock:
            expired = [ident for ident, tap in self.pending.items()
                       if now > tap['expires_ms'] + post]
            for ident in expired:
                self.pending.pop(ident, None)
            return [dict(tap) for tap in self.pending.values()
                    if tap['at_ms'] - pre <= now <= tap['expires_ms'] + post]


def probe(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    events = (output / 'probe-events.jsonl').open('x')
    write_lock = threading.Lock()

    def record(event, **fields):
        with write_lock:
            events.write(json.dumps({'event': event, 'unix': time.time(), **fields}, allow_nan=False) + '\n')
            events.flush()

    def request(endpoint, payload=None, image_name=None):
        url = args.url.rstrip('/') + endpoint
        begin = time.monotonic()
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        record('request', endpoint=endpoint, payload=payload, monotonic_start=begin)
        try:
            req = Request(url, data=data, headers={} if data is None else {'Content-Type': 'application/json'})
            with urlopen(req, timeout=args.timeout) as response:
                raw, headers, status = response.read(), dict(response.headers.items()), response.status
            end = time.monotonic()
            if image_name:
                if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Expected public PNG')
                (output / image_name).write_bytes(raw)
                body = {'image': image_name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            else:
                body = json.loads(raw)
            record('response', endpoint=endpoint, payload=payload, status=status, headers=headers,
                   body=body, monotonic_start=begin, monotonic_end=end, rtt_ms=1000*(end-begin))
            return body, headers, 1000*(end-begin), end
        except Exception as error:
            record('request_failed', endpoint=endpoint, payload=payload, error=str(error),
                   monotonic_start=begin, monotonic_end=time.monotonic())
            raise

    profile = {'schema': 'inspection-public-transport-profile-v1', 'mode': None,
               'started_unix': time.time(), 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'model_calls': 0, 'requested_samples': args.samples, 'complete': False,
               'samples': [], 'failures': [],
               'limitations': ['Finite DEV samples are not a tail guarantee.',
                              'Capture RTT is an occupancy upper-bound proxy, not measured lock duration.',
                              'Python timer jitter is a transport probe, not a Node timer benchmark.',
                              'No visual accuracy or native-model occupancy is measured.']}
    try:
        observation, _, _, _ = request('/observe')
        if observation.get('mode') != 'dev' or observation.get('clock_rate', 1) != 1 or observation['phase'] != 'running':
            raise ValueError('Probe requires a separate running DEV world at speed 1; never a live world')
        profile.update(mode='dev', session_id=observation['session_id'], clock_rate=1,
                       calibration=observation['calibration'])
        deadline = time.monotonic() + args.wait_seconds
        while not observation.get('carriers'):
            if time.monotonic() > deadline or observation['phase'] != 'running':
                raise ValueError('No public carrier available for capture timing')
            time.sleep(.1)
            observation, _, _, _ = request('/observe')
        carrier = observation['carriers'][0]
        profile['capture_endpoint'] = carrier['inspection_url']
        with ThreadPoolExecutor(max_workers=1) as pool:
            for index in range(args.samples):
                observation, _, observe_rtt, anchor = request('/observe')
                if observation['session_id'] != profile['session_id'] or observation['phase'] != 'running':
                    raise ValueError('DEV session ended or changed during timing probe')
                target_local = anchor + .05
                target_world = observation['clock_ms'] + 50
                capture_future = None
                if index % 2 == 1:
                    time.sleep(max(0, target_local - .01 - time.monotonic()))
                    capture_future = pool.submit(request, carrier['inspection_url'], None, f'probe-{index:04d}.png')
                time.sleep(max(0, target_local - time.monotonic()))
                sent = time.monotonic()
                sent_world = observation['clock_ms'] + 1000*(sent-anchor)
                keys = observation['calibration']['lane_keys']
                payload = {'key': keys[index % len(keys)],
                           'id': 'timing-' + uuid.uuid4().hex, 'session_id': profile['session_id']}
                try:
                    receipt, _, rtt, _ = request('/act', payload)
                    capture = capture_future.result() if capture_future is not None else None
                    if not finite(receipt.get('clock_ms')) or receipt.get('session_id') != profile['session_id']:
                        raise ValueError('Invalid public timing receipt')
                    if capture is not None:
                        headers = {key.lower(): value for key, value in capture[1].items()}
                        if headers.get('x-session-id') != profile['session_id']:
                            raise ValueError('DEV capture session changed')
                    row = {'sample': index, 'probe_at_ms': target_world, 'sent_ms': sent_world,
                           'server_apply_ms': receipt['clock_ms'], 'scheduler_jitter_ms': 1000*(sent-target_local),
                           'apply_delay_ms': receipt['clock_ms']-sent_world, 'control_rtt_ms': rtt,
                           'observe_rtt_ms': observe_rtt, 'capture_rtt_ms': capture[2] if capture else None,
                           'capture_overlap_requested': capture_future is not None,
                           'receipt_id': receipt['id']}
                    profile['samples'].append(row)
                    record('probe_sample', **row)
                except Exception as error:
                    profile['failures'].append({'sample': index, 'error': str(error)})
                    record('probe_sample_failed', sample=index, error=str(error))
                    if capture_future is not None:
                        try:
                            capture_future.result()
                        except Exception:
                            pass
                time.sleep(max(0, args.period_seconds-(time.monotonic()-anchor)))
        profile['complete'] = not profile['failures'] and len(profile['samples']) == args.samples
    finally:
        profile['finished_unix'] = time.time()
        (output / 'profile.json').write_text(json.dumps(profile, indent=2, allow_nan=False) + '\n')
        events.close()
    if profile['failures'] or len(profile['samples']) != args.samples:
        raise ValueError('Incomplete probe retained; do not silently fit successful samples only')
    print(json.dumps({'profile': str(output/'profile.json'), 'samples': len(profile['samples']), 'model_calls': 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('probe')
    p.add_argument('--url', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--samples', type=int, default=8)
    p.add_argument('--period-seconds', type=float, default=1)
    p.add_argument('--wait-seconds', type=float, default=15)
    p.add_argument('--timeout', type=float, default=5)
    args = parser.parse_args()
    if not 4 <= args.samples <= 32 or not 0.8 <= args.period_seconds <= 10 or not 1 <= args.timeout <= 10:
        parser.error('samples=4..32, period-seconds=.8..10, timeout=1..10')
    probe(args)


if __name__ == '__main__':
    main()
