"""Fixed privileged appliance operations; invoked only by the local peer service."""
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid

CONFIG=Path('/var/DaOS/system')
UPDATES=Path('/var/DaOS/updates')


def run(arguments,timeout=10):
    result=subprocess.run(arguments,check=True,capture_output=True,text=True,timeout=timeout)
    return result.stdout.strip()


def atomic(path,data,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data);os.chmod(temporary,mode);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        descriptor=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)


@contextmanager
def writable_root():
    readonly=bool(os.statvfs('/').f_flag & getattr(os,'ST_RDONLY',1))
    if readonly:run(['/sbin/mount','-u','-w','/'])
    try:yield
    finally:
        if readonly:run(['/sbin/mount','-u','-r','/'])


def backlight_device():
    utility=Path('/usr/bin/backlight')
    if not utility.is_file():return None
    for device in sorted(Path('/dev/backlight').glob('backlight[0-9]*')):
        if re.fullmatch('backlight[0-9]+',device.name):return str(device)
    return None


def status():
    device=backlight_device();value=None
    if device:
        try:value=int(run(['/usr/bin/backlight','-q','-f',device]))
        except (ValueError,subprocess.SubprocessError):device=None
    timezone=(Path('/var/db/zoneinfo').read_text().strip() if Path('/var/db/zoneinfo').is_file() else 'UTC')
    temperature=None
    try:temperature=run(['/sbin/sysctl','-n','dev.cpu.0.temperature'])
    except subprocess.SubprocessError:pass
    developer=(CONFIG/'developer-mode').read_text().strip()=='enabled' if (CONFIG/'developer-mode').exists() else True
    usage=shutil.disk_usage('/var/DaOS')
    return {'system':platform.system(),'release':platform.release(),'machine':platform.machine(),'native':True,
            'power_supported':True,'backlight_supported':bool(device),'backlight_percent':value,
            'timezone':timezone,'clock_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'temperature':temperature,'developer_mode':developer,'data_free_bytes':usage.free,
            'kernel':run(['/sbin/sysctl','-n','kern.bootfile'])}


def active_installer():
    base=Path('/opt/hasslefree');pointer=base/'camera-app-active.json'
    if pointer.is_symlink() or pointer.stat().st_size>4096:raise ValueError('应用版本选择无效')
    value=json.loads(pointer.read_text());version=value.get('version','')
    if not re.fullmatch('camera-experience-[0-9]{3}',version):raise ValueError('应用版本无效')
    installer=base/'releases'/version/'support/camera-app-installer.py'
    if installer.is_symlink() or installer.stat().st_uid!=0 or installer.stat().st_mode&0o022:
        raise ValueError('更新工具身份校验失败')
    return installer,version


def schedule(arguments,log_name):
    UPDATES.mkdir(parents=True,exist_ok=True)
    destination=UPDATES/log_name
    def start():
        with destination.open('ab') as log:
            subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    timer=threading.Timer(2,start);timer.daemon=True;timer.start()
    return {'message':'操作已安排，界面将安全关闭','scheduled':True,'log':str(destination)}


def handle(request):
    if platform.system()!='FreeBSD' or os.geteuid()!=0:raise ValueError('需要原生 DaOS 系统服务')
    operation=request.get('op')
    if operation=='system.status':return status()
    if operation=='system.set-backlight':
        value=request.get('value');device=backlight_device()
        if type(value) is not int or not 1<=value<=100 or not device:raise ValueError('未检测到可控制背光')
        run(['/usr/bin/backlight','-f',device,str(value)])
        actual=int(run(['/usr/bin/backlight','-q','-f',device]))
        return {'message':'背光已读回确认','backlight_percent':actual}
    if operation=='system.set-timezone':
        zone=request.get('timezone','')
        if not isinstance(zone,str) or not re.fullmatch('[A-Za-z0-9_+/-]{1,80}',zone) or '..' in zone.split('/'):
            raise ValueError('时区名称无效')
        base=Path('/usr/share/zoneinfo');source=(base/zone).resolve(strict=True)
        if not source.is_relative_to(base) or not source.is_file():raise ValueError('时区不存在')
        with writable_root():
            run(['/usr/sbin/tzsetup','-s',zone])
        if Path('/etc/localtime').read_bytes()!=source.read_bytes():raise ValueError('时区回读不一致')
        return {'message':'系统时区已设置','timezone':zone}
    if operation=='system.sync-time':
        run(['/usr/sbin/ntpd','-g','-q'],timeout=30)
        return {'message':'网络时间同步完成','clock_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if operation=='system.set-time':
        value=request.get('utc','')
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value):raise ValueError('时间格式无效')
        datetime.datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ')
        run(['/bin/date','-u','-f','%Y-%m-%dT%H:%M:%SZ',value])
        return {'message':'系统时间已设置','clock_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if operation=='system.developer-mode':
        enabled=request.get('enabled')
        if type(enabled) is not bool:raise ValueError('开发模式值无效')
        atomic(CONFIG/'developer-mode',b'enabled\n' if enabled else b'disabled\n')
        # Existing interactive streams must lose access too, not only new OPENs.
        schedule(['/usr/sbin/service','DaOS-usb-adb','restart'],'developer-usb-restart.log')
        return {'message':'开发模式已保存','developer_mode':enabled}
    if operation in ('system.reboot','system.shutdown'):
        return schedule(['/sbin/shutdown','-r' if operation.endswith('reboot') else '-p','now'],'power.log')
    if operation=='system.activate-update':
        path=Path(request.get('path','')).absolute();expected=request.get('sha256','')
        incoming=UPDATES/'incoming'
        if path.parent!=incoming or path.is_symlink() or not re.fullmatch(r'camera-experience-[0-9]{3}\.tar\.gz',path.name):
            raise ValueError('请先把更新包传入设备更新目录')
        if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('更新摘要无效')
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=expected:raise ValueError('更新摘要不匹配')
        installer,_=active_installer();tag=path.name[:-7]
        return schedule(['/usr/local/bin/python3.12',str(installer),str(path),'--sha256',expected,'--tag',tag],tag+'.log')
    if operation=='system.rollback-update':
        installer,tag=active_installer()
        return schedule(['/usr/local/bin/python3.12',str(installer),'--rollback','--tag',tag],tag+'-rollback.log')
    raise ValueError('不支持的系统操作')
