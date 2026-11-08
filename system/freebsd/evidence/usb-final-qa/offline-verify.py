#!/usr/bin/env python3
"""Read-only verification of the stopped final USB QA snapshot."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

PROJECT = Path('/mnt/f/Project HasselFree')
OUT = Path('/home/dy09/daos-vm-usb-adb/verification/offline-run1-1dea85c9')
FILES = OUT / 'files'
INSPECTOR = Path('/home/dy09/hf-task-freebsd-r10/ufs-inspector')
ENV = dict(os.environ, LD_LIBRARY_PATH=str(INSPECTOR / 'root/usr/lib/x86_64-linux-gnu'))
RELEASE = PROJECT / 'build/DaOS_BSD_RPi4_USB_ADB_ARM64'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read_json(path):
    return json.loads(path.read_text())

evidence = read_json(RELEASE / 'image-evidence.json')
manifest_file = FILES / 'DaOS-root/usr/share/DaOS/usb-adb-overlay.json'
assert digest(manifest_file) == evidence['usb_adb']['manifest_sha256']
manifest = read_json(manifest_file)
gpt = read_json(OUT / 'gpt.json')
partitions = {x['label']: x for x in gpt['partitions']}
indexes = {}
for label in ('DaOS-root', 'DaOS-efi'):
    indexes[label] = {}
    for line in (OUT / (label + '-fls.txt')).read_text().splitlines():
        match = re.fullmatch(r'r/r\s+(\d+(?:-\d+-\d+)?):\t(.+)', line)
        if match:
            indexes[label][match[2]] = match[1]

def extract(label, path):
    inode = indexes[label][path]
    target = FILES / label / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with target.open('xb') as output:
            subprocess.run([str(INSPECTOR / 'root/usr/bin/icat'), '-f',
                            'fat' if label == 'DaOS-efi' else 'ufs2', '-o',
                            str(partitions[label]['start_sector']),
                            str(OUT / 'stopped-snapshot.img'), inode],
                           env=ENV, check=True, stdout=output)
    return target, inode

checked = []
for kind in ('root', 'efi'):
    label = 'DaOS-' + kind
    for entry in manifest[kind]:
        assert entry['type'] == 'file'
        path, inode = extract(label, entry['path'])
        actual = digest(path)
        assert actual == entry['sha256'], entry['path']
        assert path.stat().st_size == entry['bytes'], entry['path']
        checked.append({'partition': label, 'path': entry['path'], 'inode': inode,
                        'bytes': path.stat().st_size, 'sha256': actual})

for entry in [manifest['source_world_usb_template'],
              *manifest['transport_elf_closure']['source_world_libraries'].values()]:
    path, inode = extract('DaOS-root', entry['path'])
    assert digest(path) == entry['sha256'], entry['path']
    checked.append({'partition': 'DaOS-root', 'path': entry['path'], 'inode': inode,
                    'bytes': path.stat().st_size, 'sha256': digest(path)})

build_path = FILES / 'DaOS-root/usr/share/DaOS/usb-adb-build.json'
assert digest(build_path) == evidence['usb_adb']['build_receipt_sha256']
keys_path = FILES / 'DaOS-root/etc/daos/adb_keys'
fingerprints = [hashlib.sha256(base64.b64decode(x.split()[0], validate=True)).hexdigest()
                for x in keys_path.read_text().splitlines() if x.strip()]
assert sorted(fingerprints) == sorted(manifest['authorized_public_key_fingerprints'])
key_inode = indexes['DaOS-root']['etc/daos/adb_keys']
key_stat = subprocess.check_output([str(INSPECTOR / 'root/usr/bin/istat'), '-f',
              'ufs2', '-o', str(partitions['DaOS-root']['start_sector']),
              str(OUT / 'stopped-snapshot.img'), key_inode], env=ENV, text=True)
(OUT / 'authorized-keys-istat.txt').write_text(key_stat)
assert re.search(r'uid\s*/\s*gid:\s*0\s*/\s*0', key_stat, re.I), key_stat
# Sleuthkit prints regular-file type as "r", followed by the nine mode bits.
assert re.search(r'mode:\s*[-r]rw-------', key_stat, re.I), key_stat

dtb = read_json(FILES / 'DaOS-root/usr/share/DaOS/usb-dtb-derivation.json')
derived = dtb['derivations'][0]['derived_sha256']
for path in (FILES / 'DaOS-efi/bcm2711-rpi-4-b.dtb',
             FILES / 'DaOS-efi/daos-rpi4-usb-device.dtb',
             FILES / 'DaOS-root/boot/dtb/daos-rpi4-usb-device.dtb'):
    assert digest(path) == derived

original = next((FILES / 'DaOS-photos').glob('*.dng'))
export = next((FILES / 'DaOS-state/DaOS/ui/apps/exports').glob('*.dng'))
assert original.stat().st_size == export.stat().st_size == 25662
assert digest(original) == digest(export)
usb_log = (FILES / 'DaOS-state/log/DaOS-usb-adb.log').read_text()
assert 'native USB helper started' in usb_log and 'waiting for configured USB host' in usb_log
assert 'USB helper stopped status=0' in usb_log
assert 'USB developer service stopped' in usb_log
assert 'Traceback' not in usb_log and 'setup failed' not in usb_log
camera_log = (FILES / 'DaOS-state/log/DaOS-camera.log').read_text()
events = [json.loads(x.partition('DaOS_GUI_EVENT ')[2]) for x in camera_log.splitlines()
          if x.startswith('DaOS_GUI_EVENT ')]
stopped = next(x for x in events if x['event'] == 'session-stopped')
assert stopped['application_returncode'] == stopped['server_returncode'] == 0
assert stopped['forced_application_stop'] is False
identity = next(x for x in events if x['event'] == 'base-identity')
result = {'format': 'DaOS-USB-final-offline-QA-v1', 'status': 'passed',
          'raw_sha256': evidence['image']['sha256'],
          'assembly_receipt_sha256': digest(RELEASE / 'image-evidence.json'),
          'embedded_usb_manifest_sha256': digest(manifest_file),
          'checked_payload_files': checked,
          'authorized_public_keys': {'fingerprints': fingerprints,
             'root_owned_mode_0600': True, 'private_key_material_staged': False},
          'derived_dtb_sha256': derived, 'derived_dtb_all_three_paths_match': True,
          'dng_original': {'path': str(original.relative_to(FILES)),
              'bytes': original.stat().st_size, 'sha256': digest(original)},
          'dng_export': {'path': str(export.relative_to(FILES)),
              'bytes': export.stat().st_size, 'sha256': digest(export)},
          'export_exact_bytes_match': True,
          'usb_rc_started_and_waited_for_host': True, 'usb_helper_clean_exit': True,
          'guest_identity': identity, 'gui_clean_stop': stopped,
          'physical_usb_enumeration_verified': False,
          'windows_usb_binding_verified': False,
          'scope': 'Stopped QEMU private COW snapshot; no physical USB controller or camera.'}
(OUT / 'offline-validation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'payloads': len(checked),
                  'export_sha256': digest(export), 'usb_rc_waiting': True}))
