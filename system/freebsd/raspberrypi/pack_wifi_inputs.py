#!/usr/bin/env python3
"""Pack the small host-side UI, firmware and licensing inputs for native assembly."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'build/DaOS_WiFi_001'

def main():
    files={
        'root/opt/hasslefree/ui/on-device/WifiPage.qml':ROOT/'ui/on-device/WifiPage.qml',
        'root/opt/hasslefree/ui/on-device/system_settings.py':ROOT/'ui/on-device/system_settings.py',
        'root/usr/local/libexec/DaOS-system-service.py':ROOT/'system/freebsd/image/overlay/usr/local/libexec/DaOS-system-service.py',
        'root/usr/local/etc/rc.d/DaOS-wifi-driver':ROOT/'system/freebsd/image/overlay/usr/local/etc/rc.d/DaOS-wifi-driver',
        'root/usr/local/etc/rc.d/DaOS-system':ROOT/'system/freebsd/image/overlay/usr/local/etc/rc.d/DaOS-system',
        'root/usr/local/libexec/DaOS-wifi-load.py':ROOT/'system/freebsd/image/overlay/usr/local/libexec/DaOS-wifi-load.py',
        'root/usr/local/libexec/DaOS-usb-adb-session.py':ROOT/'system/adb/integration/files/usr/local/libexec/DaOS-usb-adb-session.py',
    }
    firmware=OUT/'firmware'
    manifest=json.loads((firmware/'firmware-manifest.json').read_text())
    for name,entry in manifest['files'].items():
        p=firmware/name
        if hashlib.sha256(p.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('Firmware changed')
        dest='root/boot/firmware/'+name if name.startswith('brcmfmac') else 'root/usr/share/DaOS/wifi/licenses/'+name
        files[dest]=p
    files['root/usr/share/DaOS/wifi/firmware-manifest.json']=firmware/'firmware-manifest.json'
    # Ship complete corresponding external-driver source and integration patches.
    files['root/usr/share/DaOS/wifi/source/brcmfmac-source.tar.gz']=OUT/'brcmfmac-source.tar.gz'
    files['root/usr/share/DaOS/wifi/source/pi4_wifi_mac.c.inc']=ROOT/'system/freebsd/raspberrypi/pi4_wifi_mac.c.inc'
    with tarfile.open(OUT/'host-inputs.tar.gz','w:gz') as tar:
        for dest,p in sorted(files.items()):tar.add(p,arcname=dest,recursive=False)
    print(json.dumps({'bytes':(OUT/'host-inputs.tar.gz').stat().st_size,'files':len(files)}))

if __name__=='__main__':main()
