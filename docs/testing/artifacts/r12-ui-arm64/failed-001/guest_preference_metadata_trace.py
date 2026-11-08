from pathlib import Path
import hashlib,importlib.util,json,os,platform,sys,time
project=Path('/home/camera/DaOS-UI-R12-project-001');sys.path.insert(0,str(project));os.chdir(project)
assert platform.system()=='FreeBSD' and platform.machine() in ('arm64','aarch64') and os.geteuid()==1000
output=Path('/home/camera/DaOS-UI-R12-preference-metadata-001');output.mkdir(mode=0o700)
os.environ.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QSG_RENDER_LOOP='basic',QML_DISABLE_DISK_CACHE='1',PYTHONDONTWRITEBYTECODE='1',XDG_RUNTIME_DIR=str(project/'runtime'),TMPDIR=str(project/'tmp'))
spec=importlib.util.spec_from_file_location('metadata_camera_first_tests',project/'tests/test_camera_first_ui.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from PyQt5 import QtTest
events=[]
def ident(info):return {'dev':info.st_dev,'ino':info.st_ino,'size':info.st_size,'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns}
def trace(stage,store,shell=None):
    before=store.path.lstat();descriptor=os.open(store.path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        opened=os.fstat(descriptor);data=os.read(descriptor,8193);after_read=os.fstat(descriptor)
    finally:os.close(descriptor)
    value={'stage':stage,'monotonic':time.monotonic(),'expected':store.expected,'lstat_before':ident(before),
           'fdstat_opened':ident(opened),'fdstat_after_read':ident(after_read),'lstat_after':ident(store.path.lstat()),
           'sha256':hashlib.sha256(data).hexdigest(),'document':data.decode('utf-8'),
           'shell_error':None if shell is None else shell.errorText,
           'shell_grid':None if shell is None else shell.gridEnabled}
    events.append(value);print(json.dumps(value,sort_keys=True),flush=True)
Case=module.CameraFirstUITests;Case.setUpClass();case=Case('test_display_preferences_survive_recreation_without_sensor_changes');case.setUp()
Store=module.shell_module._PreferenceStore;original=Store.save
def traced_save(store,*args,**kwargs):
    value=original(store,*args,**kwargs)
    if not events:trace('immediately-after-initial-theme-save',store)
    return value
try:
    Store.save=traced_save
    case.launch()
    Store.save=original
    store=case.shell._state._store
    trace('after-launch',store,case.shell);QtTest.QTest.qWait(300);trace('after-idle-300ms',store,case.shell)
    case.click('menuButton');trace('after-enter-menu',store,case.shell)
    case.click('menuCategory-display');trace('before-grid-click',store,case.shell)
    case.click('menuGridSwitch');trace('after-grid-click',store,case.shell)
    assert case.capture_rows()==[]
    with (output/'metadata-trace.json').open('x') as f:json.dump({'format':'DaOS-r12-freebsd-preference-metadata-trace-v1','uid':os.geteuid(),'events':events,'capture_rows':[],'qml_warnings':case.warnings},f,sort_keys=True,indent=2);f.write('\n')
finally:
    Store.save=original;case.tearDown()
