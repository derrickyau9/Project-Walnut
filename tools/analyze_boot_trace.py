"""Summarize board-local boot markers without inventing a power-on timestamp."""
import argparse
import json
from pathlib import Path
import re

EVENTS={'session-start','Xorg-started','camera-started','display-ready',
        'native-splash-handoff','ui-ready','python-preload-start','python-preload-end','camera-frame-presented'}


def analyze(lines):
    events={}
    for line in lines:
        if len(line)>65536:continue
        match=re.search(r'(?:DaOS_GUI_EVENT|DaOS_BOOT_MARK) (\{.*\})',line)
        if not match:continue
        try:value=json.loads(match.group(1))
        except ValueError:continue
        name=value.get('event');clock=value.get('monotonic_ns')
        if name not in EVENTS or type(clock) is not int or clock<0:continue
        if name=='session-start':events={}
        if name not in events:events[name]=value
    def delta(first,last):
        if first not in events or last not in events:return None
        elapsed=events[last]['monotonic_ns']-events[first]['monotonic_ns']
        if elapsed<0:raise ValueError('Mixed/reversed clock domains in boot markers')
        return round(elapsed/1e6,3)
    frame=events.get('camera-frame-presented')
    return {'format':'Project-Walnut-boot-stage-timing-v1',
        'scope':'Last supervisor session; board-local monotonic clock; physical power-on start is not available in this log',
        'power_on_to_UI_ms':None,'physical_power_on_measurement_required':True,
        'camera_frame_presented':frame is not None,
        'authority_ready_at_frame':frame.get('authority_ready') if frame else None,
        'session_to_camera_frame_ms':delta('session-start','camera-frame-presented'),
        'Xorg_to_display_ready_ms':delta('Xorg-started','display-ready'),
        'python_preload_ms':delta('python-preload-start','python-preload-end'),
        'application_start_to_camera_frame_ms':delta('camera-started','camera-frame-presented'),
        'events':events}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    with args.log.open(encoding='utf-8',errors='replace') as source:report=analyze(source)
    data=json.dumps(report,indent=2)+'\n'
    if args.output:
        with args.output.open('x',encoding='utf-8') as destination:destination.write(data)
    else:print(data,end='')


if __name__=='__main__':main()
