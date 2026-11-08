"""Bounded synthetic capture health check in a new, explicitly supplied scratch directory.

This never opens a production camera journal or replays an existing request.
Evidence confirms the host synthetic path, not sensor or board health.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid

from camera.camerad.native_client import NativeClient
from camera.dng.inspect import inspect_dng
from system.linux.recovery import validate_bundle


def check(command, scratch, *, timeout=8, bundle=None, attempt_id=None):
    scratch = Path(scratch).resolve()
    if scratch.exists():
        raise ValueError('health scratch must be a new directory, separate from persistent camera data')
    if not 0 < timeout <= 60:
        raise ValueError('health timeout must be positive and at most 60 seconds')
    reference = validate_bundle(bundle) if bundle is not None else None
    if (reference is None) != (attempt_id is None):
        raise ValueError('bundle and attempt ID must be supplied together')
    if reference and (Path(reference['path']) in scratch.parents or scratch == Path(reference['path'])):
        raise ValueError('health scratch must be separate from the immutable bundle')
    if reference and (not isinstance(command, (list, tuple)) or len(command) != 1 or
                      Path(command[0]).resolve() != Path(reference['path']) / 'bin/hf-camerad'):
        raise ValueError('trial health must execute the selected bundle native daemon')
    if any((parent / 'journal.sqlite3').exists() for parent in scratch.parents):
        raise ValueError('health scratch must be separate from an existing camera journal')
    client = NativeClient(scratch, command, timeout_seconds=min(timeout, 5))
    rid = str(uuid.uuid4())
    try:
        lease = client.acquire('offline-simulator-health')
        request = {'request_id': rid, 'sensor_mode': 'sim-small', 'requested_exposure_us': 10000,
                   'requested_analogue_gain': 1.0, 'output_format': 'dng', 'simulation_scene': 'scene',
                   'token': lease['token'], 'control_epoch': lease['epoch'],
                   'deadline_unix_ms': int(time.time() * 1000 + timeout * 1000)}
        client.capture(request)  # Once only. Transport failure is never a resend.
        until = time.monotonic() + timeout
        record = None
        while time.monotonic() < until:
            record = client.query_capture(rid)
            if record and record['status'] not in ('accepted', 'capturing') and client.get_state()['camera_state'] == 'idle':
                break
            time.sleep(.01)
        if not record or record['status'] != 'complete':
            raise RuntimeError('health capture was not confirmed complete: ' + str(record and record['status']))
        artifact = Path(record['file_path']).resolve()
        if artifact.parent != scratch / 'photos' or artifact.name != rid + '.dng':
            raise RuntimeError('health artifact escaped scratch storage')
        inspection = inspect_dng(artifact)
        if inspection['width'] != 128 or inspection['height'] != 96 or inspection['request_id'] != rid:
            raise RuntimeError('health artifact dimensions are invalid')
        state = client.get_state()
        if state.get('authority') != 'native' or not state.get('journal_healthy'):
            raise RuntimeError('native journal is unhealthy')
        client.release(lease['token'], lease['epoch'])
        client.close()
        client = NativeClient(scratch, command, timeout_seconds=min(timeout, 5))
        persisted = client.query_capture(rid)
        if persisted != record:
            raise RuntimeError('completed health request changed after restart')
        return {'format': 'hf-sim-health-v1', 'passed': True, 'simulated': True, 'authority': 'native',
                'journal_healthy': True, 'capture_status': 'complete', 'request_id': rid,
                'artifact_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                'manifest_sha256': reference['manifest_sha256'] if reference else None,
                'native_binary_sha256': hashlib.sha256(Path(command[0]).read_bytes()).hexdigest(),
                'attempt_id': attempt_id, 'scope': 'host synthetic capture and restart persistence'}
    finally:
        if not client.closed:
            try:
                client.close()
            except Exception:
                client.abort()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-daemon', type=Path, required=True)
    parser.add_argument('--scratch-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=8)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--attempt-id')
    args = parser.parse_args()
    print(json.dumps(check([str(args.native_daemon.resolve())], args.scratch_dir,
                           timeout=args.timeout, bundle=args.bundle, attempt_id=args.attempt_id), indent=2))


if __name__ == '__main__':
    main()
