"""Exercise the unchanged device broker, SDK and fixed stdio bridge on FreeBSD.

No USB device, exposure hardware, HTTP listener or network interface is used.
The test refuses to replace any existing appliance socket.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'bundle/ui/on-device'),str(ROOT/'bundle')]
from PyQt5.QtCore import QCoreApplication,QObject,QMetaObject,Qt,pyqtProperty,pyqtSignal
from camera_link import CameraLink,CHUNK_SIZE,peer_uid
from daos.camera_link import CameraClient,LinkError
from daos.camera_link.client import SocketTransport,StreamTransport

QA=ROOT/('qa-unix-link-'+str(time.time_ns()))
QA.mkdir(exist_ok=False)
SOURCE=ROOT/'truth-24mp.dng'
BRIDGE=ROOT/'bundle/DaOS-camera-link.py'
ENDPOINT=Path('/var/DaOS/ui/camera-link.sock')
assert not ENDPOINT.exists() and not ENDPOINT.is_symlink(),'Refusing an existing device socket'
created=[]
for directory in (ENDPOINT.parent.parent,ENDPOINT.parent):
    if not directory.exists():directory.mkdir();created.append(directory)
    assert directory.is_dir() and not directory.is_symlink()

class Camera(QObject):
    connectionState='ready';captureState='idle';lastRequestId=''
    confirmedSettings={'requested_exposure_us':10000};settingsView={'canCapture':True}
    capabilities={'simulated':True}
    def __init__(self):super().__init__();self.requests=[]
    def captureWithId(self,identity,origin):self.requests.append(identity);self.lastRequestId=identity;return True
    def setSettings(self,patch,origin):return 1

class Service:
    def __init__(self):self.opened=0
    def get_library_asset(self,photo):return {'photo_uuid':photo,'library_id':'native-fixture','extension':'.dng'}
    def open_library_asset(self,photo):
        service=self
        class Stream:
            def __init__(self):self.source=SOURCE.open('rb');service.opened+=1
            def read(self,*args):return self.source.read(*args)
            def readinto(self,*args):return self.source.readinto(*args)
            def readable(self):return True
            def seek(self,*args):return self.source.seek(*args)
            def fileno(self):return self.source.fileno()
            def close(self):
                if not self.source.closed:service.opened-=1
                self.source.close()
        return Stream()
    def query_capture(self,identity):return {'request_id':identity,'status':'unknown'}

class Broker(CameraLink):
    @property
    def socketPath(self):return str(ENDPOINT)

class MainCalls(QObject):
    invoke=pyqtSignal(object,object)
    def __init__(self):super().__init__();self.invoke.connect(self.perform)
    def perform(self,function,future):
        try:future.set_result(function())
        except BaseException as error:future.set_exception(error)
    def run(self,function):
        future=concurrent.futures.Future();self.invoke.emit(function,future);return future.result(timeout=30)

app=QCoreApplication([]);camera=Camera();service=Service();main=MainCalls()
report={'format':'daos.camera-link-native-validation.v1','platform':platform.platform(),
        'hardware_camera':False,'physical_usb':False,'lan_listener_started':False,'checks':[]}
# Only nodes made by this fixture are tested. No existing appliance endpoint is
# ever taken over; the precondition above refuses it before any construction.
unknown=Broker(camera,QA/'identity-probe',client_getter=lambda:service)
ENDPOINT.write_bytes(b'ordinary owner data')
try:
    try:unknown.startSocket();raise AssertionError('ordinary file was treated as a stale socket')
    except ValueError:pass
    assert ENDPOINT.read_bytes()==b'ordinary owner data'
finally:unknown.close();ENDPOINT.unlink()
stale=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);stale.bind(str(ENDPOINT));stale.close()
assert stat.S_ISSOCK(ENDPOINT.lstat().st_mode) and ENDPOINT.lstat().st_uid==os.getuid()
broker=Broker(camera,QA,client_getter=lambda:service)
broker.startSocket()
assert stat.S_IMODE(ENDPOINT.stat().st_mode)==0o600
active_node=ENDPOINT.lstat()
second=Broker(camera,QA/'active-probe',client_getter=lambda:service)
try:
    try:second.startSocket();raise AssertionError('second broker replaced a live endpoint')
    except ValueError:pass
    unchanged=ENDPOINT.lstat()
    assert (active_node.st_dev,active_node.st_ino)==(unchanged.st_dev,unchanged.st_ino)
finally:second.close()
report['checks'].append('Owned stale AF_UNIX bind/close node safely rebuilt; ordinary file rejected and preserved; second live broker rejected without unlinking active endpoint')
probe_left,probe_right=socket.socketpair()
try:assert peer_uid(probe_left)==os.getuid()
finally:probe_left.close();probe_right.close()

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def scenario():
    global broker
    client=CameraClient(SocketTransport(ENDPOINT))
    assert client.state()['simulated'] is True
    try:client.acquire();raise AssertionError('disabled remote control accepted')
    except LinkError:pass
    main.run(lambda:broker.setEnabled(True));client.acquire();client.configure(requested_exposure_us=10000)
    identity=str(uuid.uuid4());client.capture(identity)
    assert client.query(identity)['request_id']==identity
    main.run(broker.takeLocalControl)
    try:client.capture(str(uuid.uuid4()));raise AssertionError('local takeover did not revoke control')
    except LinkError:pass
    assert camera.requests==[identity]
    client.close();report['checks'].append('AF_UNIX state/configure/capture UUID/query/local takeover; exactly one fake exposure')

    class Disconnect(SocketTransport):
        chunks=0
        def call(self,request):
            if request['method']=='transfer.getChunk':
                self.chunks+=1
                if self.chunks>2:raise OSError('Injected connection interruption')
            return super().call(request)
    photo=str(uuid.uuid4());target=QA/'socket-resumed.dng';client=CameraClient(Disconnect(ENDPOINT))
    try:client.download(photo,target);raise AssertionError('disconnect injection failed')
    except OSError:pass
    client.close()
    assert not target.exists() and target.with_name(target.name+'.part').stat().st_size==2*CHUNK_SIZE
    checkpoint=json.loads(target.with_name(target.name+'.transfer.json').read_text())
    paused=CameraClient(SocketTransport(ENDPOINT));paused.call('transfer.pause',{'transfer_id':checkpoint['transfer_id']})
    assert service.opened==0
    try:paused.call('transfer.getChunk',{'transfer_id':checkpoint['transfer_id'],'offset':2*CHUNK_SIZE});raise AssertionError('paused transfer served bytes')
    except LinkError:pass
    paused.close()
    def restart():
        global broker
        broker.close();assert service.opened==0
        broker=Broker(camera,QA,client_getter=lambda:service);broker.startSocket()
    main.run(restart)
    client=CameraClient(SocketTransport(ENDPOINT));result=client.download(photo,target)
    assert result['sha256']==sha(SOURCE)==sha(target) and result['bytes']==SOURCE.stat().st_size
    assert service.opened==0
    try:client.download(photo,target);raise AssertionError('existing target was overwritten')
    except LinkError:pass
    client.close();report['checks'].append('Real binary socket transfer interrupted after 512KiB; pause releases pin and blocks bytes; broker restart/explicit SDK checkpoint resume/SHA commit/existing destination rejection')

    process=subprocess.Popen([sys.executable,'-B',str(BRIDGE)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    client=CameraClient(StreamTransport(process.stdout,process.stdin))
    try:
        assert client.state()['simulated'] is True
        assert client.query(identity)['request_id']==identity
        output=QA/'stdio-full.dng';result=client.download(str(uuid.uuid4()),output)
        assert result['sha256']==sha(SOURCE)==sha(output)
        assert camera.requests==[identity]
    finally:
        client.close();process.wait(timeout=15);error=process.stderr.read();process.stderr.close()
    assert process.returncode==0,error
    report['checks'].append('Unmodified fixed stdio bridge -> real AF_UNIX broker -> SDK, 48,998,206-byte native DNG verified; no re-exposure')

    # The socket is normally 0600. Briefly permitting connect tests the second,
    # independent peer-UID gate against nobody; this is only this test socket.
    os.chmod(ENDPOINT,0o666)
    probe='import socket;s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect('+repr(str(ENDPOINT))+');s.sendall(b\'{"method":"getState"}\\n\');print(repr(s.recv(100)))'
    def nobody():os.setgid(65534);os.setuid(65534)
    try:
        rejected=subprocess.run([sys.executable,'-B','-c',probe],capture_output=True,text=True,preexec_fn=nobody,timeout=10)
        assert rejected.stdout.strip()=="b''" or 'reset' in rejected.stderr.lower(),(rejected.stdout,rejected.stderr)
    finally:os.chmod(ENDPOINT,0o600)
    assert not broker.webEnabled and service.opened==0
    report['checks'].append('0600 socket mode and FreeBSD getpeereid rejected unauthorized nobody UID, even with temporary connect permission')
    report['raw_sha256']=sha(SOURCE);report['bytes']=SOURCE.stat().st_size
    report['source_files']={str(path.relative_to(ROOT/'bundle')):sha(path) for path in (ROOT/'bundle/ui/on-device/camera_link.py',ROOT/'bundle/daos/camera_link/client.py',BRIDGE)}

executor=concurrent.futures.ThreadPoolExecutor(max_workers=1)
future=executor.submit(scenario)
try:
    deadline=time.monotonic()+180
    while not future.done():
        app.processEvents();time.sleep(.002)
        if time.monotonic()>deadline:raise TimeoutError('Unix integration exceeded 180 seconds')
    future.result()
finally:
    broker.close();executor.shutdown(wait=True)
    for directory in reversed(created):
        if not any(directory.iterdir()):directory.rmdir()
assert not ENDPOINT.exists() and service.opened==0
report.update(passed=True,fixture_closed_cleanly=True,active_endpoint_removed=True,asset_pins_released=True)
(ROOT/'unix-link-validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
