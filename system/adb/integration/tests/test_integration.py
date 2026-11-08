#!/usr/bin/env python3
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(HERE))
import device_tree
import install
spec=importlib.util.spec_from_file_location('usb_session',HERE/'files/usr/local/libexec/DaOS-usb-adb-session.py')
session=importlib.util.module_from_spec(spec); spec.loader.exec_module(session)

DTS='''/dts-v1/;
/memreserve/ 0x1000 0x100;
/ { compatible="raspberrypi,4-model-b", "brcm,bcm2711";
    #address-cells=<1>; #size-cells=<1>;
    soc { #address-cells=<1>; #size-cells=<1>;
        usb@7e980000 { compatible="brcm,bcm2708-usb"; reg=<0x7e980000 0x10000>;
            status="disabled"; keep-me="literal } inside string";
            child { status="disabled"; };
        };
    };
};
'''

class IntegrationTest(unittest.TestCase):
    def test_dtc_changes_only_intended_properties(self):
        with tempfile.TemporaryDirectory(prefix='daos-usb-dtb-') as work:
            work=Path(work); source=work/'original.dtb'; original_dts=work/'original.dts'; original_dts.write_text(DTS)
            subprocess.run(['dtc','-I','dts','-O','dtb','-o',str(source),str(original_dts)],check=True,capture_output=True)
            original_hash=device_tree.sha(source)
            result=device_tree.derive(source,work/'derived.dtb')
            self.assertEqual(device_tree.sha(source),original_hash)
            self.assertTrue(result['other_properties_and_reservations_unchanged'])
            nodes,_=device_tree.parse_fdt((work/'derived.dtb').read_bytes())
            self.assertEqual(nodes['/soc/usb@7e980000/child']['status'],b'disabled\0')
            self.assertEqual(nodes['/soc/usb@7e980000']['keep-me'],b'literal } inside string\0')
            self.assertEqual(nodes['/soc/usb@7e980000']['dr_mode'],b'peripheral\0')

    def test_actual_official_firmware_can_derive(self):
        source=HERE.parents[2]/'build/pi4-firmware/payload/rpi-firmware/bcm2711-rpi-4-b.dtb'
        if not source.exists(): self.skipTest('official firmware fixture not present')
        with tempfile.TemporaryDirectory(prefix='daos-usb-real-dtb-') as work:
            result=device_tree.derive(source,Path(work)/'pi4.dtb')
            self.assertEqual(result['usb_node'],'/soc/usb@7e980000')
            self.assertIn('snps,dwc2',result['compatible'])
            self.assertFalse(result['physical_runtime_fdt_verified'])

    def test_non_pi_board_rejected(self):
        with self.assertRaises(ValueError): device_tree.usb_node({'/':{'compatible':b'raspberrypi,5-model-b\0'}})

    def test_source_world_without_pi4_board_dtb_is_valid(self):
        with tempfile.TemporaryDirectory(prefix='daos-source-dtb-set-') as work:
            base=Path(work); (base/'boot/dtb/overlays').mkdir(parents=True)
            self.assertEqual(install.source_world_dtbs(base),[])

    def test_public_key_and_private_key_rejection(self):
        public=HERE.parent/'daemon/keys/daos-development-adbkey.pub'
        blob,fingerprints=install.validate_keys(public)
        self.assertEqual(len(fingerprints),1); self.assertIn(b'=',blob)
        with tempfile.TemporaryDirectory() as work:
            private=Path(work)/'private'; private.write_text('-----BEGIN PRIVATE KEY-----')
            with self.assertRaises(ValueError): install.validate_keys(private)

    def test_usb_setup_loads_before_selecting_template(self):
        commands=[]
        def run(argv,**kwargs): commands.append(argv)
        session.configure_usb(run,lambda *a,**k:'-1\n')
        self.assertEqual(commands,[['/sbin/kldload','-n','usb_template'],['/sbin/kldload','-n','/boot/modules/daos_adb_template.ko'],['/sbin/sysctl','hw.usb.template=55813']])
        commands=[]
        session.configure_usb(run,lambda *a,**k:'55813\n')
        self.assertEqual(len(commands),2)

    def test_supervisor_reconnects_and_sigterm_stops_waiting_helper(self):
        session.STOP.clear()
        with tempfile.TemporaryDirectory(prefix='daos-usb-supervise-') as work:
            marker=Path(work)/'starts'
            code='import pathlib,time; p=pathlib.Path('+repr(str(marker))+'); p.open("a").write("start\\n"); time.sleep(.1)'
            logger=logging.getLogger('usb-test'); logger.addHandler(logging.NullHandler())
            thread=threading.Thread(target=session.serve,args=([sys.executable,'-B','-c',code],logger),kwargs={'retry':.05})
            thread.start()
            deadline=time.monotonic()+4
            while time.monotonic()<deadline and (not marker.exists() or len(marker.read_text().splitlines())<2): time.sleep(.05)
            self.assertTrue(marker.exists()); self.assertGreaterEqual(len(marker.read_text().splitlines()),2)
            session.STOP.set(); thread.join(3); self.assertFalse(thread.is_alive())
        session.STOP.clear()

    def test_runtime_command_is_stdio_auth_only(self):
        command=session.helper_command({'format':'DaOS-USB-ADB-session-v1','vid':65535,'pid':55813,
            'authorized_keys':'/etc/daos/adb_keys','daemon_root':'/usr/local/libexec/daos-adb'})
        self.assertIn('--wait',command); self.assertIn('--stdio',command); self.assertIn('--authorized-keys',command)
        self.assertNotIn('--listen',command)

    def test_target_architecture_guard(self):
        with patch.object(session.platform,'system',return_value='FreeBSD'),patch.object(session.os,'geteuid',return_value=0):
            session.target_check(lambda *a,**k:'aarch64\n')
            with self.assertRaises(RuntimeError): session.target_check(lambda *a,**k:'amd64\n')

if __name__=='__main__': unittest.main(verbosity=2)
