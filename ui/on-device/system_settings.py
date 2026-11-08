"""Asynchronous client for the local, peer-authenticated Wi-Fi service."""
import json
import socket
import threading
from PyQt5.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot


class WifiController(QObject):
    changed = pyqtSignal()
    completed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._status_pending = False
        self._state = {'available': False, 'message': 'Checking wireless adapter', 'ssid': '', 'address': '', 'connected': False}
        self._networks = []
        self.completed.connect(self.apply)

    @pyqtProperty(bool, notify=changed)
    def busy(self): return self._busy
    @pyqtProperty(bool, notify=changed)
    def available(self): return self._state.get('available', False)
    @pyqtProperty(bool, notify=changed)
    def connected(self): return self._state.get('connected', False)
    @pyqtProperty(str, notify=changed)
    def message(self): return self._state.get('message', '')
    @pyqtProperty(str, notify=changed)
    def ssid(self): return self._state.get('ssid', '')
    @pyqtProperty(str, notify=changed)
    def address(self): return self._state.get('address', '')
    @pyqtProperty(str, notify=changed)
    def country(self): return self._state.get('country', '')
    @pyqtProperty('QVariantList', notify=changed)
    def networks(self): return self._networks

    def request(self, operation, **fields):
        if self._busy: return
        foreground = operation != 'status'
        if not foreground:
            if self._status_pending:return
            self._status_pending = True
        else:
            self._busy = True
            self.changed.emit()
        def worker():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(25)
                    s.connect('/var/run/DaOS-system/control.sock')
                    s.sendall(json.dumps(dict(op=operation, **fields)).encode()+b'\n')
                    data = bytearray()
                    while b'\n' not in data:
                        part = s.recv(4096)
                        if not part: raise OSError('service closed')
                        data.extend(part)
                        if len(data)>65536: raise ValueError('reply exceeds bound')
                result = json.loads(data.split(b'\n', 1)[0])
            except (OSError, ValueError, AttributeError):
                result = {'message': 'Wi-Fi service unavailable. Check the local system service.'}
            self.completed.emit((result,foreground))
        threading.Thread(target=worker, daemon=True).start()

    def apply(self, reply):
        result,foreground = reply
        if foreground:self._busy = False
        else:self._status_pending = False
        if 'networks' in result: self._networks = result.pop('networks')
        self._state.update(result)
        self.changed.emit()

    @pyqtSlot()
    def refresh(self): self.request('status')
    @pyqtSlot()
    def scan(self): self.request('scan')
    @pyqtSlot(str, str, bool)
    def connectNetwork(self, ssid, password, open_network):
        self.request('connect', ssid=ssid, password=password, open=bool(open_network))
    @pyqtSlot()
    def disconnectNetwork(self): self.request('disconnect')
    @pyqtSlot(str)
    def setCountry(self, country): self.request('region', country=country.strip().upper())
