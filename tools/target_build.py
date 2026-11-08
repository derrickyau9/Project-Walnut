"""Run the pinned generic ARM64 Qt image recipe in a new, private work directory.

The component helpers own package authentication, image construction and guest
verification. This coordinator uses argv-only subprocesses, bounded stage logs
and process groups, and never resumes or retries a partly completed build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
if __package__ in (None,''):
    sys.path.insert(0,str(ROOT))
DEFAULT_PROFILE=ROOT/'system/targets/qemu-arm64-qt5.json'
CONTENT_ROOTS={'camera','daos','docs','firmware','fpga','hardware','licenses','mounts','simulation','system','tests','tools','ui','.github'}
REQUIRED_TOOLS=['aarch64-linux-gnu-gcc','aarch64-linux-gnu-ld','clang++','qemu-aarch64',
                'qemu-system-aarch64','qemu-img','mke2fs','debugfs','e2fsck','dpkg-deb','dpkg','gpgv']
MAX_STAGE_LOG=16*1024**2


def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''): result.update(block)
    return result.hexdigest()


def recipe_sources(value,profile_path):
    """Record component/application code as well as locks, excluding QA output."""
    paths={Path(profile_path).resolve()}
    paths.update(source_path(value[name]) for name in ('rootfs_profile','rootfs_lock','kernel_lock'))
    for pattern in ('rootfs_*.py','image_*.py','arm64_*.py','target_build.py','qemu_target.py'):
        paths.update((ROOT/'tools').glob(pattern))
    paths.update([ROOT/'system/arm64-image/init.c',ROOT/'tests/ui/arm64_smoke.py',ROOT/'tests/ui/offline_smoke.py'])
    paths.add(ROOT/'system/arm64/toolchain-lock.json')
    paths.update((ROOT/'system/arm64-rootfs/metadata').glob('*'))
    from tools.arm64_build import source_inputs
    paths.update(source_path(name) for name in source_inputs(ROOT))
    for component in ('camera','daos','simulation','ui/on-device'):
        for path in (ROOT/component).rglob('*'):
            if (path.is_file() and path.suffix in {'.py','.qml','.json','.js','.html','.css','.cpp','.hpp','.h','.c','.md'}
                    and '__pycache__' not in path.parts and not any(part.startswith('evidence') for part in path.relative_to(ROOT).parts)):
                paths.add(path)
    return {path.relative_to(ROOT).as_posix():digest(path) for path in sorted(paths)}


def source_path(value,root=None):
    root=ROOT if root is None else root
    if not isinstance(value,str) or '\\' in value:
        raise ValueError('recipe source paths must be canonical relative paths')
    relative=PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix()!=value or '..' in relative.parts or not relative.parts or ':' in value:
        raise ValueError('unsafe recipe source path')
    result=(Path(root)/value).resolve()
    if not result.is_relative_to(Path(root).resolve()): raise ValueError('recipe source path escaped source tree')
    return result


def profile(path=DEFAULT_PROFILE):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT): raise ValueError('target recipe must be a recorded source-tree input')
    if path.stat().st_size>65536: raise ValueError('target recipe exceeds bound')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('duplicate target recipe field')
            result[key]=value
        return result
    value=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=unique)
    fields={'format','id','target','simulated','physical_board','rootfs_profile','rootfs_lock','kernel_lock','phases','stage_timeout_seconds','scope'}
    if (not isinstance(value,dict) or set(value)!=fields or value['format']!='hf-target-recipe-v1'
            or value['id']!='qemu-arm64-qt5' or value['target']!='generic-virt-aarch64-linux-qt5'
            or value['simulated'] is not True or value['physical_board'] is not False
            or value['phases']!=['first','resume']):
        raise ValueError('unsupported target recipe; this is not a board profile')
    for name in ('rootfs_profile','rootfs_lock','kernel_lock'): source_path(value[name])
    budgets=value['stage_timeout_seconds']
    if not isinstance(budgets,dict) or set(budgets)!={'native','kernel','rootfs-inputs','rootfs-stage','image','first','resume'}:
        raise ValueError('invalid stage budgets')
    if any(type(n) is not int or not 1<=n<=900 for n in budgets.values()): raise ValueError('stage budgets must be 1..900 seconds')
    return value


def work_path(path,root=ROOT):
    original=Path(path).expanduser().absolute()
    if original.exists() or original.is_symlink(): raise ValueError('work directory must be new; inspect existing evidence instead of replaying')
    result=original.resolve()
    root=Path(root).resolve()
    if result==root or result.is_relative_to(root) and result.relative_to(root).parts[0] in CONTENT_ROOTS:
        raise ValueError('build outputs cannot be written inside project source roots')
    for ancestor in result.parents:
        if (ancestor/'journal.sqlite3').exists(): raise ValueError('work directory cannot nest in camera data')
        stage=ancestor/'stage-manifest.json'
        if stage.is_file() and stage.stat().st_size<=16*1024**2:
            candidate=json.loads(stage.read_text(encoding='utf-8'))
            if isinstance(candidate,dict) and candidate.get('format')=='hf-arm64-qt-rootfs-v1':
                raise ValueError('work directory cannot nest in an immutable staged rootfs')
        manifest=ancestor/'manifest.json'
        if manifest.is_file() and manifest.stat().st_size<=1024*1024:
            candidate=json.loads(manifest.read_text(encoding='utf-8'))
            if isinstance(candidate,dict) and candidate.get('format') in {'hf-sim-host-bundle-v1','hf-static-arm64-sim-package-v1'}:
                raise ValueError('work directory cannot nest in an immutable release')
    if result.is_relative_to(root) and result.relative_to(root).parts[0]!='build':
        raise ValueError('workspace work directory must be beneath build; protected and unrelated files are excluded')
    return result


def image_path(work):
    """Keep regular disk images within the image helper's workspace guard.

    POSIX staging and run state stay on persistent Linux storage; regular ext4
    files do not need host POSIX metadata and can safely live in workspace build.
    """
    identity=hashlib.sha256(str(Path(work).resolve()).encode('utf-8')).hexdigest()[:24]
    return ROOT/'build'/('target-'+identity)


def command_plan(value,work,*,native_dir=None,kernel_dir=None,inputs_dir=None,offline=False):
    work=Path(work).expanduser().resolve(); native=Path(native_dir).expanduser().resolve() if native_dir else work/'native'
    kernel=Path(kernel_dir).expanduser().resolve() if kernel_dir else work/'kernel'
    inputs=Path(inputs_dir).expanduser().resolve() if inputs_dir else work/'inputs'
    if offline and (native_dir is None or kernel_dir is None or inputs_dir is None):
        raise ValueError('offline reproduction requires explicit verified native, kernel and package input caches')
    image=image_path(work)
    python=sys.executable; result=[]
    def add(name,args): result.append({'id':name,'command':[python,'-B','-m',*map(str,args)],'timeout':value['stage_timeout_seconds'][name]})
    if native_dir is None: add('native',['tools.arm64_build','--build-dir',native,'--tools-dir',work/'toolchain','--jobs','2'])
    if kernel_dir is None: add('kernel',['tools.qemu_target','fetch-kernel','--dest',kernel,'--lock',source_path(value['kernel_lock'])])
    add('rootfs-inputs',['tools.rootfs_build','prepare','--profile',source_path(value['rootfs_profile']),
                        '--inputs-dir',inputs,'--lock',source_path(value['rootfs_lock']),*(['--offline'] if offline else [])])
    add('rootfs-stage',['tools.rootfs_build','stage','--lock',inputs/'package-lock.json','--inputs-dir',inputs,'--output',work/'rootfs-tree'])
    add('image',['tools.image_build','build','--rootfs',work/'rootfs-tree/rootfs','--rootfs-manifest',work/'rootfs-tree/stage-manifest.json',
                 '--kernel-dir',kernel,'--native-dir',native,'--build-dir',image])
    for phase in value['phases']:
        # The inner boot owns a shorter deadline and shuts down its own guest.
        add(phase,['tools.image_boot','boot','--build-dir',image,'--phase',phase,
                   '--timeout',str(max(1,value['stage_timeout_seconds'][phase]-30))])
    return result


def preflight(value,work):
    found={name:shutil.which(name) for name in REQUIRED_TOOLS}
    missing=[name for name,path in found.items() if not path]
    absent=[value[name] for name in ('rootfs_profile','rootfs_lock','kernel_lock') if not source_path(value[name]).is_file()]
    parent=Path(work).expanduser().resolve().parent
    while not parent.exists(): parent=parent.parent
    filesystem=None
    if sys.platform=='linux' and shutil.which('findmnt'):
        probe=subprocess.run(['findmnt','-T',str(parent),'-n','-o','FSTYPE'],capture_output=True,text=True,timeout=5)
        if probe.returncode==0: filesystem=probe.stdout.strip()
    image_parent=ROOT/'build'
    while not image_parent.exists(): image_parent=image_parent.parent
    return {'format':'hf-target-preflight-v1','linux':sys.platform=='linux',
            'nonroot':getattr(os,'geteuid',lambda:1)()!=0,'missing_tools':missing,
            'missing_source_inputs':absent,'tools':found,'free_bytes':shutil.disk_usage(parent).free,
            'image_free_bytes':shutil.disk_usage(image_parent).free,
            'work_filesystem':filesystem,'volatile_work_filesystem':filesystem in {'tmpfs','ramfs'},
            'credentials_required':False,'scope':'generic virtual ARM64 target only'}


def verify_boot(path,phase,*,base_hash,previous=None):
    """Check actual guest/ABI/persistence proof, not merely a zero exit code."""
    path=Path(path)
    if not path.is_file() or path.stat().st_size>2*1024**2: raise ValueError('missing or oversized guest boot evidence')
    value=json.loads(path.read_text(encoding='utf-8'))
    def checksum(value):
        return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    if (not checksum(base_hash) or not isinstance(value,dict) or value.get('format')!='hf-arm64-image-boot-v1'
            or value.get('passed') is not True or value.get('phase')!=phase
            or value.get('rootfs_unchanged') is not True
            or value.get('rootfs_sha256_before')!=base_hash or value.get('rootfs_sha256_after')!=base_hash
            or not checksum(value.get('data_sha256_before')) or not checksum(value.get('data_sha256_after'))):
        raise ValueError('guest boot or immutable-root evidence is inconsistent')
    guest=value.get('guest_report')
    if (not isinstance(guest,dict) or guest.get('passed') is not True or guest.get('execution_mode')!='qemu-system'
            or guest.get('phase')!=phase or guest.get('simulated') is not True
            or guest.get('effective_uid')!=1000 or guest.get('machine') not in ('aarch64','arm64')
            or type(guest.get('journal_rows')) is not int or guest.get('journal_rows')!=1 or guest.get('native_process_closed') is not True
            or guest.get('physical_display_validated') is not False or guest.get('sensor_acquisition_validated') is not False
            or guest.get('capture_status')!='complete'):
        raise ValueError('actual nonroot ARM64 Qt guest execution was not established')
    for name in ('python_elf','native_authority'):
        if not isinstance(guest.get(name),dict) or guest[name].get('elf_machine')!=183 or not checksum(guest[name].get('sha256')):
            raise ValueError('guest interpreter/authority architecture proof missing')
    qt=guest.get('qt_extensions')
    if (not isinstance(qt,dict) or not {'QtCore','QtGui','QtQml','QtQuick','QtTest'}.issubset(qt)
            or any(not isinstance(qt[name],dict) or qt[name].get('elf_machine')!=183 or not checksum(qt[name].get('sha256')) for name in ('QtCore','QtGui','QtQml','QtQuick','QtTest'))):
        raise ValueError('native ARM64 Qt extension proof missing')
    rid=guest.get('request_id')
    try:
        if not isinstance(rid,str) or str(uuid.UUID(rid))!=rid or value.get('request_id')!=rid: raise ValueError()
    except (ValueError,AttributeError): raise ValueError('guest request identity invalid') from None
    if (guest.get('capture_id')!=rid or not isinstance(guest.get('frame_id'),str) or not guest['frame_id']
            or guest.get('dng_sha256')!=guest.get('export_sha256') or not checksum(guest.get('dng_sha256'))
            or type(guest.get('dng_bytes')) is not int or not 0<guest['dng_bytes']<=50*1024**2
            or guest.get('export_bytes')!=guest['dng_bytes']):
        raise ValueError('guest export is inconsistent')
    if previous and (value.get('persistent_same_request_id') is not True or rid!=previous['request_id']
                     or value.get('data_sha256_before')!=previous.get('data_sha256_after')
                     or guest.get('dng_sha256')!=previous['guest_report'].get('dng_sha256')
                     or guest.get('frame_id')!=previous['guest_report'].get('frame_id')
                     or guest.get('restart_same_id_without_recapture') is not True):
        raise ValueError('persistent second boot did not reconcile the original capture')
    return value


def _stop(process):
    # The leader can exit before a descendant. Always close the process group
    # that we created, including after successful/fast component termination.
    try: os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try: os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    process.wait(timeout=5)


def run_stage(stage,log,cwd=ROOT):
    """Bound only the process group created here; there is no shell or retry."""
    if sys.platform!='linux': raise RuntimeError('target stages require a Linux host process')
    env=dict(os.environ,PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    for key in ('PYTHONPATH','PYTHONHOME','LD_LIBRARY_PATH','QT_PLUGIN_PATH','QML2_IMPORT_PATH','QT_QPA_PLATFORM','QT_QUICK_BACKEND','QEMU_LD_PREFIX'):
        env.pop(key,None)
    started=time.monotonic(); reason=None
    with Path(log).open('xb') as handle:
        child=subprocess.Popen(stage['command'],cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                               stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while child.poll() is None:
                if time.monotonic()-started>stage['timeout']: reason='stage deadline expired'; break
                if Path(log).stat().st_size>MAX_STAGE_LOG: reason='stage log exceeded bound'; break
                time.sleep(.1)
        finally:
            _stop(child)
        if Path(log).stat().st_size>MAX_STAGE_LOG: reason='stage log exceeded bound'
    result={'id':stage['id'],'command':stage['command'],'returncode':child.returncode,
            'elapsed_seconds':round(time.monotonic()-started,3),'log_sha256':digest(log)}
    if reason or child.returncode!=0:
        raise RuntimeError(stage['id']+': '+(reason or 'component exited '+str(child.returncode))+'; inspect '+Path(log).name)
    return result


def build(value,path,*,profile_path=DEFAULT_PROFILE,native_dir=None,kernel_dir=None,inputs_dir=None,offline=False):
    if value!=profile(profile_path): raise ValueError('in-memory target recipe differs from its recorded source')
    work=work_path(path)
    native_dir=Path(native_dir).expanduser().resolve() if native_dir is not None else None
    kernel_dir=Path(kernel_dir).expanduser().resolve() if kernel_dir is not None else None
    inputs_dir=Path(inputs_dir).expanduser().resolve() if inputs_dir is not None else None
    check=preflight(value,work)
    if not check['linux'] or not check['nonroot'] or check['missing_tools'] or check['missing_source_inputs']:
        raise RuntimeError('target preflight incomplete: '+json.dumps(check))
    if check['volatile_work_filesystem']:
        raise ValueError('use a persistent Linux work directory; this host temporary filesystem can disappear between WSL invocations')
    if check['work_filesystem'] in {'9p','drvfs','ntfs','fuseblk'}:
        raise ValueError('use a persistent Linux-native filesystem for rootfs mode/symlink semantics')
    if check['free_bytes']<2*1024**3 or check['image_free_bytes']<3*1024**3:
        raise ValueError('target needs at least 2 GiB free for staging and 3 GiB for regular image files')
    plan=command_plan(value,work,native_dir=native_dir,kernel_dir=kernel_dir,inputs_dir=inputs_dir,offline=offline)
    image=image_path(work)
    if image.exists() or image.is_symlink(): raise ValueError('target image destination must be new; inspect existing evidence instead of replaying')
    if native_dir:
        from tools.arm64_build import source_inputs
        from tools.arm64_elf import inspect_arm64_elf
        evidence=json.loads((Path(native_dir)/'arm64-build-evidence.json').read_text())
        if (evidence.get('format')!='hf-arm64-cross-build-v1' or evidence.get('target_triple')!='aarch64-linux-gnu'
                or evidence.get('tests_passed') is not True or evidence.get('native_input_sha256')!=source_inputs(ROOT)):
            raise ValueError('reused native input must have complete matching tested source evidence')
        for name in ('hf-camerad','hf-native-worker','hf-core-tests','hf-daemon-tests'):
            file=Path(native_dir)/name; inspect_arm64_elf(file)
            if digest(file)!=evidence.get('artifacts',{}).get(name,{}).get('sha256'): raise ValueError('reused native artifact changed')
    if kernel_dir:
        directory=Path(kernel_dir).resolve()
        evidence=json.loads((directory/'kernel-manifest.json').read_text())
        selected=json.loads(source_path(value['kernel_lock']).read_text())
        kernel_image=(directory/evidence['kernel_relative_path']).resolve()
        if (not kernel_image.is_relative_to(directory) or evidence.get('sha256')!=selected.get('sha256')
                or evidence.get('kernel_sha256')!=selected.get('kernel_sha256')
                or digest(kernel_image)!=selected['kernel_sha256'] or digest(directory/'kernel.deb')!=selected['sha256']):
            raise ValueError('reused kernel cache differs from the pinned target profile')
    for reused in (native_dir,kernel_dir,inputs_dir):
        if reused:
            directory=Path(reused).resolve()
            if any(directory==target or directory in target.parents or target in directory.parents for target in (work,image)):
                raise ValueError('new build output and reused input caches must be separate')
    work.mkdir(parents=True)
    source_hashes=recipe_sources(value,profile_path)
    state={'format':'hf-target-build-run-v1','run_id':str(uuid.uuid4()),'profile':value['id'],'simulated':True,
           'physical_board':False,'source_inputs':source_hashes,'image_directory':str(image),'status':'running','steps':[]}
    record=work/'run-state.json'
    def persist():
        temporary=work/'run-state.json.tmp'
        with temporary.open('w',encoding='utf-8') as handle:
            handle.write(json.dumps(state,indent=2)+'\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary,record)
        directory=os.open(work,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    persist()
    try:
        first=None; base_hash=None
        for stage in plan:
            state['active_step']=stage['id']; persist()
            result=run_stage(stage,work/(stage['id']+'.log'))
            if stage['id']=='image':
                base_hash=digest(image/'rootfs.ext4')
                report=json.loads((image/'build-evidence.json').read_text())
                expected={name:checksum for name,checksum in report['application_input_sha256'].items() if not name.startswith('native/')}
                if any(source_hashes.get(name)!=checksum for name,checksum in expected.items()):
                    raise RuntimeError('deployed application differs from recorded recipe inputs')
            if stage['id'] in ('first','resume'):
                path=image/'boots'/stage['id']/'boot-evidence.json'
                boot=verify_boot(path,stage['id'],base_hash=base_hash,previous=first if stage['id']=='resume' else None)
                if stage['id']=='first': first=boot
                result['evidence_sha256']=digest(path)
                result['request_id']=boot['request_id']
            state['steps'].append(result); persist()
        if recipe_sources(value,profile_path)!=source_hashes:
            raise RuntimeError('target recipe inputs changed during execution')
        state.update(status='completed',active_step=None); persist()
    except BaseException as error:
        state.update(status='failed',error_type=type(error).__name__); persist(); raise
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('action',choices=('preflight','plan','build'))
    parser.add_argument('--profile',type=Path,default=DEFAULT_PROFILE); parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--native-dir',type=Path); parser.add_argument('--kernel-dir',type=Path); parser.add_argument('--inputs-dir',type=Path)
    parser.add_argument('--offline',action='store_true'); args=parser.parse_args(); value=profile(args.profile)
    if args.action=='preflight': result=preflight(value,args.work_dir)
    elif args.action=='plan': result=command_plan(value,args.work_dir,native_dir=args.native_dir,kernel_dir=args.kernel_dir,inputs_dir=args.inputs_dir,offline=args.offline)
    else: result=build(value,args.work_dir,profile_path=args.profile,native_dir=args.native_dir,kernel_dir=args.kernel_dir,inputs_dir=args.inputs_dir,offline=args.offline)
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
