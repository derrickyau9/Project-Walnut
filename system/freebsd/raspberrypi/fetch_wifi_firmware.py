#!/usr/bin/env python3
"""Fetch pinned, redistributable Pi4 firmware; retain source identities/licenses."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'build/DaOS_WiFi_001/firmware'
COMMIT='c91cd2804cf7463aab913e7247c176049f16bbd6'
BASE='https://raw.githubusercontent.com/RPi-Distro/firmware-nonfree/'+COMMIT+'/debian/config/brcm80211/'

def fetch(url):
    with urllib.request.urlopen(url,timeout=25) as stream:return stream.read(2*1024**2)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    files={}
    for source,name in [('cypress/cyfmac43455-sdio-standard.bin','brcmfmac43455-sdio.bin'),
                        ('cypress/cyfmac43455-sdio.clm_blob','brcmfmac43455-sdio.clm_blob'),
                        ('brcm/brcmfmac43455-sdio.txt','brcmfmac43455-sdio.txt')]:
        url=BASE+source
        if name.endswith('.txt'):
            # Board-specific RF calibration; do not ship the generic RPi file's
            # fixed example MAC address on every appliance.
            url='https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/9b6d0b0842b5fd99be6df3fcb238be10940e3704/brcm/brcmfmac43455-sdio.raspberrypi,4-model-b.txt?format=TEXT'
            import base64
            data=base64.b64decode(fetch(url))
            # The native DaOS driver replaces the example MAC with the board's
            # firmware-populated FDT MAC before parsing this unchanged input.
        else:data=fetch(url)
        if name.endswith('.bin'):
            version=re.search(rb'Version: ([^\x00]{1,170})',data)
            if not version or not version.group(1).startswith(b'7.45.265 '):raise ValueError('Expected upstream-tested 7.45.265 firmware')
            print(version.group(1).decode(errors='replace'))
        (OUT/name).write_bytes(data)
        files[name]={'url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    for name in ('LICENCE.cypress','LICENCE.broadcom_bcm43xx','GPL-2.0.txt'):
        data=(OUT/name).read_bytes()
        files[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    (OUT/'firmware-manifest.json').write_text(json.dumps({'format':'DaOS-pi4-wifi-firmware-v1',
        'repository':'https://github.com/RPi-Distro/firmware-nonfree','commit':COMMIT,
        'version':'7.45.265','variant':'standard firmware; upstream driver-tested revision',
        'files':files},indent=2)+'\n')

if __name__=='__main__':main()
