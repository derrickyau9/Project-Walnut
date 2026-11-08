#!/usr/bin/env python3
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess

WORK=Path(os.environ.get('DAOS_WIFI_WORK_ROOT','/var/DaOS-wifi-build-memory'))/('native-'+os.environ.get('DAOS_WIFI_REVISION','001'))

def main():
    record=json.loads((WORK/'native-complete.json').read_text())
    driver=WORK/'driver'
    source=driver/'src/sdio.c'
    old=source.read_text()
    if 'daos_pi4_nvram' in old:raise ValueError('Patch already applied')
    data=old.replace('#include <sys/firmware.h>','#include <sys/firmware.h>\n#include <dev/ofw/openfirm.h>\n#include <dev/ofw/ofw_bus_subr.h>')
    anchor='static int brcmf_sdio_diag_sysctl(SYSCTL_HANDLER_ARGS);'
    assert data.count(anchor)==1
    mac_include='/var/tmp/DaOS-pi4-wifi-mac-v2.inc' if os.environ.get('DAOS_WIFI_REVISION','001')!='001' else '/var/tmp/DaOS-pi4-wifi-mac-001.inc'
    data=data.replace(anchor,Path(mac_include).read_text()+'\n'+anchor)
    data=data.replace('nvram = brcmf_nvram_parse(nvram_fw->data,','nvram = daos_pi4_nvram(nvram_fw->data,')
    anchor='\n\t/* Download firmware */\n'
    assert data.count(anchor)==1
    data=data.replace(anchor,'\n\tif (nvram == NULL) {\n\t\tdevice_printf(sc->dev, "Pi4 calibration or board FDT MAC unavailable\\n");\n\t\tfirmware_put(fw, FIRMWARE_UNLOAD);\n\t\treturn (ENXIO);\n\t}\n'+anchor)
    source.write_text(data)
    patch=WORK/'DaOS-pi4-board-mac.patch'
    patch.write_text(''.join(difflib.unified_diff(old.splitlines(True),data.splitlines(True),fromfile='a/src/sdio.c',tofile='b/src/sdio.c')))
    src=WORK/'source';sdk=Path('/var/daos-builds/daos-base-arm64-pi-splash-001/stage')
    script=WORK/'modules.sh'
    common=['make','-C',str(src),'TARGET=arm64','TARGET_ARCH=aarch64','SRCCONF='+str(WORK/'config/src.conf'),
            'SRC_ENV_CONF=/dev/null','__MAKE_CONF=/dev/null','WORLDTMP='+str(sdk),
            'buildenv','BUILDENV_SHELL=/usr/bin/env PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh '+str(script)]
    env=dict(os.environ,MAKEOBJDIRPREFIX=str(WORK/'obj'))
    with (WORK/'mac-build.log').open('x') as log:subprocess.run(common,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    module=driver/'if_brcmfmac.ko'
    record['artifacts']['if_brcmfmac.ko']={'path':str(module),'bytes':module.stat().st_size,'sha256':hashlib.sha256(module.read_bytes()).hexdigest()}
    record['daos_board_mac_patch_sha256']=hashlib.sha256(patch.read_bytes()).hexdigest()
    (WORK/'native-final.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':'compiled','board_mac':'FDT mac-address/local-mac-address; fixed NVRAM address replaced'}))

if __name__=='__main__':main()
