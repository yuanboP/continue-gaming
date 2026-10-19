"""Post-hoc real race control-delay calibration, NOT a fastest serial API profile.

Only timing/cohort metadata is used. Sample entire vectors to retain slot launch
skew and correlated delays; never consult actual Future completion or expose a
sampled delay to the player prompt.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


def nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be finite and nonnegative')
    return float(value)


def percentile(values, q):
    values = sorted(values)
    if not values: raise ValueError('Empty timing population')
    index = (len(values)-1)*q
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi]-values[lo])*(index-lo)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def calibrate(episodes: list[Path], k: int = 4, apply_overhead_s: float = .02) -> dict:
    if type(k) is not int or k < 1: raise ValueError('K must be positive integer')
    overhead = nonnegative(apply_overhead_s, 'apply_overhead_s')
    vectors, image_counts, provenance, control_ages, frame_ages = [], [], [], [], []
    for episode in episodes:
        episode = Path(episode)
        summary = json.loads((episode/'summary.json').read_text())
        config = json.loads((episode/'config.json').read_text())
        if summary.get('complete') is not True or config.get('mode') != 'realtime' or config.get('provider') != 'api':
            raise ValueError('Calibration requires completed real API episodes')
        if config.get('decision_fanout') != k or config.get('speed') != 1:
            raise ValueError('Calibration requires consistent K and original speed')
        actions = read_rows(episode/'actions.jsonl')
        issued = read_rows(episode/'issued.jsonl')
        decisions = read_rows(episode/'decisions.jsonl')
        if not actions: raise ValueError('Missing applied actions')
        offsets = [nonnegative(a['applied_wall_s'], 'applied_wall_s') -
                   nonnegative(a['apply_clock']['wall_ms'], 'apply clock')/1000 for a in actions]
        offset = statistics.median(offsets)
        if not math.isfinite(offset): raise ValueError('Invalid clock offset')
        by_seq = {}
        for row in issued:
            seq = row['seq']
            if seq in by_seq: raise ValueError('Duplicate issued sequence')
            if row.get('decision_fanout') != k: raise ValueError('Candidate K mismatch')
            by_seq[seq] = row
        completed = {}
        seen_decisions = set()
        for row in decisions:
            seq = row['seq']
            if seq not in by_seq or seq in seen_decisions: raise ValueError('Unmatched/duplicate completed sequence')
            seen_decisions.add(seq)
            source = by_seq[seq]
            for key in ('cohort_id', 'fanout_index', 'visual_frame'):
                if row.get(key) != source.get(key): raise ValueError('Completed/issued metadata mismatch')
            # Terminal cleanup records answers whose Futures finished AFTER
            # the live coordinator stopped, without a processed readiness time.
            if 'completed_wall_s' in row:
                completed[seq] = row
            elif row.get('disposition') is not None:
                raise ValueError('Processed candidate missing readiness timestamp')
        groups = defaultdict(list)
        for row in issued: groups[row['cohort_id']].append(row)
        full_groups = []
        incomplete = []
        observation_times = defaultdict(list)
        for row in issued:
            observed = nonnegative(row['capture_clock']['wall_ms'], 'capture clock')/1000 + offset
            observation_times[row['visual_frame']].append(observed)
        for cohort, members in sorted(groups.items()):
            indices = [m['fanout_index'] for m in members]
            if any(type(i) is not int or not 0 <= i < k for i in indices) or len(set(indices)) != len(indices):
                raise ValueError('Duplicate/out-of-range cohort slot')
            if len(members) != k or any(m['seq'] not in completed for m in members):
                incomplete.append(cohort)
                continue
            vector = []
            counts = set()
            for member in sorted(members, key=lambda m: m['fanout_index']):
                row = completed[member['seq']]
                observed = nonnegative(member['capture_clock']['wall_ms'], 'capture clock')/1000 + offset
                readiness = nonnegative(row['completed_wall_s'], 'completed wall') - observed
                vector.append(nonnegative(readiness, 'cohort readiness'))
                count = len(member['image_frames'])
                if count < 1: raise ValueError('Missing screenshot count')
                counts.add(count)
            if len(counts) != 1: raise ValueError('Within-cohort image count mismatch')
            vectors.append(vector)
            image_counts.append(counts.pop())
            full_groups.append(cohort)
        if not full_groups: raise ValueError('No complete joint cohorts')
        if any(c < max(full_groups) for c in incomplete):
            raise ValueError('Incomplete nonterminal cohort; refusing biased sample')
        for action in actions:
            observations = observation_times[action['observed_frame']]
            if not observations: raise ValueError('Action lacks source observation')
            # Early logs contain no winning slot id. All candidates in an
            # observation cohort must share a capture time to identify true age.
            if max(observations)-min(observations) > 1e-6:
                raise ValueError('Ambiguous action observation capture time')
            age = nonnegative(action['applied_wall_s'], 'applied_wall_s') - observations[0]
            control_ages.append(nonnegative(age, 'applied control age'))
            frame_ages.append(nonnegative(action['age_frames'], 'age_frames')/60)
        hashes = {name: hashlib.sha256((episode/name).read_bytes()).hexdigest()
                  for name in ('summary.json','config.json','actions.jsonl','issued.jsonl','decisions.jsonl')}
        provenance.append({'episode': str(episode), 'clock_offset_s': offset,
            'issued_candidates': len(issued), 'processed_candidates': len(completed),
            'terminal_drained_candidates': len(decisions)-len(completed),
            'complete_cohorts': len(full_groups), 'excluded_terminal_cohorts': incomplete,
            'applied_count': len(actions), 'source_sha256': hashes})
    return {'kind': 'posthoc_real_race_control_readiness', 'version': 1, 'k': k,
            'not_serial_fastest_api_profile': True,
            'delay_definition': 'completed_wall_s - (capture_clock.wall_ms/1000 + episode_median_clock_offset)',
            'apply_overhead_s': overhead, 'joint_delay_vectors_s': vectors,
            'image_counts': image_counts, 'provenance': provenance,
            'control_age_p50_s': percentile(control_ages,.5), 'control_age_p95_s': percentile(control_ages,.95),
            'control_age_frames_as_s_p50': percentile(frame_ages,.5),
            'control_age_frames_as_s_p95': percentile(frame_ages,.95),
            'applied_count': len(control_ages)}


def sample_cohort_delay_frames(profile: dict, rng: random.Random, speed: float = 1,
                               image_count: int | None = None,
                               apply_overhead_s: float | None = None) -> list[int]:
    """Sample one whole cohort in slot order; strict stratum matching, no fallback."""
    if image_count is not None and (type(image_count) is not int or image_count < 1):
        raise ValueError('image_count must be a positive integer')
    speed = nonnegative(speed, 'speed')
    if speed == 0: raise ValueError('speed must be positive')
    k = profile['k']
    if type(k) is not int or k < 1: raise ValueError('Invalid profile K')
    vectors = profile['joint_delay_vectors_s']
    counts = profile['image_counts']
    if len(vectors) != len(counts) or not vectors: raise ValueError('Invalid cohort population')
    candidates = []
    for vector, count in zip(vectors, counts):
        if not isinstance(vector,list) or len(vector) != k: raise ValueError('Inconsistent vector K')
        values = [nonnegative(v,'delay vector') for v in vector]
        if type(count) is not int or count < 1: raise ValueError('Invalid image count')
        if image_count is None or count == image_count: candidates.append(values)
    if not candidates: raise ValueError('No matching image-count stratum')
    overhead = nonnegative(profile['apply_overhead_s'] if apply_overhead_s is None else apply_overhead_s,
                           'apply_overhead_s')
    chosen = rng.choice(candidates)
    return [math.ceil((seconds + overhead)*60*speed) for seconds in chosen]


def sample_cohort_ready_terminal_frames(profile: dict, rng: random.Random, speed: float = 1,
                                       image_count: int | None = None) -> tuple[list[int], list[int]]:
    """One joint row supplies both readiness and terminal slots; no fallback.

    Application overhead affects readiness, while slot release must never
    precede application. Both clocks are rounded upward to visual frames.
    """
    speed=nonnegative(speed,'speed')
    if speed==0: raise ValueError('speed must be positive')
    k=profile['k']
    if type(k) is not int or k<1: raise ValueError('Invalid profile K')
    if image_count is not None and (type(image_count) is not int or image_count<1):
        raise ValueError('image_count must be positive integer')
    ready=profile['joint_delay_vectors_s'];terminal=profile['joint_terminal_delay_vectors_s'];counts=profile['image_counts']
    if not ready or len(ready)!=len(terminal) or len(ready)!=len(counts): raise ValueError('Invalid paired population')
    overhead=nonnegative(profile['apply_overhead_s'],'apply overhead')
    candidates=[]
    for r,t,c in zip(ready,terminal,counts):
        if not isinstance(r,list) or not isinstance(t,list) or len(r)!=k or len(t)!=k: raise ValueError('Invalid paired vector K')
        r=[nonnegative(v,'readiness') for v in r]; t=[nonnegative(v,'terminal') for v in t]
        if any(end<start for start,end in zip(r,t)): raise ValueError('Terminal precedes readiness')
        if type(c) is not int or c<1: raise ValueError('Invalid image count')
        if image_count is None or c==image_count: candidates.append((r,t))
    if not candidates: raise ValueError('No matching image-count stratum')
    r,t=rng.choice(candidates)
    rf=[math.ceil((v+overhead)*60*speed) for v in r]
    tf=[max(first,math.ceil(v*60*speed)) for first,v in zip(rf,t)]
    return rf,tf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('episodes', nargs='+', type=Path)
    parser.add_argument('--k',type=int,default=4)
    parser.add_argument('--apply-overhead-s',type=float,default=.02)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=calibrate(args.episodes,args.k,args.apply_overhead_s)
    encoded=json.dumps(result,indent=2,allow_nan=False)+'\n'
    if args.output: args.output.write_text(encoded)
    else: print(encoded,end='')


if __name__ == '__main__': main()
