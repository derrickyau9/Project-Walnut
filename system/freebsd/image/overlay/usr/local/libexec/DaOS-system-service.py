#!/usr/local/bin/python3.12
"""Local Wi-Fi manager; only root/camera peers and fixed network operations."""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import socketserver
import socket
import subprocess
import threading
import time
import uuid
import importlib.util

RUNTIME=Path('/var/run/DaOS-system')
CONFIG=Path('/var/DaOS/system/wifi')
CTRL=RUNTIME/'wpa'
LOCK=threading.RLock()
STOP=threading.Event()
_ops_spec=importlib.util.spec_from_file_location('daos_system_ops',Path(__file__).with_name('DaOS-system-ops.py'))
_ops=importlib.util.module_from_spec(_ops_spec);_ops_spec.loader.exec_module(_ops)

def command(args, timeout=8):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout)
    return p.returncode,p.stdout[:60000]

class Wifi:
    def __init__(self):
        self.interface=None
        self.parent=''
        self.wpa=None
        self.dhcp=None
        self.since=0
        self.requested=''
        self.disabled=False
        self.dhcp_retry=0

    def adapters(self):
        return [v for v in command(['/sbin/sysctl','-n','net.wlan.devices'])[1].split() if re.fullmatch('[a-z][a-z0-9]{0,15}',v)]

    def ensure(self):
        if self.interface: return True
        devices=self.adapters()
        if not devices: return False
        existing=command(['/sbin/ifconfig','-l'])[1].split()
        for n in range(10):
            name='wlan'+str(n)
            if name in existing:
                if 'description: DaOS Wi-Fi' not in command(['/sbin/ifconfig',name])[1]:continue
            else:
                if command(['/sbin/ifconfig',name,'create','wlandev',devices[0]])[0]:continue
                command(['/sbin/ifconfig',name,'description','DaOS Wi-Fi'])
            self.interface=name
            self.parent=devices[0]
            command(['/sbin/ifconfig',name,'up'])
            return True
        raise ValueError('Could not create a wireless interface')

    def cli(self,*args):
        return command(['/usr/sbin/wpa_cli','-p',str(CTRL),'-i',self.interface,*args])[1].strip()

    def terminate(self, process):
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()

    def start(self, profile):
        self.terminate(self.dhcp);self.dhcp=None
        self.terminate(self.wpa)
        self.wpa=subprocess.Popen(['/usr/sbin/wpa_supplicant','-i',self.interface,'-c',str(profile),'-C',str(CTRL)],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(30):
            if self.wpa.poll() is not None:raise ValueError('Wireless supplicant could not start')
            if self.cli('ping').endswith('PONG'):return
            time.sleep(.05)
        raise ValueError('Wireless control interface did not become ready')

    def ensure_supplicant(self):
        if self.wpa is None or self.wpa.poll() is not None:
            profile=CONFIG/'last.conf'
            if self.disabled or not profile.exists():
                profile=RUNTIME/'scan.conf'
                profile.write_text('ctrl_interface='+str(CTRL)+'\nupdate_config=0\n')
                os.chmod(profile,0o600)
            self.start(profile)

    def status(self):
        if any(name.startswith('brcmfmac') for name in self.adapters()):
            desired=(CONFIG/'country').read_text().strip() if (CONFIG/'country').exists() else ''
            marker=Path('/var/run/DaOS-wifi-country')
            active=marker.read_text().strip() if marker.exists() else ''
            if desired!=active:
                return {'available':False,'connected':False,'ssid':'','address':'','message':'Region changed. Restart the device to apply it to the active radio.'}
        if not self.ensure():
            return {'available':False,'connected':False,'ssid':'','address':'',
                    'message':'Select the installation country to start Pi4 Wi-Fi.' if not (CONFIG/'country').exists() else 'Waiting for Pi4 Wi-Fi. Boot the SDIO kernel; if unavailable, check the wireless driver log.'}
        self.ensure_supplicant()
        values=dict(line.split('=',1) for line in self.cli('status').splitlines() if '=' in line)
        associated=values.get('wpa_state')=='COMPLETED' and not self.disabled
        if associated and not self.disabled and time.monotonic()>self.dhcp_retry and (self.dhcp is None or self.dhcp.poll() is not None):
            self.dhcp_retry=time.monotonic()+15
            self.dhcp=subprocess.Popen(['/sbin/dhclient','-d',self.interface],stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        addresses=re.findall(r'\binet (\d+\.\d+\.\d+\.\d+)',command(['/sbin/ifconfig',self.interface])[1])
        address=next((a for a in addresses if a!='0.0.0.0'),'') if associated else ''
        message='Connected' if address else 'Obtaining an address' if associated else 'Not connected'
        if self.disabled:message='Disconnected'
        elif self.since and not associated:
            message='Connecting' if time.monotonic()-self.since<40 else 'Connection not completed. Check password and signal.'
        return {'available':True,'connected':bool(address),'ssid':values.get('ssid',self.requested),'address':address,'message':message,
                'interface':self.interface,'saved':(CONFIG/'last.conf').is_file()}

    def wait_scan(self):
        # Subscribe before requesting the scan. Firmware scans complete
        # asynchronously; a fixed two-second sleep returns an empty old cache.
        endpoint=RUNTIME/('scan-'+uuid.uuid4().hex[:12])
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as events:
            try:
                events.bind(str(endpoint));events.settimeout(2)
                events.connect(str(CTRL/self.interface))
                events.send(b'ATTACH')
                if b'OK' not in events.recv(4096):raise ValueError('Could not monitor wireless scans')
                response=self.cli('scan')
                if 'OK' not in response and 'FAIL-BUSY' not in response:raise ValueError('Wireless scan could not start')
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    events.settimeout(max(.1,deadline-time.monotonic()))
                    try:message=events.recv(4096)
                    except socket.timeout:break
                    if b'CTRL-EVENT-SCAN-RESULTS' in message:return
                    if b'CTRL-EVENT-SCAN-FAILED' in message:raise ValueError('Wireless scan failed; try again')
            finally:endpoint.unlink(missing_ok=True)

    def scan(self):
        state=self.status()
        if not state['available']:return dict(state,networks=[])
        self.wait_scan()
        networks={}
        for line in self.cli('scan_results').splitlines():
            parts=line.split('\t',4)
            if len(parts)!=5 or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}',parts[0],re.I):continue
            _,freq,rssi,flags,ssid=parts
            # wpa_cli escapes SSID bytes. Decode explicit \xHH without eval.
            raw=bytearray()
            i=0
            while i<len(ssid):
                if ssid[i:i+2]=='\\x' and re.fullmatch('[0-9a-fA-F]{2}',ssid[i+2:i+4]):raw.append(int(ssid[i+2:i+4],16));i+=4
                elif ssid[i:i+2]=='\\\\':raw.append(92);i+=2
                else:raw.extend(ssid[i].encode('utf-8'));i+=1
            name=raw.decode('utf-8',errors='replace')
            if not name or len(raw)>32:continue
            secure=any(v in flags for v in ('WPA','RSN','WEP'))
            supported=not secure or ('PSK' in flags and 'WEP' not in flags)
            item={'ssid':name,'rssi':int(rssi),'security':flags,'open':not secure,'supported':supported,
                  'frequency_mhz':int(freq),'physical_band_validated':False,
                  'band':'5 GHz' if int(freq)>=4900 else '2.4 GHz'}
            if name not in networks or item['rssi']>networks[name]['rssi']:networks[name]=item
        return dict(state,networks=sorted(networks.values(),key=lambda n:n['rssi'],reverse=True)[:100])

    def connect(self, data):
        if not self.ensure():return self.status()
        ssid=data.get('ssid');password=data.get('password');is_open=data.get('open')
        if not isinstance(ssid,str) or not 1<=len(ssid.encode('utf-8'))<=32:raise ValueError('SSID must contain 1 to 32 UTF-8 bytes')
        if not isinstance(password,str) or type(is_open) is not bool:raise ValueError('Invalid network credentials')
        if '\x00' in password or '\n' in password or '\r' in password:raise ValueError('Invalid passphrase')
        self.ensure_supplicant()
        lines=['ctrl_interface='+str(CTRL),'update_config=0','network={',' ssid='+ssid.encode('utf-8').hex(),' scan_ssid=1']
        if is_open:lines+=[' key_mgmt=NONE']
        else:
            if re.fullmatch('[0-9a-fA-F]{64}',password):psk=password
            elif 8<=len(password.encode('utf-8'))<=63:psk=hashlib.pbkdf2_hmac('sha1',password.encode(),ssid.encode(),4096,32).hex()
            else:raise ValueError('WPA passphrase must contain 8 to 63 bytes, or 64 hexadecimal digits')
            lines+=[' key_mgmt=WPA-PSK',' psk='+psk]
        lines+=['}']
        temp=CONFIG/'last.conf.tmp'
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write('\n'.join(lines)+'\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,CONFIG/'last.conf')
        self.disabled=False;self.requested=ssid;self.since=time.monotonic()
        self.dhcp_retry=0
        self.start(CONFIG/'last.conf')
        return {'available':True,'connected':False,'ssid':ssid,'address':'','message':'Connecting'}

    def disconnect(self):
        self.disabled=True;self.since=0;self.requested=''
        self.terminate(self.dhcp);self.dhcp=None
        if self.wpa and self.wpa.poll() is None:self.cli('disconnect')
        (CONFIG/'last.conf').unlink(missing_ok=True)
        return {'available':bool(self.interface),'connected':False,'ssid':'','address':'','message':'Disconnected'}

    def region(self,data):
        country=data.get('country','')
        if not isinstance(country,str) or not re.fullmatch('[A-Z]{2}',country):raise ValueError('Enter the two-letter country code for this device location')
        old=(CONFIG/'country').read_text().strip() if (CONFIG/'country').exists() else ''
        fd=os.open(CONFIG/'country.tmp',os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write(country+'\n');f.flush();os.fsync(f.fileno())
        os.replace(CONFIG/'country.tmp',CONFIG/'country')
        loaded=any(name.startswith('brcmfmac') for name in self.adapters())
        return {'country':country,'message':'Region saved. Restart the device to change an active radio region.' if loaded and old!=country else 'Region saved. Starting wireless driver.'}

wifi=Wifi()

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            uid=ctypes.c_uint();gid=ctypes.c_uint()
            libc=ctypes.CDLL(None,use_errno=True)
            libc.getpeereid.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
            if libc.getpeereid(self.request.fileno(),ctypes.byref(uid),ctypes.byref(gid))!=0:return
            if uid.value not in (0,1000):return
            data=self.rfile.readline(8193)
            if len(data)>8192 or not data.endswith(b'\n'):return
            request=json.loads(data)
            if not isinstance(request,dict):return
            with LOCK:
                op=request.get('op')
                if op=='status':result=wifi.status()
                elif op=='scan':result=wifi.scan()
                elif op=='connect':result=wifi.connect(request)
                elif op=='disconnect':result=wifi.disconnect()
                elif op=='region':result=wifi.region(request)
                elif isinstance(op,str) and op.startswith('system.'):result=_ops.handle(request)
                else:raise ValueError('Unknown network operation')
                result['country']=(CONFIG/'country').read_text().strip() if (CONFIG/'country').exists() else ''
        except ValueError as error:result={'message':str(error)[:180]}
        except Exception:result={'message':'Wireless operation failed. Check adapter and driver.'}
        self.wfile.write(json.dumps(result).encode()+b'\n')

def main():
    if os.geteuid()!=0:raise SystemExit('Root service required')
    os.umask(0o077)
    for path in (RUNTIME,CONFIG,CTRL):
        path.mkdir(parents=True,exist_ok=True)
        if path.is_symlink() or path.stat().st_uid!=0:raise SystemExit('Unsafe service directory')
    if CONFIG.parent.is_symlink() or CONFIG.parent.stat().st_uid!=0:raise SystemExit('Unsafe Wi-Fi configuration parent')
    os.chown(RUNTIME,0,1000);os.chmod(RUNTIME,0o750)
    sock=RUNTIME/'control.sock'
    if sock.exists():sock.unlink()
    server=socketserver.ThreadingUnixStreamServer(str(sock),Handler)
    server.daemon_threads=True
    os.chown(sock,0,1000);os.chmod(sock,0o660)
    def stop(*_):
        STOP.set();threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    def monitor():
        while not STOP.wait(3):
            try:
                with LOCK:
                    if (CONFIG/'last.conf').exists():wifi.status()
            except Exception:pass
    threading.Thread(target=monitor,daemon=True).start()
    try:server.serve_forever(poll_interval=.25)
    finally:
        wifi.terminate(wifi.dhcp);wifi.terminate(wifi.wpa)
        server.server_close();sock.unlink(missing_ok=True)

if __name__=='__main__':main()
