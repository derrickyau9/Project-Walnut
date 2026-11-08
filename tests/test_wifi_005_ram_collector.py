"""Failure gates for the no-RF RAM collector; no device/radio operations."""
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('wifi005_ram',ROOT/'system/freebsd/raspberrypi/collect_wifi_005_ram.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
NONCE='a'*32
# Mount-table paths describe the FreeBSD target, regardless of the test host.
RAM=PurePosixPath('/DaOS-recovery-ram')
ARTIFACTS=RAM/('wifi005-no-rf-'+NONCE)
MOUNTS='/dev/md0 / ufs ro 1 1\ndevfs /dev devfs rw 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n'


def fdt_fixture(*,old=False,sd_disabled=False):
    strings=bytearray();offsets={};body=bytearray()
    def word(n):body.extend(struct.pack('>I',n))
    def node(name):
        word(1);body.extend(name.encode()+b'\0')
        while len(body)%4:body.append(0)
    def end():word(2)
    def prop(name,data):
        if name not in offsets:offsets[name]=len(strings);strings.extend(name.encode()+b'\0')
        word(3);word(len(data));word(offsets[name]);body.extend(data)
        while len(body)%4:body.append(0)
    def text(name,value):prop(name,value.encode()+b'\0')
    node('');text('compatible','raspberrypi,4-model-b\0brcm,bcm2711')
    node('aliases');text('mmc0','/emmc2bus/mmc@7e340000');text('mmc1','/soc/mmcnr@7e300000');end()
    node('soc')
    for name,status in (('mmc@7e202000','disabled'),('mmc@7e300000','okay' if old else 'disabled'),('mmcnr@7e300000','disabled' if old else 'okay')):
        node(name);text('status',status);text('compatible','brcm,bcm2835-mmc')
        if name.startswith('mmcnr'):prop('mmc-pwrseq',struct.pack('>I',42))
        end()
    end();node('emmc2bus');node('mmc@7e340000');text('status','disabled' if sd_disabled else 'okay');text('compatible','brcm,bcm2711-emmc2');end();end()
    node('daos-wifi-pwrseq');text('compatible','mmc-pwrseq-simple');prop('phandle',struct.pack('>I',42));end()
    end();word(9)
    start=56;strstart=start+len(body);size=strstart+len(strings)
    return struct.pack('>10I',0xd00dfeed,size,start,strstart,40,17,16,0,len(strings),len(body))+b'\0'*16+body+strings


class FakeRunner:
    def __init__(self):
        self.calls=[];self.mount_calls=0;self.changed_mounts=None;self.radio=False;self.quirks='4';self.health=True;self.dmesg=None;self.fdt=fdt_fixture();self.bootfile=c.BOOTFILE;self.module_path=c.KERNEL_DIR;self.md_type='preload'
    def run(self,args,limit=65536,accepted=(0,)):
        self.calls.append(args);code=0;value=''
        if args==['/rescue/mount','-p']:
            self.mount_calls+=1;value=self.changed_mounts if self.mount_calls>1 and self.changed_mounts else MOUNTS
        elif args==['/sbin/mdconfig','-l','-v','-u','0']:value='md0 '+self.md_type+' 384M'
        elif args==['/sbin/kldstat','-q','-n','if_brcmfmac.ko']:code=0 if self.radio else 1
        elif args==['/sbin/kldstat','-v']:
            value=' 1 6 0xffff000000000000 14b67f0 kernel ('+c.BOOTFILE+')\n\tContains modules:\n\t Id Name\n'+''.join('\t '+str(i)+' '+n+'\n' for i,n in enumerate(c.DEPENDENCY_MODULES.values(),1))
        elif args[:3]==['/sbin/kldstat','-q','-m'] or args[:3]==['/sbin/kldstat','-q','-n']:pass
        elif args==['/usr/local/bin/python3.12','-I','-B',c.SUPERVISOR,'health']:
            value=json.dumps({'format':'DaOS-early-recovery-adb-health-v1','ready':self.health,'supervisor_alive':True,'host_configured':True,'adb_authenticated':True})
        elif args==['/sbin/sysctl','-b','hw.fdt.dtb']:return 0,self.fdt
        elif args==['/rescue/dmesg','-a']:value=self.dmesg if self.dmesg is not None else 'DaOS diagnostic: platform DMA and generic SDMA disabled\n'*2
        elif args==['/sbin/sysctl','-aN']:value='\n'.join('dev.sdhci_bcm.'+n+'.slot0.quirks' for n in ('0','1'))
        elif args[:2]==['/sbin/sysctl','-n']:
            key=args[2]
            if key=='vm.swap_total':value='0'
            elif key=='kern.bootfile':value=self.bootfile
            elif key=='kern.module_path':value=self.module_path
            elif key=='net.wlan.devices':value='brcmfmac0' if self.radio else ''
            elif key.endswith('.%pnpinfo'):value='name='+('mmcnr@7e300000 compat=brcm,bcm2835-mmc' if '.0.' in key else 'mmc@7e340000 compat=brcm,bcm2711-emmc2')
            elif key.endswith('.quirks'):value=self.quirks
            elif key.endswith(('.dumpregs','.dumpcaps')):value='read-only controller data'
            else:raise AssertionError('Unexpected read command: '+repr(args))
        else:raise AssertionError('Unexpected command: '+repr(args))
        if code not in accepted:raise c.Refused('Fixture command failed')
        return code,value.encode()
    def text(self,args,**kwargs):
        code,data=self.run(args,**kwargs);return code,data.decode().strip()


class CollectorGateTests(unittest.TestCase):
    def setUp(self):
        self.runner=FakeRunner()
        self.env={'daos_recovery':'YES','daos_wifi_trial':'005','hw.bcm2835.sdhci.pio_mode':'1',
            'init_script':c.INIT_SCRIPT,'nextboot_enable':'NO','sdio_load':'NO','if_brcmfmac_load':'NO','rpi_ft5406_load':'NO',
            'daos_wifi005_no_rf':'YES','daos_wifi005_trial_nonce':NONCE,
            'daos_wifi005_kernel_sha256':c.PINS['kernel'][1],'daos_wifi005_ram_gzip_sha256':c.RAM_GZIP_SHA}
        self.hashes=[]
    def call(self,**kw):
        def hashfile(path,pin):self.hashes.append(str(path));return {'bytes':pin[0],'sha256':pin[1]}
        with patch.object(c,'RAM',RAM),patch.object(c,'trusted',side_effect=lambda p,**kwargs:Path(p)),patch.object(Path,'iterdir',return_value=iter(Path(name) for name in c.PINS)):
            return c.collect(ARTIFACTS,NONCE,runner=self.runner,getenv=self.env.__getitem__,hash_file=hashfile,native=False,**kw)
    def test_success_is_bounded_and_never_uses_card_or_mutation_commands(self):
        report=self.call();self.assertTrue(report['passed']);self.assertFalse(report['resident_kernel_sha_measured'])
        self.assertLess(len(c.encode_report(report)),c.MAX_REPORT)
        for args in self.runner.calls:
            self.assertNotIn('/dev/mmcsd0',' '.join(args));self.assertNotIn('/dev/gpt/',' '.join(args))
            self.assertTrue(args[0] in ('/rescue/mount','/sbin/mdconfig','/sbin/sysctl','/sbin/kldstat','/rescue/dmesg','/usr/local/bin/python3.12'))
            self.assertFalse(any('=' in a for a in args))
    def test_physical_ufs_alias_or_other_mount_refused_before_artifact_read(self):
        for row in ('/dev/gpt/DaOS-state /var ufs ro 1 1','/dev/mmcsd0p1 /efi msdosfs ro 0 0','/var/photos /apps nullfs ro 0 0','proc /proc procfs rw 0 0'):
            with self.subTest(row=row),patch.object(self.runner,'text',return_value=(0,MOUNTS+row+'\n')),self.assertRaises(c.Refused):self.call()
        self.assertFalse(self.hashes)
    def test_nonresident_vnode_root_rejected(self):
        self.runner.md_type='vnode'
        with self.assertRaises(c.Refused):self.call()
    def test_wrong_nonce_artifact_hash_binding_or_radio_flag_refused(self):
        for key,value in (('daos_wifi005_trial_nonce','b'*32),('daos_wifi005_kernel_sha256','f'*64),('daos_wifi005_ram_gzip_sha256','f'*64),('if_brcmfmac_load','YES'),('sdio_load','YES'),('nextboot_enable','YES'),('daos_recovery','NO'),('hw.bcm2835.sdhci.pio_mode','0')):
            original=self.env[key];self.env[key]=value
            with self.subTest(key=key),self.assertRaises(c.Refused):self.call()
            self.env[key]=original
    def test_wrong_bootfile_or_module_search_path_refused(self):
        self.runner.bootfile='/boot/kernel/kernel'
        with self.assertRaises(c.Refused):self.call()
        self.runner.bootfile=c.BOOTFILE;self.runner.module_path=c.KERNEL_DIR+';/boot/kernel'
        with self.assertRaises(c.Refused):self.call()
    def test_loaded_radio_or_unauthenticated_usb_refused(self):
        self.runner.radio=True
        with self.assertRaises(c.Refused):self.call()
        self.runner.radio=False;self.runner.health=False
        with self.assertRaises(c.Refused):self.call()
    def test_hardware_dma_capability_does_not_override_missing_pio_quirks(self):
        for value in ('0','2','6','-1','4 garbage'):
            self.runner.quirks=value
            with self.subTest(value=value),self.assertRaises(c.Refused):self.call()
    def test_old_fdt_and_unreviewed_sd_disabled_variant_refused(self):
        for data in (fdt_fixture(old=True),fdt_fixture(sd_disabled=True)):
            self.runner.fdt=data
            with self.assertRaises(c.Refused):self.call()
    def test_initial_storage_error_or_missing_pio_messages_refused(self):
        for value in ('g_vfs_done(): READ error =5','DaOS diagnostic: platform DMA and generic SDMA disabled','no messages'):
            self.runner.dmesg=value
            with self.subTest(value=value),self.assertRaises(c.Refused):self.call()
    def test_mount_change_during_collection_refused(self):
        self.runner.changed_mounts=MOUNTS+'tmpfs /tmp tmpfs rw 0 0\n'
        with self.assertRaises(c.Refused):self.call()
    def test_radio_attachment_during_collection_refused(self):
        run=self.runner.run
        def changing(args,**kwargs):
            if args==['/rescue/dmesg','-a']:self.runner.radio=True
            return run(args,**kwargs)
        with patch.object(self.runner,'run',side_effect=changing),self.assertRaises(c.Refused):self.call()
    def test_oversized_final_report_rejected(self):
        with self.assertRaises(c.Refused):c.encode_report({'data':'x'*(c.MAX_REPORT+1)})


class ParserAndProcessTests(unittest.TestCase):
    def test_exact_timeout_register_fields_are_distinct_from_timeout_errors(self):
        rows='sdhci_bcm0-slot0: Timeout:  0x00000000 | Int stat: 0x00000000\nsdhci_bcm1-slot0: Timeout:  0x0000000e | Int stat: 0x00000003\n'
        self.assertEqual(c.dmesg_evidence(rows)['normal_timeout_register_rows'],2)
        for text in (rows.replace('0x00000003','0x00008003'),rows.replace('0x00000003','0x00010003'),rows.replace('0x0000000e','0x0000010e'),
                     'sdhci_bcm0-slot0: Controller timeout\n','sdhci_bcm1: command timeout\n',rows+'g_vfs_done(): I/O error\n',rows+'sdhci_bcm0-slot0: Timeout: error\n'):
            with self.subTest(text=text),self.assertRaises(c.Refused):c.dmesg_evidence(text)
    def test_registered_dependency_names_must_belong_to_candidate_kernel(self):
        text=FakeRunner().text(['/sbin/kldstat','-v'])[1]
        self.assertEqual(c.resident_dependencies(text)['sdiob']['registered_name'],'SDIOB_NAME')
        for changed in (text.replace('SDIOB_NAME','sdiob'),text.replace('pcib/pci','pci'),text.replace(c.BOOTFILE,'/boot/kernel/kernel')):
            with self.assertRaises(c.Refused):c.resident_dependencies(changed)
    def test_only_identical_devfs_duplicates_are_accepted_and_counted(self):
        rows=c.mounts(MOUNTS+'devfs /dev devfs rw 0 0\n')
        with patch.object(c,'RAM',RAM):
            self.assertEqual(sum(row[1]=='/dev' for row in c.isolation(rows)),2)
        for extra in ('devfs /dev devfs ro 0 0','/dev/md1 / ufs ro 1 1','tmpfs /DaOS-recovery-ram tmpfs rw 0 0'):
            with self.subTest(extra=extra),self.assertRaises(c.Refused):c.mounts(MOUNTS+extra+'\n')
    def test_fdt_bounds_duplicates_truncation_and_termination(self):
        good=fdt_fixture();self.assertTrue(c.validate_fdt(good)['wifi_pwrseq_verified'])
        for data in (good[:30],good[:-8],b'bad'+good[3:],good+b'x'*131072):
            with self.subTest(size=len(data)),self.assertRaises((ValueError,UnicodeError)):c.fdt_properties(data)
    @unittest.skipUnless(os.name=='posix','The native collector selects POSIX subprocess pipes')
    def test_runner_deadline_and_output_bound_stop_own_child(self):
        with self.assertRaises(c.Refused):c.Runner(seconds=.1).run([sys.executable,'-c','import time;time.sleep(2)'])
        with self.assertRaises(c.Refused):c.Runner().run([sys.executable,'-c','print("x"*10000)'],limit=1000)
    @unittest.skipUnless(os.name=='posix','The native collector selects POSIX subprocess pipes')
    def test_runner_nonzero_and_stderr_fail_closed(self):
        for script in ('raise SystemExit(3)','import sys;sys.stderr.write("failure")'):
            with self.assertRaises(c.Refused):c.Runner().run([sys.executable,'-c',script])
    @unittest.skipUnless(os.name=='posix' and hasattr(os,'O_NOFOLLOW'),'POSIX no-follow descriptors and file ownership')
    def test_pinned_hash_mismatch_checked_on_real_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'kernel';p.write_bytes(b'candidate')
            real=c.os.fstat
            def root_stat(fd):
                s=real(fd)
                return SimpleNamespace(st_mode=s.st_mode,st_uid=0,st_nlink=s.st_nlink,st_size=s.st_size,st_mtime_ns=s.st_mtime_ns,st_ctime_ns=s.st_ctime_ns)
            with patch.object(c,'trusted'),patch.object(c.os,'fstat',side_effect=root_stat):
                self.assertEqual(c.pinned(p,(9,hashlib.sha256(b'candidate').hexdigest()))['bytes'],9)
                with self.assertRaises(c.Refused):c.pinned(p,(9,'a'*64))
                with self.assertRaises(c.Refused):c.pinned(p,(8,'a'*64))
    def test_kenv_missing_permission_and_malformed_values_refused(self):
        def libc_for(payload,count,error=0):
            def call(action,name,target,size):
                ctypes.memmove(target,payload,len(payload));ctypes.set_errno(error);return count
            return SimpleNamespace(kenv=call)
        with patch.object(c.ctypes,'CDLL',return_value=libc_for(b'YES\0',4)):
            self.assertEqual(c.native_kenv('test'),'YES')
        for payload,count,error in ((b'',-1,errno.ENOENT),(b'',-1,errno.EPERM),(b'YES',3,0),(b'Y\0S\0',4,0),(b'',4097,0)):
            with patch.object(c.ctypes,'CDLL',return_value=libc_for(payload,count,error)),self.assertRaises(c.Refused):c.native_kenv('test')


if __name__=='__main__':unittest.main()
