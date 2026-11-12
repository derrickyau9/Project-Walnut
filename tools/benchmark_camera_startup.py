"""Measure fresh-process desktop camera readiness, without taking a photo.

This is not power-on, cold-disk or physical Pi timing. Each sample uses new
camera/state/cache directories. Existing device data and USB are not accessed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def child(args):
    started=time.perf_counter()
    os.environ.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QSG_RENDER_LOOP='basic')
    import runpy
    from PyQt5 import QtQml
    from PyQt5.QtCore import QObject,QTimer
    from PyQt5.QtGui import QGuiApplication
    original=QtQml.QQmlApplicationEngine
    report={'first_frame_ms':None,'qml_load_ms':None}
    class Engine(original):
        def load(self,*values):
            before=time.perf_counter();super().load(*values)
            report['qml_load_ms']=(time.perf_counter()-before)*1000
            if not self.rootObjects():return
            window=self.rootObjects()[0]
            def frame():
                if report['first_frame_ms'] is None:report['first_frame_ms']=(time.perf_counter()-started)*1000
            window.frameSwapped.connect(frame)
            def check():
                camera=self.rootContext().contextProperty('camera')
                if report['first_frame_ms'] is not None and camera.canCapture:
                    report.update(capture_ready_ms=(time.perf_counter()-started)*1000,
                                  qobjects=len(window.findChildren(QObject)),passed=True)
                    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
                    QGuiApplication.instance().quit()
                else:QTimer.singleShot(10,check)
            QTimer.singleShot(0,check)
            QTimer.singleShot(15000,lambda:QGuiApplication.instance().exit(3))
    QtQml.QQmlApplicationEngine=Engine
    sys.path.insert(0,str(ROOT/'ui/on-device'))
    sys.argv=['main.py','--offscreen','--backend',args.backend,'--data-dir',str(args.output.with_suffix(''))]
    if args.native_command:sys.argv+=['--native-command',str(args.native_command)]
    runpy.run_path(str(ROOT/'ui/on-device/main.py'),run_name='__main__')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='new directory for a report, or child JSON path')
    parser.add_argument('--samples',type=int,default=5)
    parser.add_argument('--backend',choices=('reference','native'),default='reference')
    parser.add_argument('--native-command',type=Path)
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.child:return child(args)
    if not 1<=args.samples<=20:parser.error('samples must be between 1 and 20')
    if args.backend=='native' and not args.native_command:parser.error('native benchmarking requires --native-command')
    args.output=args.output.resolve();args.output.mkdir(parents=True,exist_ok=False)
    inputs=(ROOT/'ui/on-device/Main.qml',ROOT/'ui/on-device/DeferredPage.qml')
    before={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    samples=[]
    for index in range(args.samples):
        record=args.output/f'sample-{index+1}.json'
        command=[sys.executable,str(Path(__file__).resolve()),'--child','--output',str(record),'--backend',args.backend]
        if args.native_command:command+=['--native-command',str(args.native_command.resolve(strict=True))]
        result=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=35)
        (args.output/f'sample-{index+1}.stderr').write_bytes(result.stderr)
        if result.returncode or not record.is_file():raise RuntimeError(f'Benchmark sample {index+1} failed; see its stderr')
        samples.append(json.loads(record.read_text()))
    report={'format':'Project-Walnut-desktop-startup-benchmark-v1',
        'scope':'child script entry to first frame/capture readiness; fresh state/cache per sample; not physical boot or cold disk',
        'platform':platform.platform(),'backend':args.backend,'samples':samples,
        'median':{key:round(statistics.median(row[key] for row in samples),3)
                  for key in ('first_frame_ms','capture_ready_ms','qml_load_ms','qobjects')},
        'source_sha256':before,
        'source_changed_during_benchmark':any(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=value for path,value in before.items())}
    if report['source_changed_during_benchmark']:raise RuntimeError('UI sources changed during the benchmark')
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['median']))


if __name__=='__main__':main()
