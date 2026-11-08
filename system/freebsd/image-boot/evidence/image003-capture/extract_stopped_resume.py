# Resume extraction also guards the stopped ancestor COW and includes export files.
"""Read-only UFS2 extraction from a cleanly stopped DaOS capture image.

Run on Linux as an ordinary user. No mounts, live-image override or VM control.
The qemu-img dd count is an absolute input-block bound, unlike GNU dd count.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def save_json(path, value):
    with Path(path).open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2)
        output.write('\n')


def run(argv, output, *, env=None, binary=False, timeout=40):
    started = time.monotonic()
    result = subprocess.run(argv, capture_output=True, env=env, timeout=timeout)
    record = {'argv': argv, 'returncode': result.returncode,
              'seconds': round(time.monotonic() - started, 3),
              'stderr': result.stderr.decode(errors='replace')}
    if not binary:
        record['stdout'] = result.stdout.decode(errors='replace')
    save_json(output, record)
    if result.returncode:
        raise RuntimeError('Read-only tool failed; do not override locks: ' + record['stderr'])
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--slice-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise ValueError('Ordinary unprivileged host user required')
    if args.output.exists() or args.slice_dir.exists():
        raise ValueError('New output and slice directories required')
    observation = json.loads((args.work / 'observation.json').read_text())
    image = json.loads((args.work / 'image.json').read_text())
    launch = json.loads((args.work / 'launch.json').read_text())
    if (observation.get('completed') is not True or observation.get('process_returncode') != 0
            or observation.get('all_buffers_synced_marker_seen') is not True
            or observation.get('source_image_unchanged') is not True):
        raise ValueError('Completed clean-shutdown observation required')
    if Path('/proc', str(launch['pid'])).exists():
        raise ValueError('Recorded parent PID exists; refuse ambiguous/live inspection')
    parent = args.work / 'guest.qcow2'
    source = Path(image['source_image'])
    parent_before, source_before = digest(parent), digest(source)
    backing_before = {item['path']: (source_before if Path(item['path']) == source else digest(item['path']))
                      for item in image['backing_inputs']}
    if any(backing_before[item['path']] != item['sha256'] for item in image['backing_inputs']):
        raise ValueError('Backing-chain identity changed before inspection')
    if (parent_before != observation['private_state']['overlay']['sha256']
            or source_before != image['source_image_sha256']):
        raise ValueError('Stopped parent or source identity changed')
    args.output.mkdir(parents=True)
    args.slice_dir.mkdir(mode=0o700)
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = str(args.tools / 'root/usr/lib/x86_64-linux-gnu')
    binaries = args.tools / 'root/usr/bin'
    slices, extracted = [], []
    for label, name in [('DaOS-state', 'state'), ('DaOS-photos', 'photos')]:
        partition = next(part for part in image['raw_gpt']['partitions'] if part['label'] == label)
        begin, end = partition['first_lba'] * 512, (partition['last_lba'] + 1) * 512
        if begin % 4096 or end % 4096:
            raise ValueError('Partition does not meet explicit block alignment')
        target = args.slice_dir / (name + '.ufs')
        command = ['qemu-img', 'dd', '-f', 'qcow2', 'if=' + str(parent),
                   'of=' + str(target), 'bs=4096', 'skip=' + str(begin // 4096),
                   'count=' + str(end // 4096)]
        run(command, args.output / (name + '-slice-command.json'))
        if target.stat().st_size != partition['size_bytes']:
            raise ValueError('QEMU slice length mismatch; exit status alone is insufficient')
        run([str(binaries / 'fsstat'), '-f', 'ufs2', str(target)],
            args.output / (name + '-fsstat.json'), env=env)
        listing = run([str(binaries / 'fls'), '-r', '-p', '-f', 'ufs2', str(target)],
                      args.output / (name + '-fls.json'), env=env).decode()
        paths = []
        for line in listing.splitlines():
            match = re.fullmatch(r'r/r\s+(\d+):\t(.+)', line)
            if not match:
                continue
            inode, rel = int(match[1]), PurePosixPath(match[2])
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError('Unsafe path in forensic listing')
            text = str(rel)
            selected = (name == 'photos' or text.lower().endswith('.dng')
                        or text.startswith(('DaOS/camera/', 'DaOS/ui/', 'DaOS/config/', 'DaOS/home/'))
                        or text in ('log/DaOS-camera.log', 'log/DaOS-Xorg.log'))
            paths.append({'path': text, 'inode': inode, 'selected': selected})
            if not selected:
                continue
            destination = args.output / name / Path(*rel.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = run([str(binaries / 'icat'), '-f', 'ufs2', str(target), str(inode)],
                          args.output / (name + '-icat-' + str(inode) + '.json'), env=env, binary=True)
            with destination.open('xb') as output:
                output.write(payload)
            extracted.append({'partition': label, 'path': text, 'inode': inode,
                              'output': destination.relative_to(args.output).as_posix(),
                              'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
        slices.append({'partition': partition, 'file': str(target), 'bytes': target.stat().st_size,
                       'sha256': digest(target), 'allocated_regular_paths': paths})
    unchanged = {'parent_cow': digest(parent) == parent_before,
                 'source_raw': digest(source) == source_before}
    backing_after = {item['path']: digest(item['path']) if Path(item['path']) != source else source_before
                     for item in image['backing_inputs']}
    unchanged['readonly_backing_chain'] = backing_before == backing_after
    receipt = {'format': 'DaOS-stopped-capture-UFS-forensics-v1',
               'host_uid': os.geteuid(), 'host_gid': os.getegid(), 'host': list(os.uname()),
               'source_image_sha256': source_before, 'parent_cow_sha256': parent_before,
               'observation_sha256': digest(args.work / 'observation.json'),
               'script_sha256': digest(__file__),
               'tools_provenance_sha256': digest(args.tools / 'tools-provenance.json'),
               'clean_stop_verified': True, 'images_unchanged': unchanged,
               'readonly_backing_before': backing_before, 'readonly_backing_after': backing_after,
               'slices': slices, 'extracted': extracted, 'host_mounts': False,
               'VM_lifecycle_actions': False, 'live_lock_overrides': False}
    save_json(args.output / 'extraction-receipt.json', receipt)
    if not all(unchanged.values()):
        raise ValueError('Source identity changed during inspection')
    print(json.dumps({'passed': True, 'extracted': extracted, 'receipt': str(args.output / 'extraction-receipt.json')}, indent=2))


if __name__ == '__main__':
    main()
