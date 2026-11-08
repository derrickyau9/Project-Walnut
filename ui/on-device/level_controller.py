"""Optional, bounded attitude input for the display-only electronic level."""
from pathlib import Path
import json
import math
import os
import stat
import time

from PyQt5.QtCore import QObject,QTimer,pyqtProperty,pyqtSignal,pyqtSlot
from system_archive import atomic,strict_json

MAX_BYTES=4096
MAX_AGE_NS=500_000_000
FORMAT='Project-Walnut-attitude-v1'
PREFERENCES_FORMAT='Project-Walnut-level-preferences-v1'


def read_bounded(path):
    info=Path(path).lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
        raise ValueError('Linked attitude input is not supported')
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
        raise ValueError('Attitude input must be a bounded regular file')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
    try:
        before=os.fstat(descriptor)
        if (before.st_dev,before.st_ino)!=(info.st_dev,info.st_ino):
            raise ValueError('Attitude input identity changed while opening')
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
            raise ValueError('Attitude input must be a bounded regular file')
        raw=os.read(descriptor,MAX_BYTES+1)
        after=os.fstat(descriptor)
        if len(raw)>MAX_BYTES or len(raw)!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns:
            raise ValueError('Attitude input changed while reading')
        return raw
    finally:os.close(descriptor)


def angles(roll,pitch):
    for value,limit in ((roll,180),(pitch,90)):
        if type(value)not in (int,float) or abs(value)>limit or not math.isfinite(value):
            raise ValueError('Attitude angles must be finite and in range')
    return float(roll),float(pitch)


def decode_attitude(raw,now_ns):
    value=strict_json(raw)
    if (type(value)is not dict or set(value)!={'format','valid','monotonic_ns','roll_deg','pitch_deg'}
            or value['format']!=FORMAT or value['valid']is not True
            or type(value['monotonic_ns'])is not int or not 0 < value['monotonic_ns'] < 2**63):
        raise ValueError('Invalid attitude record')
    roll,pitch=angles(value['roll_deg'],value['pitch_deg'])
    age=now_ns-value['monotonic_ns']
    if age<0:raise ValueError('Attitude timestamp is in the future')
    return ('stale',None,None)if age>MAX_AGE_NS else ('ready',roll,pitch)


class LevelController(QObject):
    changed=pyqtSignal()

    def __init__(self,state_dir,attitude_file=None,demo_attitude=None,parent=None,clock_ns=time.monotonic_ns,poll_ms=100):
        super().__init__(parent)
        self.path=Path(state_dir).absolute()/'level-preferences.json'
        self._file=Path(attitude_file).absolute()if attitude_file is not None else None
        self._clock=clock_ns;self._settings_error=False;self._enabled=False
        self._demo=angles(*demo_attitude)if demo_attitude is not None else None
        self._state='disabled';self._roll=None;self._pitch=None;self._emitted=None
        try:
            value=strict_json(read_bounded(self.path))
            if (type(value)is not dict or set(value)!={'format','enabled'} or value['format']!=PREFERENCES_FORMAT
                    or type(value['enabled'])is not bool):raise ValueError('Invalid level preferences')
            self._enabled=value['enabled']
        except FileNotFoundError:pass
        except (OSError,ValueError,TypeError):self._settings_error=True
        self._timer=QTimer(self);self._timer.setInterval(max(20,poll_ms))
        self._timer.timeout.connect(self.refresh)
        if poll_ms>0:self._timer.start()
        self.refresh()

    @pyqtProperty(bool,notify=changed)
    def enabled(self):return self._enabled
    @pyqtProperty(bool,notify=changed)
    def available(self):return self._enabled and self._state in ('ready','demo')
    @pyqtProperty(bool,notify=changed)
    def isDemo(self):return self._demo is not None
    @pyqtProperty('QVariant',notify=changed)
    def rollDeg(self):return self._roll
    @pyqtProperty('QVariant',notify=changed)
    def pitchDeg(self):return self._pitch
    @pyqtProperty(bool,notify=changed)
    def aligned(self):return self.available and abs(self._roll)<=.5 and abs(self._pitch)<=.5
    @pyqtProperty(str,notify=changed)
    def state(self):return self._state
    @pyqtProperty(bool,notify=changed)
    def settingsError(self):return self._settings_error

    def _publish(self):
        current=(self._enabled,self.isDemo,self._state,self._roll,self._pitch,self._settings_error)
        if current!=self._emitted:self._emitted=current;self.changed.emit()

    @pyqtSlot(bool,result=bool)
    def setEnabled(self,value,persist=True):
        if type(value)is not bool:raise ValueError('Level enabled must be boolean')
        if persist:
            try:atomic(self.path,json.dumps({'format':PREFERENCES_FORMAT,'enabled':value}).encode())
            except (OSError,ValueError):self._settings_error=True;self._publish();return False
        self._enabled=value;self._settings_error=False;self.refresh();return True

    @pyqtSlot(float,float,result=bool)
    def setDemoAngles(self,roll,pitch):
        try:self._demo=angles(roll,pitch)
        except ValueError:return False
        self.refresh();return True

    @pyqtSlot()
    def clearDemo(self):self._demo=None;self.refresh()

    @pyqtSlot()
    def refresh(self):
        self._roll=None;self._pitch=None
        if not self._enabled:self._state='disabled'
        elif self._demo is not None:self._state='demo';self._roll,self._pitch=self._demo
        elif self._file is None:self._state='unavailable'
        else:
            try:self._state,self._roll,self._pitch=decode_attitude(read_bounded(self._file),self._clock())
            except FileNotFoundError:self._state='unavailable'
            except (OSError,ValueError,TypeError,OverflowError):self._state='invalid'
        self._publish()

    def close(self):self._timer.stop()
