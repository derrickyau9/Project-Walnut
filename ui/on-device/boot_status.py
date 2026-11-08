"""Real startup phases and the framebuffer-to-Qt first-frame handoff."""
import os
from pathlib import Path
import re
from PyQt5.QtCore import QObject,pyqtSignal,pyqtProperty

def marker(variable):
    value=os.environ.get(variable,'')
    if not value:return
    path=Path(value)
    if path.parent!=Path('/var/run/DaOS-camera/client') or not re.fullmatch('(splash|ready)-[0-9a-f]{16}',path.name):return
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        os.write(fd,b'ready\n');os.close(fd)
    except FileExistsError:pass

class BootStatus(QObject):
    changed=pyqtSignal()
    def __init__(self,parent=None):
        super().__init__(parent);self._progress=.65;self._stage='Loading interface';self._ready=False
    @pyqtProperty(float,notify=changed)
    def progress(self):return self._progress
    @pyqtProperty(str,notify=changed)
    def stage(self):return self._stage
    @pyqtProperty(bool,notify=changed)
    def ready(self):return self._ready
    def phase(self,value,text):
        self._progress=max(self._progress,value);self._stage=text;self.changed.emit()
    def finish(self):
        if self._ready:return
        handoff=os.environ.get('DAOS_NATIVE_HANDOFF_FILE','')
        if handoff and not Path(handoff).is_file():return
        self._progress=1;self._stage='Ready';self._ready=True;marker('DAOS_UI_READY_FILE');self.changed.emit()
