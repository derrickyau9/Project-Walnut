#!/usr/local/bin/python3.12
"""Attach Pi4 radio after the user has selected the installation country."""
import os
from pathlib import Path
import re
import signal
import subprocess
import threading

STOP=threading.Event()
COUNTRY=Path('/var/DaOS/system/wifi/country')

def main():
    if os.geteuid()!=0:raise SystemExit('Root required')
    bootfile=subprocess.check_output(['/sbin/sysctl','-n','kern.bootfile'],text=True,timeout=5).strip()
    if bootfile!='/boot/kernel.daos-wifi-004/kernel':
        print('Original kernel active; Pi4 SDIO radio attach skipped',flush=True)
        return
    signal.signal(signal.SIGTERM,lambda *_:STOP.set())
    signal.signal(signal.SIGINT,lambda *_:STOP.set())
    while not STOP.is_set():
        try:
            country=COUNTRY.read_text().strip()
            if COUNTRY.is_symlink() or COUNTRY.stat().st_uid!=0:raise ValueError('Unsafe region configuration')
        except FileNotFoundError:country=''
        if re.fullmatch('[A-Z]{2}',country):break
        STOP.wait(1)
    else:return
    if subprocess.run(['/sbin/kldstat','-q','-n','if_brcmfmac.ko'],timeout=5).returncode==0:return
    subprocess.run(['/bin/kenv','hw.brcmfmac.country='+country],check=True,stdout=subprocess.DEVNULL,timeout=5)
    # A single attempt: never retry a potentially blocked kernel attach.
    subprocess.run(['/sbin/kldload','-n','/boot/kernel.daos-wifi-004/if_brcmfmac.ko'],check=True,timeout=120)
    Path('/var/run/DaOS-wifi-country').write_text(country+'\n')
    print('Pi4 wireless driver attached with region '+country,flush=True)

if __name__=='__main__':main()
