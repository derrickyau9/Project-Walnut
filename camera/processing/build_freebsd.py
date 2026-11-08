"""Build fixed OpenCV and hf-imager on the private FreeBSD builder scratch disk.

Run in /var/DaOS004-work/processing; never installs into the running OS or SDK.
Source archive must be the pinned upstream bytes; --arm64 selects the existing
read-only FreeBSD ARM64 SDK. Native amd64 is an independently runnable QA build.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

SHA='fa3faf7581f1fa943c9e670cf57dd6ba1c5b4178f363a188a2c8bff1eb28b7e4'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm64',action='store_true')
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    root=Path('/var/DaOS004-work/processing')
    if os.uname().sysname!='FreeBSD' or not root.is_dir():raise RuntimeError('private FreeBSD work disk required')
    archive=root/'opencv-4.12.0.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=SHA:raise RuntimeError('OpenCV source hash mismatch')
    arch='arm64' if args.arm64 else 'amd64';out=root/arch;out.mkdir(exist_ok=True)
    temporary=root/'tmp';temporary.mkdir(exist_ok=True);os.environ['TMPDIR']=str(temporary)
    flags=[]
    if args.arm64:
        sdk=Path('/var/daos-builds/daos-base-arm64-pi-splash-001/stage')
        toolchain=out/'toolchain.cmake'
        toolchain.write_text('''set(CMAKE_SYSTEM_NAME FreeBSD)
set(CMAKE_SYSTEM_VERSION 15.1)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER /usr/bin/clang)
set(CMAKE_CXX_COMPILER /usr/bin/clang++)
set(CMAKE_ASM_COMPILER /usr/bin/clang)
set(CMAKE_C_COMPILER_TARGET aarch64-unknown-freebsd15.1)
set(CMAKE_CXX_COMPILER_TARGET aarch64-unknown-freebsd15.1)
set(CMAKE_ASM_COMPILER_TARGET aarch64-unknown-freebsd15.1)
set(CMAKE_ASM_FLAGS_INIT "--target=aarch64-unknown-freebsd15.1")
set(CMAKE_SYSROOT "'''+str(sdk)+'''")
set(CMAKE_FIND_ROOT_PATH "'''+str(sdk)+'''")
set(CMAKE_C_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_CXX_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_EXE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(CMAKE_CROSSCOMPILING_EMULATOR "")
''')
        flags=['-DCMAKE_TOOLCHAIN_FILE='+str(toolchain)]
    def run(command,name):
        started=time.monotonic()
        with (out/(name+'.log')).open('w') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
        print(json.dumps({'stage':name,'seconds':round(time.monotonic()-started,2),'returncode':result.returncode}),flush=True)
        if result.returncode:
            print((out/(name+'.log')).read_text(errors='replace')[-10000:],flush=True)
            raise SystemExit(result.returncode)
    switches={'BUILD_LIST':'core,imgproc,imgcodecs,photo,features2d,calib3d','BUILD_SHARED_LIBS':'OFF',
        'BUILD_TESTS':'OFF','BUILD_PERF_TESTS':'OFF','BUILD_EXAMPLES':'OFF','BUILD_opencv_apps':'OFF',
        'BUILD_JAVA':'OFF','BUILD_opencv_python2':'OFF','BUILD_opencv_python3':'OFF','BUILD_opencv_gapi':'OFF',
        'WITH_ADE':'OFF','WITH_PROTOBUF':'OFF','BUILD_PROTOBUF':'OFF','WITH_AVIF':'OFF','WITH_IPP':'OFF',
        'WITH_ITT':'OFF','WITH_OPENCL':'OFF','WITH_TBB':'OFF','WITH_OPENMP':'OFF','WITH_FFMPEG':'OFF',
        'WITH_GSTREAMER':'OFF','WITH_VTK':'OFF','WITH_WEBP':'OFF','WITH_OPENEXR':'OFF','WITH_JASPER':'OFF',
        'WITH_OPENJPEG':'OFF','WITH_KLEIDICV':'OFF','WITH_CAROTENE':'OFF','WITH_FASTCV':'OFF',
        'BUILD_ZLIB':'ON','BUILD_PNG':'ON','BUILD_TIFF':'ON','BUILD_JPEG':'ON','WITH_JPEG':'ON',
        'WITH_PNG':'ON','WITH_TIFF':'ON','CMAKE_BUILD_TYPE':'Release','CMAKE_INSTALL_PREFIX':str(out/'install')}
    run(['cmake','-S',str(root/'opencv-4.12.0'),'-B',str(out/'opencv-build'),*flags,
         *['-D'+k+'='+v for k,v in switches.items()]],'opencv-configure')
    run(['cmake','--build',str(out/'opencv-build'),'--parallel','6'],'opencv-build')
    run(['cmake','--install',str(out/'opencv-build')],'opencv-install')
    run(['cmake','-S',str(args.source/'camera/processing'),'-B',str(out/'worker-build'),*flags,
         '-DCMAKE_BUILD_TYPE=Release','-DOpenCV_DIR='+str(out/'install/lib/cmake/opencv4'),
         '-DDAOS_OPENCV_SOURCE='+str(root/'opencv-4.12.0'),'-DDAOS_OPENCV_BUILD='+str(out/'opencv-build')],
         'worker-configure')
    run(['cmake','--build',str(out/'worker-build'),'--parallel','4'],'worker-build')
    worker=out/'worker-build/hf-imager'
    print(json.dumps({'status':'built-not-target-executed' if args.arm64 else 'built',
          'worker':str(worker),'sha256':hashlib.sha256(worker.read_bytes()).hexdigest()}),flush=True)


if __name__=='__main__':main()
