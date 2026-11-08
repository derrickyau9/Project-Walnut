"""Probe Windows/WSL ownership boundaries using new disposable directories only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile

from camera.camerad.native_client import NativeClient
from camera.camerad.service import CameraError


def wsl_path(path):
    path = PureWindowsPath(Path(path).resolve())
    return '/mnt/' + path.drive[0].lower() + '/' + '/'.join(path.parts[1:])


def probe(windows_daemon, linux_daemon, distribution='Debian'):
    if os.name != 'nt':
        raise RuntimeError('run this optional Windows/WSL boundary probe from Windows')
    get_state = json.dumps({'id': 1, 'method': 'get_state', 'params': {}}) + '\n'
    shutdown = json.dumps({'id': 2, 'method': 'shutdown', 'params': {}}) + '\n'

    def command(directory):
        return ['wsl.exe', '-d', distribution, '--exec', wsl_path(linux_daemon), '--data-dir', wsl_path(directory)]

    outcomes = {}
    with tempfile.TemporaryDirectory(prefix='hf-cross-os-lock-win-') as directory:
        with NativeClient(directory, [str(Path(windows_daemon).resolve())]):
            result = subprocess.run(command(directory), input=get_state + shutdown, text=True,
                                    capture_output=True, timeout=15)
            lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            outcomes['windows_owner_linux_contender'] = {'returncode': result.returncode, 'responses': lines,
                                                         'stderr': result.stderr[-2000:]}
    with tempfile.TemporaryDirectory(prefix='hf-cross-os-lock-linux-') as directory:
        linux = subprocess.Popen(command(directory), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            linux.stdin.write(get_state); linux.stdin.flush()
            with ThreadPoolExecutor(max_workers=1) as reader:
                line = reader.submit(linux.stdout.readline).result(timeout=15)
            owner = json.loads(line)
            if owner.get('id') != 1 or 'result' not in owner:
                outcomes['linux_owner_windows_contender'] = {'linux_startup': owner, 'tested': False}
            else:
                windows = None
                try:
                    windows = NativeClient(directory, [str(Path(windows_daemon).resolve())])
                    outcome = {'simultaneous_ownership': True, 'windows_state': windows.get_state()}
                except CameraError as error:
                    outcome = {'simultaneous_ownership': False, 'error': error.code, 'message': str(error)}
                finally:
                    if windows is not None:
                        windows.close()
                outcomes['linux_owner_windows_contender'] = {'linux_startup': owner, 'tested': True, **outcome}
        finally:
            if linux.poll() is None:
                try:
                    linux.communicate(shutdown, timeout=10)
                except subprocess.TimeoutExpired:
                    linux.kill(); linux.communicate(timeout=5)
            for stream in (linux.stdin, linux.stdout, linux.stderr):
                stream.close()
    return {'format': 'hf-sim-cross-os-lock-probe-v1', 'simulated': True, 'distribution': distribution,
            'fresh_disposable_storage_only': True, 'outcomes': outcomes,
            'deployment_policy': 'Use a Linux-native filesystem and dedicated nonshared Linux data; never mix OS authorities on DrvFS.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows-daemon', type=Path, required=True)
    parser.add_argument('--linux-daemon', type=Path, required=True)
    parser.add_argument('--distribution', default='Debian')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evidence = probe(args.windows_daemon, args.linux_daemon, args.distribution)
    args.output.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
