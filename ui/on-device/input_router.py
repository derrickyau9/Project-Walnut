"""Dedicated camera keys and a queued-slot entrypoint for a future MCU bridge.

Ordinary text keys are left to Qt editors. A physical full press is edge
triggered; repeats and duplicate down events cannot submit another photograph.
"""
from PyQt5.QtCore import QObject, QEvent, Qt, pyqtSignal, pyqtSlot


class CameraInputRouter(QObject):
    returnToCapture = pyqtSignal()
    shutterRequested = pyqtSignal()
    dialRequested = pyqtSignal(int)
    fnRequested = pyqtSignal(str, bool)
    backRequested = pyqtSignal()

    def __init__(self, app, parent=None):
        super().__init__(parent or app)
        self.app = app
        self._down = set()
        app.installEventFilter(self)

    @pyqtSlot(str, bool, result=bool)
    @pyqtSlot(str, bool, int, result=bool)
    def dispatch(self, event, pressed=True, value=0):
        if event == 'dial':
            if pressed and type(value) is int and -1000000 <= value <= 1000000 and value: self.dialRequested.emit(value)
            return True
        if event not in {'half_press','shutter','back','fn1','fn2'}: return False
        if not pressed:
            held = event in self._down
            self._down.discard(event)
            if held and event in {'fn1','fn2'}: self.fnRequested.emit(event,False)
            return True
        if event in self._down: return True
        self._down.add(event)
        if event == 'half_press': self.returnToCapture.emit()
        elif event == 'shutter':
            self.returnToCapture.emit()
            self.shutterRequested.emit()
        elif event == 'back': self.backRequested.emit()
        else: self.fnRequested.emit(event,True)
        return True

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.ApplicationDeactivate,QEvent.WindowDeactivate):
            for key in list(self._down): self.dispatch(key,False)
        if event.type() in (QEvent.KeyPress,QEvent.KeyRelease):
            mapping={Qt.Key_CameraFocus:'half_press',Qt.Key_Camera:'shutter'}
            key=mapping.get(event.key())
            if key is not None:
                if not event.isAutoRepeat(): self.dispatch(key,event.type()==QEvent.KeyPress)
                return True
        return False
