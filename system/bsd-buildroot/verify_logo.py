#!/usr/bin/env python3
"""Observe an actual QEMU framebuffer and compare its logo pixels to the asset."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from PIL import Image, ImageChops
import vm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--asset', type=Path, default=Path(__file__).resolve().parents[2] / 'assets/boot-logo/DaOS-logo.png')
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--freebsd-desktop', action='store_true', help='use the owned FreeBSD desktop launcher record')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error('Use an observation window of 1 to 600 seconds')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    logo = Image.open(args.asset).convert('RGB')
    if args.freebsd_desktop:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tools import launch_bsd_desktop as desktop
        state = desktop.read_json(args.run_dir / 'status.json')
        if not desktop.alive(state):
            raise ValueError('The owned FreeBSD desktop instance is not running')
        client = vm.QMP(args.run_dir / 'qmp.sock', expected_pid=state['qemu_pid'])
        name = client.call('query-name')['name']
        if name != 'DaOS-BSD-R14':
            client.close()
            raise ValueError('Unexpected desktop QEMU identity')
        record = {'name': name, 'uuid': state['run'], 'raw_sha256': state['image_sha256']}
    else:
        record, client = vm.identity(args.run_dir)
    evidence = {'format': 'DaOS-FreeBSD-framebuffer-logo-v1' if args.freebsd_desktop else 'DaOS-NetBSD-framebuffer-logo-v1', 'vm_name': record['name'],
                'vm_uuid': record['uuid'], 'raw_image_sha256': record['raw_sha256'],
                'asset_sha256': vm.sha(args.asset), 'asset_rgb_sha256': hashlib.sha256(logo.tobytes()).hexdigest(),
                'asset_size': list(logo.size), 'physical_Pi4_validation': False,
                'logo_observed': False, 'frames': []}
    start = time.monotonic()
    last = None
    index = 0
    try:
        while time.monotonic() - start < args.seconds:
            temporary = args.output_dir / 'current.ppm'
            client.call('screendump', {'filename': str(temporary.absolute())})
            with Image.open(temporary) as source:
                frame = source.convert('RGB')
            frame_hash = hashlib.sha256(frame.tobytes()).hexdigest()
            found = None
            if frame.width >= logo.width and frame.height >= logo.height:
                for x, y in (((frame.width - logo.width)//2, (frame.height - logo.height)//2), (0, 0)):
                    crop = frame.crop((x, y, x + logo.width, y + logo.height))
                    if ImageChops.difference(crop, logo).getbbox() is None:
                        found = {'x': x, 'y': y, 'width': logo.width, 'height': logo.height,
                                 'exact_rgb_pixel_match': True}
                        break
            if frame_hash != last or found:
                name = f'frame-{index:04d}.png'
                frame.save(args.output_dir / name)
                item = {'file': name, 'seconds_after_observation_start': round(time.monotonic()-start, 3),
                        'rgb_sha256': frame_hash, 'png_sha256': vm.sha(args.output_dir / name),
                        'size': list(frame.size), 'logo_match': found}
                evidence['frames'].append(item)
                print(json.dumps(item), flush=True)
                last = frame_hash
                index += 1
            temporary.unlink()
            if found:
                evidence.update(logo_observed=True, matched_frame=name, match=found)
                break
            time.sleep(.5)
    finally:
        client.close()
        evidence['observation_seconds'] = round(time.monotonic()-start, 3)
        (args.output_dir / 'logo-validation.json').write_text(json.dumps(evidence, indent=2)+'\n')
    if not evidence['logo_observed']:
        raise SystemExit('No exact logo pixels observed in this boot window; see saved frames')


if __name__ == '__main__':
    main()
