"""Recover a verified continuous decision prefix after interruption.

Only runs with durable request.json manifests are supported. Missing results end
that prefix; later completed decisions are never used to jump over a gap.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path


def durable_write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as output:
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def append_jsonl(path, value):
    with Path(path).open('a') as output:
        output.write(json.dumps(value) + '\n')
        output.flush()
        os.fsync(output.fileno())


def recover_prefix(directory):
    directory = Path(directory)
    rows = []
    for seq, decision in enumerate(sorted(directory.glob('decision_[0-9][0-9][0-9][0-9][0-9]'))):
        if decision.name != f'decision_{seq:05d}':
            break
        request_path = decision / 'request.json'
        if not request_path.exists():
            if (directory / 'issued.jsonl').exists():
                break  # Crash before this request was durably issued.
            raise ValueError(f'{decision.name}: legacy run lacks request.json; cannot reconstruct timing')
        request = json.loads(request_path.read_text())
        required = ('seq', 'visual_frame', 'logical_frame', 'image_frames', 'image_sha256', 'prompt_sha256', 'held', 'submitted_wall_s')
        if any(field not in request for field in required) or request['seq'] != seq:
            raise ValueError(f'{decision.name}: incomplete request manifest')
        if not request['image_sha256'] or len(request['image_sha256']) != len(request['image_frames']):
            raise ValueError(f'{decision.name}: invalid image manifest')
        prompt_hash = hashlib.sha256((decision / 'prompt.txt').read_bytes()).hexdigest()
        if prompt_hash != request['prompt_sha256']:
            raise ValueError(f'{decision.name}: prompt hash mismatch')
        for index, expected in enumerate(request['image_sha256']):
            actual = hashlib.sha256((decision / f'image_{index}.png').read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f'{decision.name}: image {index} hash mismatch')
        result_path = decision / 'result.json'
        if not result_path.exists():
            break
        result = json.loads(result_path.read_text())
        if not isinstance(result, dict) or 'answer' not in result or 'plan' not in result:
            raise ValueError(f'{decision.name}: incomplete result')
        # Keep timing and hashes exclusively from the pre-submission manifest.
        if set(required) & set(result):
            raise ValueError(f'{decision.name}: result overwrites request manifest')
        rows.append({**request, **result})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rows = recover_prefix(args.directory)
    output = args.output or args.directory / 'completed_prefix.recovered.jsonl'
    temporary = output.with_name(output.name + '.tmp')
    with temporary.open('w') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(output)
    sync_directory(output.parent)
    print(json.dumps({'continuous_decisions': len(rows), 'output': str(output)}))


if __name__ == '__main__':
    main()
