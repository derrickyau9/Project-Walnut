#!/usr/bin/env python3
"""Operate only the selected second-boot final USB QA instance."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from PIL import Image

PROJECT = Path('/mnt/f/Project HasselFree')
WORK = Path('/home/dy09/daos-vm-usb-adb')
sys.path.insert(0, str(PROJECT / 'system/bsd-buildroot'))
sys.path.insert(0, str(PROJECT / 'tools'))
import vm
import launch_bsd_desktop as desktop

state = desktop.read_json(WORK / 'status.json')
assert state['run'] == '8b2c1d0a15e04e75890e74155855a14f'
assert state['image_sha256'] == 'ed84cce5eb14da514732f88909d0baee8f22266a3f837569185b30f855e1394e'
assert desktop.alive(state)
client = vm.QMP(WORK / 'qmp.sock', expected_pid=state['qemu_pid'])
assert client.call('query-name')['name'] == 'DaOS-BSD-R14'
action = {'time': datetime.now(timezone.utc).isoformat(), 'run': state['run'],
          'qemu_pid': state['qemu_pid'], 'arguments': sys.argv[1:]}
if sys.argv[1] == 'click':
    x, y = int(sys.argv[2]), int(sys.argv[3])
    assert 0 <= x < 800 and 0 <= y < 600
    client.call('input-send-event', {'events': [
        {'type': 'abs', 'data': {'axis': 'x', 'value': round(x * 32767 / 799)}},
        {'type': 'abs', 'data': {'axis': 'y', 'value': round(y * 32767 / 599)}}]})
    time.sleep(1)
    client.call('input-send-event', {'events': [
        {'type': 'btn', 'data': {'button': 'left', 'down': True}}]})
    time.sleep(.35)
    client.call('input-send-event', {'events': [
        {'type': 'btn', 'data': {'button': 'left', 'down': False}}]})
    time.sleep(2)
    name = sys.argv[4]
else:
    assert sys.argv[1] == 'screenshot'
    name = sys.argv[2]
assert '/' not in name and name.endswith('.png')
ppm = WORK / 'verification' / (name + '.ppm')
png = PROJECT / 'system/freebsd/evidence/usb-final-qa' / name
assert not ppm.exists() and not png.exists(), 'Preserve previous QA evidence.'
client.call('screendump', {'filename': str(ppm)})
client.close()
Image.open(ppm).save(png)
action.update(screenshot=str(png), screenshot_sha256=hashlib.sha256(png.read_bytes()).hexdigest())
with (WORK / 'verification/gui-restart-actions.jsonl').open('a') as output:
    output.write(json.dumps(action) + '\n')
print(json.dumps(action))
