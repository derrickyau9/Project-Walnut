"""One bounded, explicitly authorized local preference FIFO-swap probe."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[2] / 'ui/on-device/shell_model.py'
TIMEOUT = 2.0


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child(scratch):
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('nonroot local Linux probe only')
    spec = importlib.util.spec_from_file_location('daos_fifo_probe_shell', SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    path = scratch / 'shell-preferences.json'
    document = {'schema_version': 1, 'preferences': dict(module.DEFAULT_PREFERENCES)}
    with path.open('xb') as output:
        output.write(json.dumps(document).encode())
    store = module._PreferenceStore(path)
    if store.load() != document['preferences'] or store.unsafe:
        raise RuntimeError('regular preference fixture failed to load')
    print('HF_FIFO_PROBE_LOADED_REGULAR', flush=True)
    opened = store._open
    triggered = False

    def swap_then_open(directory, name, flags, mode=0o600):
        nonlocal triggered
        if name == path.name and not triggered and flags & os.O_ACCMODE == os.O_RDONLY:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise RuntimeError('expected own regular leaf immediately before swap')
            path.unlink()
            os.mkfifo(path, 0o600)
            triggered = True
            print('HF_FIFO_PROBE_SWAPPED_BEFORE_OPEN', flush=True)
        return opened(directory, name, flags, mode)

    store._open = swap_then_open
    try:
        with store._directory() as directory:
            store._check_current(directory)
    except Exception as error:
        print('HF_FIFO_PROBE_REJECTED ' + type(error).__name__ + ': ' + str(error), flush=True)
        return 0
    print('HF_FIFO_PROBE_UNEXPECTEDLY_ACCEPTED', flush=True)
    return 3


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        return child(Path(sys.argv[2]))
    if len(sys.argv) != 1 or sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('nonroot local Linux probe only; no extra arguments')
    for name in ('probe.log', 'result.json'):
        if (HERE / name).exists():
            raise RuntimeError('probe outputs must be new')
    before = digest(SOURCE)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='daos-preference-fifo-probe-') as temporary:
        scratch = Path(temporary).resolve()
        scratch.chmod(0o700)
        process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--child', str(scratch)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        bounded_timeout = False
        try:
            output, _ = process.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            bounded_timeout = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = process.communicate(timeout=2)
        elapsed = time.monotonic() - started
        try:
            os.killpg(process.pid, 0)
            group_gone = False
        except ProcessLookupError:
            group_gone = True
        leaf = scratch / 'shell-preferences.json'
        fifo_seen = leaf.exists() and stat.S_ISFIFO(leaf.lstat().st_mode)
    if len(output) > 65536:
        raise RuntimeError('fixed probe exceeded diagnostic output bound')
    result = {'format': 'DaOS-preference-fifo-probe-v1', 'platform': platform.platform(),
              'uid': os.geteuid(), 'python': platform.python_version(), 'timeout_seconds': TIMEOUT,
              'source': str(SOURCE), 'source_sha256': before, 'source_unchanged': digest(SOURCE) == before,
              'probe_sha256': digest(Path(__file__)), 'child_pid': process.pid,
              'child_returncode': process.returncode, 'child_reaped': process.poll() is not None,
              'exact_process_group_gone': group_gone, 'elapsed_seconds': elapsed,
              'timeout_triggered': bounded_timeout, 'own_fifo_seen': fifo_seen,
              'loaded_regular_marker_seen': b'HF_FIFO_PROBE_LOADED_REGULAR' in output,
              'swap_marker_seen': b'HF_FIFO_PROBE_SWAPPED_BEFORE_OPEN' in output,
              'rejected_marker_seen': b'HF_FIFO_PROBE_REJECTED' in output,
              'observation': ('blocked_then_exact_group_killed_and_reaped' if bounded_timeout else 'child_returned'),
              'temporary_directory_removed': not scratch.exists(),
              'tests_run': False, 'VM_actions': False, 'camera_actions': False}
    with (HERE / 'probe.log').open('xb') as log:
        log.write(output)
        log.write(b'\nHARNESS_RESULT ' + json.dumps(result, sort_keys=True).encode() + b'\n')
    with (HERE / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
