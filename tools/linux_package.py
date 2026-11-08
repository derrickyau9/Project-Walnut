"""Stage an integrity-manifested Linux host simulator bundle, without installing it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from system.linux.recovery import FORMAT, TARGET, validate_bundle
from tools.linux_build import native_input_hashes


def package(root, build_dir, output, *, package_id):
    root, build_dir, output = Path(root).resolve(), Path(build_dir).resolve(), Path(output).resolve()
    if not isinstance(package_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', package_id):
        raise ValueError('invalid package identity')
    evidence = json.loads((build_dir / 'build-evidence.json').read_text(encoding='utf-8'))
    if (evidence.get('format') != 'hf-sim-linux-build-evidence-v1' or evidence.get('native_tests_passed') is not True or
            not evidence.get('platform', '').startswith('Linux-')):
        raise ValueError('a successful actual Linux build-evidence.json is required')
    expected_inputs = evidence.get('native_input_sha256')
    if not isinstance(expected_inputs, dict) or not expected_inputs or expected_inputs != native_input_hashes(root):
        raise ValueError('native build inputs changed or source hashes are missing; rebuild and test before packaging')
    if output.exists():
        raise ValueError('output must be a new directory; an installed bundle is never overwritten')
    artifacts = []
    for name in ('hf-camerad', 'hf-native-worker'):
        path = build_dir / name
        if path.read_bytes()[:4] != b'\x7fELF':
            raise ValueError('Linux ELF executable required: ' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != evidence['artifacts'].get(name):
            raise ValueError('executable differs from tested build evidence: ' + name)
        artifacts.append((path, 'bin/' + name, 0o755))
    for component in ('camera', 'simulation', 'daos'):
        for path in sorted((root / component).rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.json', '.html', '.css', '.js') and '__pycache__' not in path.parts:
                artifacts.append((path, 'runtime/' + path.relative_to(root).as_posix(), 0o644))
    for path in sorted((root / 'system/linux').glob('*.py')):
        artifacts.append((path, 'runtime/system/linux/' + path.name, 0o644))
    for name in ('__init__.py', 'diagnostics.py'):
        artifacts.append((root / 'tools' / name, 'runtime/tools/' + name, 0o644))
    artifacts.extend([
        (root / 'system/linux/hf-sim.service', 'systemd/hf-sim.service', 0o644),
        (root / 'camera/native/third_party/README.md', 'licenses/third-party-notices.md', 0o644),
        (root / 'camera/native/third_party/manifest.json', 'licenses/source-manifest.json', 0o644),
        (root / 'camera/native/third_party/nlohmann/LICENSE.MIT', 'licenses/nlohmann-LICENSE.MIT', 0o644),
        (build_dir / 'build-evidence.json', 'licenses/build-evidence.json', 0o644),
    ])
    output.mkdir(parents=True)
    entries = []
    for source, relative, mode in sorted(artifacts, key=lambda item: item[1]):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(mode)
        data = destination.read_bytes()
        if relative.startswith('bin/') and hashlib.sha256(data).hexdigest() != evidence['artifacts'].get(destination.name):
            raise ValueError('staged executable differs from tested build evidence')
        entries.append({'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': mode})
    manifest = {'format': FORMAT, 'target': TARGET, 'package_id': package_id,
                'architecture': evidence['architecture'], 'files': entries}
    if native_input_hashes(root) != expected_inputs:
        raise ValueError('native build inputs changed while packaging; freeze sources and rebuild')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return validate_bundle(output, architecture=evidence['architecture'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=Path('build/linux-native'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package-id', required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], args.build_dir, args.output,
                             package_id=args.package_id), indent=2))


if __name__ == '__main__':
    main()
