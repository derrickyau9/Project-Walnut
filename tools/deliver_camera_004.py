"""Assemble the 004 software release, SDK, separate driver trial and recovery kit."""
from pathlib import Path
import hashlib,json,shutil,tarfile,zipfile
from tools.camera_experience_bundle import build,source_files
ROOT=Path(__file__).resolve().parents[1]
QA=ROOT/'build/DaOS_Camera_004'
OUT=ROOT/'build/DaOS_Camera_Experience_Release_004'

INSTALL_PS=r'''param([ValidateSet('Check','Install','Rollback')][string]$Action='Check',
 [string]$HostTools='F:\Project HasselFree\build\DaOS_USB_Development\host-tools',
 [string]$Serial='DaOS-USB-ADB-DEVELOPMENT',[int]$Port=5049,[string]$Python='python')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$manifest=Get-Content -LiteralPath 'release.json' -Raw | ConvertFrom-Json
$archive=Join-Path $PSScriptRoot 'camera-experience-004.tar.gz'
$actual=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if($actual -ne $manifest.application_sha256){throw 'Application archive hash mismatch'}
$rootPrefix=[IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')+'\'
$listed=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
Get-Content -LiteralPath 'SHA256SUMS.txt' | ForEach-Object {
 if($_ -notmatch '^([a-f0-9]{64})  (.+)$'){throw 'Invalid checksum line'}
 $hash=$Matches[1];$relative=$Matches[2]
 if([IO.Path]::IsPathRooted($relative) -or $relative -match '[\\:\x00-\x1f]' -or ($relative.Split('/') | Where-Object {$_ -eq '..' -or $_ -eq '.' -or $_ -eq ''})){throw 'Unsafe checksum path'}
 $path=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot $relative))
 if(-not $path.StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase) -or -not $listed.Add($relative)){throw 'Duplicate or redirected checksum path'}
 if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw "Missing release file: $relative"}
 if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash){throw "File hash mismatch: $relative"}
}
if($listed.Count -eq 0){throw 'Empty checksum list'}
Get-ChildItem -LiteralPath $PSScriptRoot -Force -Recurse | ForEach-Object {
 if($_.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Release files cannot be links or junctions'}
 if(-not $_.PSIsContainer){
  $relative=$_.FullName.Substring($rootPrefix.Length).Replace('\','/')
  if($relative -ne 'SHA256SUMS.txt' -and -not $listed.Contains($relative)){throw "Unlisted release file: $relative"}
 }
}
Write-Output '004 checksums verified. No device was changed.'
if($Action -eq 'Check'){exit 0}
if($Serial -notmatch '^DaOS[A-Za-z0-9_.-]{1,60}$' -or $Port -eq 5037 -or $Port -lt 1024 -or $Port -gt 65535){throw 'Use the exact DaOS serial and dedicated server port'}
& $Python -B 'SDK/connect_usb.py' --tools $HostTools --server-port $Port --serial $Serial --no-shell --timeout 60
if($LASTEXITCODE -ne 0){throw 'Authorized DaOS USB is unavailable'}
$adb=Join-Path $HostTools 'platform-tools/adb.exe'
$prefix=@('-P',[string]$Port,'-s',$Serial)
& $adb @prefix shell 'mkdir -p /var/DaOS/updates/incoming'
if($LASTEXITCODE -ne 0){throw 'Enable developer mode for installation'}
& $adb @prefix push 'install_camera_app.py' '/var/DaOS/updates/incoming/camera-app-installer-004.py'
if($LASTEXITCODE -ne 0){throw 'Installer upload failed'}
& $adb @prefix push 'run_camera_update.py' '/var/DaOS/updates/incoming/run-camera-update-004.py'
if($LASTEXITCODE -ne 0){throw 'Operation runner upload failed'}
$operation=[Guid]::NewGuid().ToString('N')
$operationPath='/var/DaOS/updates/camera-experience-004-operation-'+$operation+'.json'
if($Action -eq 'Install'){
 & $adb @prefix push $archive '/var/DaOS/updates/incoming/camera-experience-004.tar.gz'
 if($LASTEXITCODE -ne 0){throw 'Archive upload failed'}
}
$command="/usr/sbin/daemon -f -o /var/DaOS/updates/camera-experience-004-$operation.log /usr/local/bin/python3.12 -B /var/DaOS/updates/incoming/run-camera-update-004.py --action $Action --operation $operation --sha256 $actual"
$startCode=1
try { & $adb @prefix shell $command; $startCode=$LASTEXITCODE } catch {}
if($startCode -ne 0){Write-Output 'Start acknowledgement interrupted; inspecting this same operation without retrying it.'}
Write-Output 'Installer started. USB may reconnect; waiting for the durable result.'
$deadline=(Get-Date).AddSeconds(120)
do{
 Start-Sleep -Milliseconds 1000
 try {$raw=& $adb @prefix shell ('cat '+$operationPath) 2>$null} catch {continue}
 if($LASTEXITCODE -eq 0){
  try{$receipt=($raw -join "`n") | ConvertFrom-Json}catch{continue}
  if($receipt.operation -ne $operation -or $receipt.action -ne $Action -or $receipt.application_sha256 -ne $actual){throw 'Operation receipt identity mismatch'}
  if($receipt.status -eq 'failed'){throw ('This update failed: '+$receipt.error+'; inspect its operation log. No retry was issued.')}
  if($receipt.status -ne 'complete' -or $receipt.exit_code -ne 0){continue}
  if(($Action -eq 'Install' -and $receipt.deployment_status -eq 'ready') -or ($Action -eq 'Rollback' -and $receipt.deployment_status -eq 'rolled-back')){
   Write-Output ("Device result: "+$receipt.deployment_status+'; operation '+$operation);exit 0
  }
 }
}while((Get-Date) -lt $deadline)
throw ("No final operation receipt. Do not repeat installation or capture. Reconnect and inspect "+$operationPath+' and its matching operation log.')
'''

RUNNER_PY=r'''#!/usr/local/bin/python3.12
"""One invocation, one durable receipt; never infer success from an older run."""
import argparse,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path

BASE=Path('/var/DaOS/updates')
VERSION='camera-experience-004'

def save(path,value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(value,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    if os.name!='nt':
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)

def run_installer(command,base,runner):
    # A second host must not roll back while the first host is still installing.
    with (base/'camera-experience-004-operation.lock').open('a+b') as lock:
        if os.name=='nt':
            import msvcrt
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=runner(command,check=False)
        if result.returncode!=0:return result,None
        raw=(base/VERSION/'deployment.json').read_bytes()
        return result,raw

def execute(action,operation,application_sha256,*,base=BASE,runner=subprocess.run):
    if action not in ('Install','Rollback') or not re.fullmatch('[a-f0-9]{32}',operation) or not re.fullmatch('[a-f0-9]{64}',application_sha256):raise ValueError('Invalid operation identity')
    path=base/(VERSION+'-operation-'+operation+'.json')
    if path.exists() or path.is_symlink():raise ValueError('Operation identity already exists; do not reuse it')
    record={'format':'DaOS-camera-update-operation-v1','operation':operation,'action':action,'application_sha256':application_sha256,'status':'running','started_at':time.time()}
    save(path,record)
    try:
        installer=base/'incoming/camera-app-installer-004.py'
        command=[sys.executable,'-B',str(installer)]
        if action=='Install':command += [str(base/'incoming/camera-experience-004.tar.gz'),'--sha256',application_sha256]
        else:command += ['--rollback']
        command += ['--tag',VERSION]
        result,raw=run_installer(command,base,runner)
        record['exit_code']=result.returncode
        if result.returncode!=0:raise ValueError('Installer exited '+str(result.returncode))
        receipt=json.loads(raw)
        expected='ready' if action=='Install' else 'rolled-back'
        if receipt.get('format')!='DaOS-camera-app-v1' or receipt.get('version')!=VERSION or receipt.get('status')!=expected:
            raise ValueError('Installer did not produce the expected final deployment receipt')
        record.update(status='complete',deployment_status=expected,deployment_sha256=hashlib.sha256(raw).hexdigest())
    except BaseException as error:
        record.update(status='failed',error=str(error))
    record['completed_at']=time.time();save(path,record)
    return record

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--action',choices=['Install','Rollback'],required=True);parser.add_argument('--operation',required=True);parser.add_argument('--sha256',required=True);args=parser.parse_args()
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD root is required')
    record=execute(args.action,args.operation,args.sha256);print(json.dumps(record));return 0 if record['status']=='complete' else 1
if __name__=='__main__':raise SystemExit(main())
'''

INSTALL_TEXT=r'''DaOS Camera 004 · 软件更新包

本包没有安装到树莓派，也没有改写内存卡。应用和Wi-Fi驱动分别发布。
适用：现有003、FreeBSD15.1 ARM64、Qt5.15、树莓派4、800×480 DSI。
纯无衬线 Noto Sans CJK SC Regular/Medium；半屏中文键盘保留。

1. 校验（无需连接设备）
   powershell -ExecutionPolicy Bypass -File .\Install.ps1 -Action Check
   默认只校验。SHA256SUMS.txt覆盖包内文件，release.json标明应用摘要。

2. 将来连接USB后安装应用
   机身开发模式需要开启；使用现有已授权主机密钥，不要替换keys目录。
   powershell -ExecutionPolicy Bypass -File .\Install.ps1 -Action Install
   可用-HostTools指定现有授权的host-tools；-Python指定Python3.12+。
   只选DaOS精确序列号、独立5049端口，不操作普通5037服务器。
   完整版本目录、匹配ARM64 hf-camerad/hf-imager和系统助手全部准备后，
   才原子切换应用选择器。相机准备失败会恢复之前选择器；用户RAW不删除。
   GUI/系统/USB服务会重启，内核和固件保持原样。重复安装同版本被拒绝。

3. 应用回滚
   powershell -ExecutionPolicy Bypass -File .\Install.ps1 -Action Rollback
   使用设备/var/DaOS/updates/camera-experience-004里的原始备份。
   recovery保留003原包；恢复既有设备应优先回滚，不能覆盖原版本目录。
   每次操作使用独立GUID收据；USB重连后只确认本次执行，旧ready/rolled-back不算成功。
   003原包仅供离线救援资料，不是当前设备备份；禁止直接覆盖已有003版本目录。
   若应用安装尚未建立004备份或卡已损坏，回滚命令会拒绝，需从已知稳定介质恢复。

4. 软件入口
   Q格：点击值或±、拖动/拨轮；C1-C3仅保存已确认实际参数；Fn按住/松开恢复。
   回放→照片库：全库墙、筛选、范围/全选、批量、双图查焦、复制导入、回收站。
   设置→图像工坊：从图库选择素材，显影/分析/多图计算；输出是新派生文件。
   设置→后台任务：逐项状态、暂停/继续/取消；重启不自动重新曝光。
   设置→系统与维护：备份恢复、版本/更新、时间/时区、休眠、开发、联机。
   配置导入不应用拍摄参数；备份排除密钥、设备身份、租约和拍摄请求状态。

5. USB SDK与手机
   SDK/README.txt提供受控拍摄、查询原UUID、SHA256断点下载的Python示例。
   机身开启联机后才可取得远程控制；本机触摸/快门立即接管。
   手机：机身联机页开启局域网，核对设备证书并用六位码配对；默认关闭。
   开发Shell与日常相机协议分开，关闭开发模式后Shell/ADB文件系统服务拒绝。

6. Wi-Fi候选（未在树莓派验证，不表示Wi-Fi已修好）
   单独看driver/INSTALL.txt；切勿安装旧撤回001/002候选。
   004已重编MMCCAM内核、匹配Wi-Fi/USB/DSI模块，含异步CAM和SDIO F0超时修复。
   稳定内核仍默认。必须显式一次性试启动、完成双频/WPA2/DHCP/重连/SD并发
   验收，工具才允许提交默认；当前没有生成通过的实机验收记录。

7. 验证范围
   validation/含桌面测试、FreeBSD原生显影与Unix/stdio联机证据及性能记录。
   24MP DNG已独立解码核验6064×4040 JPEG/TIFF16，原始RAW SHA不变。
   算法使用合成/现有素材，输出未经过传感器色彩标定。
   ARM64程序已构建/检查；Pi触控、物理USB、Wi-Fi、60fps目标仍须实机验收。
   sources-004.tar.gz含本轮源码/构建工具；第三方版本、摘要、许可证随包。
'''
SDK_TEXT='''DaOS Camera Link · Python3.12+ 标准库SDK

当前电脑：SDK/camera_usb.py默认复用F:/Project HasselFree/build/DaOS_USB_Development/host-tools。
通过--tools指定其他已授权host-tools。私钥不随本包分发；首次新电脑需另行授权。

  python camera_usb.py state
  python camera_usb.py photos
  python camera_usb.py capture --request-id <预先保存的UUID>
  python camera_usb.py query --request-id <同一UUID>
  python camera_usb.py download --photo <photo_uuid> --output F:/Photos/new.dng

中断后重复download同一目标，使用.part和.transfer.json继续并校验SHA256。
不会覆盖既有文件。拍摄请求错误时只能query原UUID，禁止生成新UUID自动重拍。
机身设置→系统与维护→联机开启控制；本机操作始终优先。

Python（在SDK目录运行）：
  from daos.camera_link import CameraClient, USBTransport
  import uuid, json
  camera=CameraClient(USBTransport(r'F:/.../platform-tools/adb.exe'))
  camera.acquire()
  request=str(uuid.uuid4())
  # 必须先持久化request；下面发送失败也保留它
  with open('request.json','x') as f: json.dump({'request_id':request},f)
  camera.capture(request)
  camera.query(request)
  # 参数的确认状态由camera.state()['settings_view']读取
  camera.configure(requested_exposure_us=10000,requested_analogue_gain=2.0)
  for photo in camera.iter_photos({'rating_min':3}): print(photo['photo_uuid'])
  camera.tasks(); camera.inspect_task('<job_uuid>')
  camera.control_task('<job_uuid>','pause') # resume/cancel同理，需控制权
  camera.release(); camera.close()

USBTransport使用固定stdio桥接，不启动第二个相机服务。SDK控制消息<=64KiB，
分页响应<=2MiB，二进制块256KiB。传输记录和资产pin分离；恢复重新验证源SHA。

局域网：机身显式启用HTTPS，配对码5分钟过期，令牌1小时过期；会话需30秒心跳。
浏览器页面由机身提供。TLS客户端必须提供从机身核实过的证书：
  from daos.camera_link import HTTPTransport
  camera=CameraClient(HTTPTransport('https://192.168.1.20:8443','123456',cafile='lan-cert.pem'))
  camera.acquire(); camera.heartbeat()
证书文件在设备/var/DaOS/ui/link/lan-cert.pem。不要关闭TLS验证。
'''
DRIVER_TEXT='''DaOS Wi-Fi 004 · 独立试验包（当前未在树莓派运行）

本目录只有候选，不会由应用安装器自动安装。已修源码/编译不等于Wi-Fi可用。
旧001/002包已撤回，禁止使用。稳定/boot/kernel保留为默认。
在已恢复稳定的设备上、先安装004应用，将本目录上传到/var/DaOS/updates/incoming。
下面每一步都由操作者显式执行，本交付没有执行它们：

  python3.12 deploy_wifi_004.py stage --archive wifi-update-004.tar.gz
  # 确认此前已观察到这台板nextboot_enable的一次性标记自动清回NO；地区先存US
  python3.12 deploy_wifi_004.py trial --fallback-observed
  # 之后自行重启一次；默认内核配置仍稳定。失败复位会回到原默认内核。

试启动后在机身连接WPA2网络、启用联机、检查USB/DSI/触摸/相机正常，再运行：
  python3.12 verify_wifi_004.py --observed-usb-dsi-touch-camera
  # 该检查会重新关联现有网络，在SD数据分区并发写/核对64MiB、拍两张模拟测试片。
  # 它保留拍摄UUID，不自动重拍。现场需能扫描到2.4GHz及5GHz AP。
  # 未通过/有错误/缺少项目/不同启动/超30分钟的记录都不能提交默认。
  python3.12 deploy_wifi_004.py commit --validation /var/DaOS/updates/wifi-004/validation.json

恢复：从原稳定内核启动，执行python3.12 deploy_wifi_004.py rollback，再自行重启。
工具不自动重启、不擦卡；rollback恢复配置和原权限，保留候选目录供诊断。
本包含同源码内核和全部模块、设备树、固件、补丁、对应驱动源码及许可证。
外部驱动已知上游2.4GHz认证问题仍须验证，不能宣称本候选已解决所有无线问题。
'''

def copy(source,relative):
    destination=OUT/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
def text(relative,value):
    path=OUT/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8')
def main():
    OUT.mkdir(exist_ok=False)
    receipt=build(OUT/'camera-experience-004.tar.gz','camera-experience-004')
    copy(ROOT/'system/freebsd/raspberrypi/deploy_camera_app.py','install_camera_app.py')
    text('Install.ps1',INSTALL_PS);text('run_camera_update.py',RUNNER_PY);text('INSTALL.txt',INSTALL_TEXT);text('SDK/README.txt',SDK_TEXT)
    for action in ('Check','Install','Rollback'):
        text(action+'.cmd','@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1" -Action '+action+'\r\n')
    for path in (ROOT/'daos/camera_link').glob('*.py'):copy(path,'SDK/daos/camera_link/'+path.name)
    text('SDK/daos/__init__.py','"""DaOS SDK namespace."""\n')
    copy(ROOT/'tools/camera_usb.py','SDK/camera_usb.py');copy(ROOT/'system/adb/connect_usb.py','SDK/connect_usb.py')
    for name in ('__init__.py','auth.py'):copy(ROOT/'system/adb/daemon'/name,'SDK/daemon/'+name)
    for name in ('hf-camerad','hf-imager'):copy(QA/'arm64'/name,'native/arm64/'+name)
    copy(QA/'arm64/native-build-evidence.json','validation/native-arm64-build.json')
    copy(QA/'wifi-update-004.tar.gz','driver/wifi-update-004.tar.gz')
    for name in ('deploy_wifi_004.py','verify_wifi_004.py'):copy(ROOT/'system/freebsd/raspberrypi'/name,'driver/'+name)
    text('driver/INSTALL.txt',DRIVER_TEXT)
    for name in ('camera-experience-003.tar.gz','camera-experience-003.tar.gz.json','install_camera_app.py'):
        copy(ROOT/'build/DaOS_Camera_Experience_Release_003'/name,'recovery/003/'+name)
    text('recovery/README.txt','''恢复说明

设备可连接且004安装已经建立备份时，从包根目录执行Rollback.cmd。
它只调用本设备/var/DaOS/updates/camera-experience-004里的原始备份，
恢复旧入口、版本选择器及服务；保留RAW、图库和完整版本目录。
每次运行有独立operation GUID收据，旧rolled-back状态不算本次完成。

003/中的归档和旧安装器是离线救援资料，不是这台设备的原始备份。
不要把003包直接覆盖到现有003版本目录，也不要拿它替代丢失的备份。
若卡/文件系统已损坏或USB无法启动，需要从已知稳定介质恢复系统后
再恢复数据。没有整卡镜像和用户照片备份时，本包不声称能恢复这些内容。
Wi-Fi内核候选的恢复步骤独立见driver/INSTALL.txt；应用回滚不改内核。
''')
    for base,label in ((ROOT/'camera/processing','processing'),(ROOT/'ui/on-device/fonts','fonts'),(ROOT/'ui/on-device/ime_data','ime')):
        for path in sorted(base.rglob('*')):
            if path.is_file() and not path.is_symlink() and ('license' in path.name.lower() or 'copyright' in path.name.lower() or path.name in ('OFL.txt','SOURCE.json','SOURCES.json','README.md')):
                copy(path,'licenses/'+label+'/'+path.relative_to(base).as_posix())
    for path in sorted(QA.glob('*.log')):copy(path,'validation/'+path.name)
    for path in sorted(QA.glob('*report*.json')):copy(path,'validation/'+path.name)
    if (QA/'004-validation-report.txt').is_file():copy(QA/'004-validation-report.txt','VALIDATION.txt')
    if (QA/'library-bounds.json').is_file():copy(QA/'library-bounds.json','validation/library-bounds.json')
    for path in sorted((QA/'processing').glob('*.json')):copy(path,'validation/processing/'+path.name)
    for path in sorted(QA.glob('*.png')):copy(path,'screenshots/'+path.name)
    for path in sorted((QA/'processing').glob('*800x480.png')):copy(path,'screenshots/'+path.name)
    if (QA/'final-state/ui-performance.json').exists():copy(QA/'final-state/ui-performance.json','validation/performance-desktop.json')
    # Complete project-controlled source plus pinned third-party notices. OpenCV
    # upstream source is fetched by the exact digest in processing/SOURCES.json.
    # Preserve original repository locations: remapping support payload names
    # would make camera_experience_bundle.py unable to find its own inputs.
    inputs={path.relative_to(ROOT).as_posix():path for path in source_files().values()}
    from tools.freebsd_cross_build import cross_source_inputs
    for name in cross_source_inputs(ROOT):inputs[name]=ROOT/name
    for name in ('DaOS-system-service.py','DaOS-system-ops.py','DaOS-camera-link.py','DaOS-wifi-load.py','DaOS-camera-app.py'):
        path=ROOT/'system/freebsd/image/overlay/usr/local/libexec'/name
        inputs[path.relative_to(ROOT).as_posix()]=path
    for name in ('DaOS-usb-adb-session.py',):
        path=ROOT/'system/adb/integration/files/usr/local/libexec'/name
        inputs[path.relative_to(ROOT).as_posix()]=path
    inputs['system/adb/connect_usb.py']=ROOT/'system/adb/connect_usb.py'
    for tree in ('camera/native','camera/processing','tools','tests','system/freebsd/raspberrypi','system/adb/daemon'):
        for path in (ROOT/tree).rglob('*'):
            if path.is_file() and not path.is_symlink() and not {'__pycache__','evidence'}.intersection(path.parts) and (path.suffix in ('.py','.cpp','.hpp','.c','.h','.inc','.json','.txt','.md','.patch','.dts','.cmake') or path.name=='CMakeLists.txt'):
                inputs[path.relative_to(ROOT).as_posix()]=path
    with tarfile.open(OUT/'sources-004.tar.gz','w:gz') as archive:
        for name,path in sorted(inputs.items()):archive.add(path,arcname=name,recursive=False)
    report={'format':'DaOS-camera-release-v1','version':'camera-experience-004','application_sha256':receipt['archive_sha256'],
            'application_bytes':receipt['archive_bytes'],'physical_device_modified':False,'physical_pi_validated':False,
            'wifi_status':'candidate-compiled-not-physically-validated','native_architecture':'FreeBSD15.1 ARM64',
            'ui':'800x480; opaque monochrome; Noto Sans CJK SC Regular/Medium; half-screen Chinese keyboard',
            'hardware_camera':'HF-Sim; real sensor/lens/AF/video capability unavailable'}
    text('release.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    sums=[]
    for path in sorted(OUT.rglob('*')):
        if path.is_file():sums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.relative_to(OUT).as_posix())
    text('SHA256SUMS.txt','\n'.join(sums)+'\n')
    destination=ROOT/'build/DaOS-Camera-Experience-004.zip'
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(OUT.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(OUT))
    print(json.dumps({'directory':str(OUT),'zip':str(destination),'zip_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'zip_bytes':destination.stat().st_size,'files':len(sums)+1}))
if __name__=='__main__':main()
