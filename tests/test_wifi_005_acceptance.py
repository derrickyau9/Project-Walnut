"""005 acceptance gates; uses no board or network."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
MODULES=ROOT/'system/freebsd/raspberrypi'
spec=importlib.util.spec_from_file_location('deploy_wifi_005',MODULES/'deploy_wifi_005.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)
previous=sys.modules.get('deploy_wifi_005');sys.modules['deploy_wifi_005']=deploy
try:
    spec=importlib.util.spec_from_file_location('wifi005_acceptance',MODULES/'verify_wifi_005.py')
    verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)
finally:
    if previous is None:sys.modules.pop('deploy_wifi_005',None)
    else:sys.modules['deploy_wifi_005']=previous

class WifiAcceptanceTests(unittest.TestCase):
    def test_wrong_running_candidate_stops_before_network_storage_or_report(self):
        with patch.object(verify.sys,'argv',['verify_wifi_005.py']),patch.object(verify,'native'),patch.object(verify,'require_running_trial',side_effect=ValueError('wrong candidate')),patch.object(verify,'run') as commands,patch.object(verify,'atomic') as save:
            with self.assertRaisesRegex(ValueError,'wrong candidate'):verify.main()
        commands.assert_not_called();save.assert_not_called()
    def test_boot_storage_failures_are_detected_without_rejecting_normal_read_only_root(self):
        import re
        failures=['g_vfs_done():gpt/DaOS-root[READ(offset=1390882816,length=4096)]error=5','sdhci_bcm1: Controller timeout','UFS write error','open: Input/output error']
        for text in failures:
            with self.subTest(text=text):self.assertIsNotNone(re.search(verify.STORAGE_ERROR_PATTERN,text))
        self.assertIsNone(re.search(verify.STORAGE_ERROR_PATTERN,'eval: cannot create /etc/hostid: Read-only file system'))
    def associated(self,*,address='192.168.137.10',lease_address='192.168.137.10',key='WPA2-PSK',state='COMPLETED'):
        lease=SimpleNamespace(is_file=lambda:True,is_symlink=lambda:False,stat=lambda:SimpleNamespace(st_size=100),read_text=lambda **_: 'lease { fixed-address '+lease_address+'; }')
        class LeaseParent:
            def __truediv__(self,name):return lease
        with patch.object(verify,'status',return_value={'wpa_state':state,'key_mgmt':key}),patch.object(verify,'run',return_value='inet '+address+' netmask 0xffffff00'),patch.object(verify,'Path',return_value=LeaseParent()):return verify.associated('wlan0')
    def test_wpa2_requires_matching_dhcp_lease_and_usable_address(self):
        self.assertTrue(self.associated())
        self.assertFalse(self.associated(lease_address='192.168.137.11'))
        self.assertFalse(self.associated(address='169.254.1.4',lease_address='169.254.1.4'))
        self.assertFalse(self.associated(key='WPA-PSK'))
        self.assertFalse(self.associated(state='DISCONNECTED'))

if __name__=='__main__':unittest.main()
