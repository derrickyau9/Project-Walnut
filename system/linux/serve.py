"""Launch the local native-authority simulator as a regular Linux user."""
import argparse
import os
from pathlib import Path
import sys


def validate_paths(runtime, daemon, data):
    data = Path(data).expanduser()
    if not data.is_absolute():
        raise ValueError('camera data must be an explicit absolute path')
    runtime, daemon, data = Path(runtime).resolve(), Path(daemon).resolve(), data.resolve()
    bundle = runtime.parent
    if data == bundle or bundle in data.parents or data in bundle.parents:
        raise ValueError('persistent camera data must be separate from the immutable application bundle')
    if not (runtime / 'camera/camerad/server.py').is_file() or not daemon.is_file():
        raise ValueError('runtime source or native daemon is missing')
    return runtime, daemon, data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--native-daemon', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() == 0:
        parser.error('run this host simulator as a nonroot Linux user')
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    runtime, daemon, data = validate_paths(args.runtime_root, args.native_daemon, args.data_dir)
    os.umask(0o077)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(runtime)
    os.execv(sys.executable, [sys.executable, '-B', '-m', 'camera.camerad.server', '--authority', 'native',
                            '--native-daemon', str(daemon), '--data-dir', str(data), '--port', str(args.port)])


if __name__ == '__main__':
    main()
