#!/usr/local/bin/python3.12
"""Native trial acceptance. Writes failed receipts too; never commits a kernel.

The operator first connects WPA2 in the camera and observes USB/DSI/touch/camera
operation. This check reassociates that saved network, writes/verifies 64 MiB on
the SD data partition concurrently with two controlled synthetic captures, and
requires both radio bands in scan results. No passwords or SSIDs enter reports.
"""
import argparse,hashlib,ipaddress,json,os,re,socket,sys,threading,time,uuid
from pathlib import Path
from deploy_wifi_005 import KERNEL,KERNEL_SHA,PAYLOAD_SHA,WORK,native,run,sha,atomic,CHECKS,require_running_trial

STORAGE_ERROR_PATTERN=r'(?i)(I/O error|Input/output error|CAM status:.*error|mmc.*(?:timeout|error)|sdhci.*(?:timeout|error)|panic:|g_vfs_done|UFS.*error)'

def rpc(connection,method,params=None,session=''):
    stream=connection.makefile('rwb',buffering=0)
    try:
        stream.write(json.dumps({'method':method,'params':params or {},'session':session}).encode()+b'\n')
        line=stream.readline(65537)
        if not line or len(line)>65536:raise ValueError('Camera broker response interrupted')
        result=json.loads(line)
        if 'error' in result:raise ValueError(result['error'])
        return result['result']
    finally:stream.close()
def wpa(interface,*args):
    return run(['/usr/sbin/wpa_cli','-p','/var/run/DaOS-system/wpa','-i',interface,*args])
def status(interface):return dict(line.split('=',1) for line in wpa(interface,'status').splitlines() if '=' in line)
def associated(interface):
    value=status(interface)
    addresses=re.findall(r'\binet ([0-9.]+)',run(['/sbin/ifconfig',interface]))
    try:address=ipaddress.ip_address(addresses[0] if addresses else '')
    except ValueError:return False
    lease=Path('/var/db')/('dhclient.leases.'+interface)
    leased=lease.is_file() and not lease.is_symlink() and lease.stat().st_size<=1024*1024 and ('fixed-address '+str(address)+';') in lease.read_text(errors='replace')
    return value.get('wpa_state')=='COMPLETED' and value.get('key_mgmt') in ('WPA2-PSK','WPA-PSK-SHA256') and leased and not address.is_link_local and not address.is_loopback
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--interface',default='wlan0')
    parser.add_argument('--observed-usb-dsi-touch-camera',action='store_true');parser.add_argument('--output',type=Path,default=WORK/'validation.json')
    args=parser.parse_args();native()
    if not re.fullmatch(r'wlan[0-9]{1,2}',args.interface):raise ValueError('Expected wlan interface')
    require_running_trial()
    report={'format':'DaOS-wifi-005-validation-v1','kernel_sha256':KERNEL_SHA,'payload_sha256':PAYLOAD_SHA,
            'boot_time':run(['/sbin/sysctl','-n','kern.boottime']),'started_at':time.time(),'checks':dict.fromkeys(CHECKS,False),'errors':[]}
    initial=run(['/sbin/dmesg','-a']);scratch=WORK/('storage-'+uuid.uuid4().hex+'.bin');stop=threading.Event();storage_error=[]
    def storage():
        try:
            digest=hashlib.sha256();block=os.urandom(1024*1024)
            with scratch.open('xb') as stream:
                for index in range(64):stream.write(block);digest.update(block);stream.flush();os.fsync(stream.fileno())
            if sha(scratch)!=digest.hexdigest():raise ValueError('SD readback mismatch')
            while not stop.wait(.05):
                if sha(scratch)!=digest.hexdigest():raise ValueError('Concurrent SD readback mismatch')
        except BaseException as error:storage_error.append(str(error))
    worker=None
    try:
        report['checks']['platform_dma_disabled']=True
        if re.search(STORAGE_ERROR_PATTERN,initial):raise ValueError('Storage errors occurred during this candidate boot before validation')
        region=run(['/sbin/ifconfig','-v',args.interface])
        report['checks']['country_us']=Path('/var/run/DaOS-wifi-country').read_text().strip()=='US' and Path('/var/DaOS/system/wifi/country').read_text().strip()=='US' and re.search(r'\bcountry\s+US\b',region) is not None
        if 'OK' not in wpa(args.interface,'scan'):raise ValueError('Scan did not start')
        end=time.monotonic()+15;frequencies=[]
        while time.monotonic()<end:
            time.sleep(.5);frequencies=[int(parts[1]) for line in wpa(args.interface,'scan_results').splitlines() if len(parts:=line.split('\t',4))==5 and parts[1].isdigit()]
            if any(f<3000 for f in frequencies) and any(f>4900 for f in frequencies):break
        report['checks']['scan_24ghz']=any(2400<=f<=2500 for f in frequencies)
        report['checks']['scan_5ghz']=any(4900<=f<=5900 for f in frequencies)
        report['checks']['wpa2_dhcp']=associated(args.interface)
        if not report['checks']['wpa2_dhcp']:raise ValueError('Connect an existing WPA2 network and obtain DHCP before validation')
        worker=threading.Thread(target=storage);worker.start()
        wpa(args.interface,'disconnect');deadline=time.monotonic()+5
        while time.monotonic()<deadline and status(args.interface).get('wpa_state')=='COMPLETED':time.sleep(.2)
        if status(args.interface).get('wpa_state')=='COMPLETED':raise ValueError('Wireless disconnect was not observed')
        wpa(args.interface,'reassociate');deadline=time.monotonic()+60
        while time.monotonic()<deadline and not associated(args.interface):time.sleep(.5)
        report['checks']['reconnect']=associated(args.interface)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(15);connection.connect('/var/DaOS/ui/camera-link.sock')
            state=rpc(connection,'getState')
            if not state.get('simulated'):raise ValueError('This automated receipt requires the synthetic development backend; real sensors need separate acceptance')
            session=rpc(connection,'session.acquire')['session'];requests=[]
            try:
                for index in range(2):
                    identity=str(uuid.uuid4());requests.append(identity);report['capture_uuids']=requests;atomic(args.output,report)
                    result=rpc(connection,'capture',{'request_id':identity},session)
                    if not result.get('accepted'):raise ValueError('Camera did not accept verification shot')
                    deadline=time.monotonic()+30
                    while time.monotonic()<deadline:
                        value=rpc(connection,'query',{'request_id':identity},session)
                        if value['status'] in ('complete','failed','cancelled','unknown'):break
                        time.sleep(.1)
                    if value['status']!='complete':raise ValueError('Original verification UUID not completed; never reshoot it')
            finally:rpc(connection,'session.release',session=session)
        time.sleep(5)
        stop.set();worker.join(timeout=30)
        report['checks']['storage_concurrent']=not worker.is_alive() and not storage_error and scratch.is_file()
        current=run(['/sbin/dmesg','-a']);delta=current[len(initial):] if current.startswith(initial) else current
        report['checks']['no_new_storage_errors']=re.search(STORAGE_ERROR_PATTERN,delta) is None
        usb=Path('/var/run/DaOS-usb-adb-session.pid')
        if usb.exists():os.kill(int(usb.read_text().strip()),0)
        report['checks']['usb_dsi_camera_observed']=args.observed_usb_dsi_touch_camera and usb.exists()
    except BaseException as error:report['errors'].append(str(error))
    finally:
        stop.set()
        if worker:worker.join(timeout=30)
        if worker is None or not worker.is_alive():scratch.unlink(missing_ok=True)
        report['errors'].extend(storage_error);report['completed_at']=time.time();report['passed']=all(report['checks'].values()) and not report['errors'];atomic(args.output,report)
    print(json.dumps({'passed':report['passed'],'checks':report['checks'],'report':str(args.output),'default_changed':False}))
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
