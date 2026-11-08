"""Exercise a packaged simulator as a nonroot Linux process, without installing it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from camera.camerad.native_client import NativeClient
from system.linux.healthcheck import check
from system.linux.recovery import RecoveryStore
from tools.linux_package import package


def smoke(root, build_dir):
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('the deployment smoke test requires a nonroot Linux process')
    root, build_dir = Path(root).resolve(), Path(build_dir).resolve()
    with tempfile.TemporaryDirectory(prefix='hf-sim-linux-deployment-') as temporary:
        directory = Path(temporary)
        first, second = directory / 'release-a', directory / 'release-b'
        first_ref = package(root, build_dir, first, package_id='linux-smoke-a')
        second_ref = package(root, build_dir, second, package_id='linux-smoke-b')
        unit_checker = shutil.which('systemd-analyze')
        unit_verification = {'available': bool(unit_checker), 'executed': False}
        if unit_checker:
            verified = subprocess.run([unit_checker, '--user', 'verify', str(first / 'systemd/hf-sim.service')],
                                      check=True, capture_output=True, text=True, timeout=10)
            unit_verification.update(executed=True, returncode=verified.returncode, stderr=verified.stderr)
        data = directory / 'persistent-camera'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        server = subprocess.Popen([sys.executable, '-B', '-m', 'system.linux.serve', '--runtime-root', str(first / 'runtime'),
                                   '--native-daemon', str(first / 'bin/hf-camerad'), '--data-dir', str(data),
                                   '--port', str(port)], cwd=first / 'runtime',
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        endpoint = 'http://127.0.0.1:' + str(port)

        def request(path, body=None):
            call = urllib.request.Request(endpoint + path, data=None if body is None else json.dumps(body).encode(),
                                          headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(call, timeout=3) as response:
                return json.load(response)

        try:
            until = time.monotonic() + 10
            while True:
                if server.poll() is not None:
                    raise RuntimeError('packaged server exited: ' + server.communicate(timeout=1)[1][-4000:])
                try:
                    state = request('/api/v1/state')
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() >= until:
                        raise RuntimeError('packaged server did not become available')
                    time.sleep(.05)
            if state.get('authority') != 'native' or state.get('journal_healthy') is not True:
                raise RuntimeError('packaged server did not select a healthy native authority')
            lease = request('/api/v1/control/acquire', {'owner': 'linux-deployment-smoke'})
            rid = str(uuid.uuid4())
            body = {'request_id': rid, 'sensor_mode': 'sim-small', 'requested_exposure_us': 10000,
                    'requested_analogue_gain': 1.0, 'output_format': 'dng', 'simulation_scene': 'scene',
                    'token': lease['token'], 'control_epoch': lease['epoch'],
                    'deadline_unix_ms': int(time.time() * 1000) + 5000}
            request('/api/v1/captures', body)
            until = time.monotonic() + 5
            while time.monotonic() < until:
                record = request('/api/v1/captures/' + rid)
                if record['status'] not in ('accepted', 'capturing'):
                    break
                time.sleep(.01)
            if record['status'] != 'complete' or Path(record['file_path']).parent != data / 'photos':
                raise RuntimeError('packaged HTTP capture did not commit to separate persistent storage')
            request('/api/v1/control/release', {'token': lease['token'], 'epoch': lease['epoch']})
        finally:
            if server.poll() is None:
                server.send_signal(signal.SIGINT)
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait(timeout=5)
            for stream in (server.stdout, server.stderr):
                stream.close()
        if server.returncode != 0:
            raise RuntimeError('packaged server did not shut down cleanly')
        before_diagnostics = {path.relative_to(data).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in data.rglob('*') if path.is_file()}
        inspected = subprocess.run([sys.executable, '-B', '-m', 'tools.diagnostics', '--data-dir', str(data), '--offline-only'],
                                   cwd=first / 'runtime', check=True, capture_output=True, text=True, timeout=10)
        diagnostics = json.loads(inspected.stdout)
        after_diagnostics = {path.relative_to(data).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in data.rglob('*') if path.is_file()}
        if diagnostics.get('ok') is not True or before_diagnostics != after_diagnostics:
            raise RuntimeError('packaged offline diagnostics failed or changed camera evidence')
        with NativeClient(data, [str(first / 'bin/hf-camerad')]) as resumed:
            if resumed.query_capture(rid) != record:
                raise RuntimeError('packaged capture changed across service shutdown/restart')
        recovery = directory / 'offline-recovery'
        with RecoveryStore(recovery, architecture=platform.machine()) as store:
            store.bootstrap(first); store.stage(second, attempts=1)
            trial = store.choose_boot()
            health = check([str(second / 'bin/hf-camerad')], directory / 'trial-health', timeout=8,
                           bundle=second, attempt_id=trial['attempt_id'])
            store.confirm(trial['attempt_id'], health)
            if store.choose_boot()['package_id'] != 'linux-smoke-b':
                raise RuntimeError('real health evidence did not confirm the simulated trial')
            store.stage(first, attempts=1)
            store.choose_boot()  # Model an unconfirmed attempt, then reopen state.
        with RecoveryStore(recovery, architecture=platform.machine()) as store:
            fallback = store.choose_boot()
            if fallback['package_id'] != 'linux-smoke-b' or fallback['mode'] != 'confirmed':
                raise RuntimeError('trial budget was not preserved across state restart')
        return {'format': 'hf-sim-linux-deployment-evidence-v1', 'passed': True, 'simulated': True,
                'platform': platform.platform(), 'uid': os.geteuid(), 'first_bundle': first_ref,
                'second_bundle': second_ref, 'foreground_http_capture': 'complete',
                'capture_id': rid, 'service_shutdown_restart': 'persisted complete',
                'packaged_offline_diagnostics': {'ok': True, 'camera_bytes_unchanged': True},
                'trial_health': health, 'offline_trial_confirmed': True, 'offline_budget_fallback': True,
                'systemd_unit_verify': unit_verification,
                'systemd_service_enabled': False, 'scope': 'temporary nonroot host simulator, no board boot/update'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=Path('build/linux-native'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    evidence = smoke(Path(__file__).resolve().parents[1], args.build_dir)
    destination = args.output or args.build_dir / 'deployment-evidence.json'
    destination.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
