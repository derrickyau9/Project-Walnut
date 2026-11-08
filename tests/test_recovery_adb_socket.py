"""Existing ADB server borrowing never starts, kills or replaces a server."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
wire=load('recovery_adb_socket',ROOT/'system/adb/recovery/adb_socket.py')
host=load('recovery_socket_host',ROOT/'system/adb/recovery/host.py')
base=host.load_connector()
READY=host.SERIAL+' device product:DaOS model:DaOS device:DaOS transport_id:12\n'
OFFLINE=host.SERIAL+' offline transport_id:12\n'


class Socket:
    def __init__(self,data):self.data=bytearray(data);self.sent=[]
    def recv(self,count):data=bytes(self.data[:count]);del self.data[:count];return data
    def sendall(self,data):self.sent.append(bytes(data))
    def close(self):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass


def frame(kind,data):return struct.pack('<BI',kind,len(data))+data


class SmartSocketTests(unittest.TestCase):
    def client(self):
        client=wire.Client(5037,host.SERIAL);client.bind({'serial':host.SERIAL,'state':'device','transport_id':'12'});return client
    def test_inventory_uses_only_readonly_devices_service(self):
        data=READY.encode();sock=Socket(b'OKAY'+('%04x'%len(data)).encode()+data);client=self.client()
        with patch.object(client,'connect',return_value=sock):self.assertEqual(client.devices(),READY)
        self.assertEqual(sock.sent,[b'000ehost:devices-l'])
    def test_shell_v2_pins_transport_and_demultiplexes_exit_stdout_stderr(self):
        sock=Socket(b'OKAYOKAY'+frame(1,b'{"ok":true}\n')+frame(2,b'notice')+frame(3,b'\0'));client=self.client()
        with patch.object(client,'connect',return_value=sock):result=client.shell('fixed command')
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,'{"ok":true}\n');self.assertEqual(result.stderr,'notice')
        self.assertEqual(sock.sent[0],b'0014host:transport-id:12')
        self.assertIn(b'shell,v2,raw:fixed command',sock.sent[1])
        self.assertEqual(sock.sent[2],frame(4,b''))
    def test_reboot_disconnect_preserves_receipt_and_does_not_retry(self):
        receipt=b'{"status":"RAM-rescue-nextboot-armed","reboot_requested":true}\n'
        sock=Socket(b'OKAYOKAY'+frame(1,receipt));client=self.client()
        with patch.object(client,'connect',return_value=sock) as connect:result=client.shell('request reboot')
        connect.assert_called_once();self.assertEqual(result.returncode,1);self.assertEqual(result.stdout,receipt.decode());self.assertIn('disconnected',result.stderr)
    def test_sync_v1_push_is_bounded_to64k_records_and_never_shells_out(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'chunk.bin';source.write_bytes(b'X'*(16*1024**2))
            sock=Socket(b'OKAYOKAY'+b'OKAY'+struct.pack('<I',0));client=self.client()
            with patch.object(client,'connect',return_value=sock),patch.object(wire.subprocess,'run',side_effect=AssertionError('must not execute adb')):
                self.assertEqual(client.push(source,'/DaOS-recovery-ram/flash/incoming/test.bin').returncode,0)
        records=[p for p in sock.sent if p.startswith(b'DATA')]
        self.assertEqual(sum(struct.unpack('<I',p[4:8])[0] for p in records),16*1024**2)
        self.assertTrue(all(len(p)<=65536+8 for p in records));self.assertEqual(sock.sent[-1],b'QUIT'+struct.pack('<I',0))
    def test_sync_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'chunk';source.write_bytes(b'X')
            sock=Socket(b'OKAYOKAYFAIL'+struct.pack('<I',5)+b'error');client=self.client()
            with patch.object(client,'connect',return_value=sock),self.assertRaisesRegex(ConnectionError,'error'):client.push(source,'/tmp/chunk')
    def test_offline_or_other_serial_cannot_be_bound(self):
        client=self.client()
        for d in ({'serial':host.SERIAL,'state':'offline','transport_id':'12'},{'serial':'phone','state':'device','transport_id':'12'}):
            with self.subTest(d=d),self.assertRaises(ValueError):client.bind(d)
    def test_connector_refuses_lifecycle_commands_and_transport_changes(self):
        client=self.client();adapter=wire.Connector(base,client)
        for args in (('kill-server',),('start-server',),('-t','13','get-state')):
            with self.subTest(args=args),self.assertRaises(ValueError):adapter.adb_call(None,5037,{},*args)
        with patch.object(client,'devices',return_value=READY.replace('transport_id:12','transport_id:13')),self.assertRaises(ConnectionError):adapter.adb_call(None,5037,{},'-t','12','get-state')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-adb-discovery-');self.addCleanup(self.temp.cleanup);self.tools=Path(self.temp.name)
    def sockets(self,inventories):
        clients={}
        def make(port,serial):
            if port not in clients:
                client=Mock();client.port=port
                if port in inventories:client.devices.return_value=inventories[port]
                else:client.devices.side_effect=ConnectionRefusedError('not listening')
                clients[port]=client
            return clients[port]
        return SimpleNamespace(Client=Mock(side_effect=make)),clients
    def test_default_reuses_authenticated_global_without_any_process_or_key_changes(self):
        sockets,clients=self.sockets({5037:READY})
        selected=host.discover_existing_server(base,sockets,self.tools,host.SERIAL)
        self.assertEqual(selected.port,5037)
        self.assertEqual([c.args[0] for c in sockets.Client.call_args_list],[5053,5037])
    def test_project_canonical_is_preferred_before_global(self):
        sockets,_=self.sockets({5053:READY,5037:READY})
        self.assertEqual(host.discover_existing_server(base,sockets,self.tools,host.SERIAL).port,5053)
        self.assertEqual(sockets.Client.call_count,1)
    def test_ready_global_is_preferred_to_offline_project_without_binding_offline(self):
        sockets,_=self.sockets({5053:OFFLINE,5037:READY})
        self.assertEqual(host.discover_existing_server(base,sockets,self.tools,host.SERIAL).port,5037)
    def test_during_reenumeration_existing_server_is_waited_on_without_starting_competitor(self):
        sockets,_=self.sockets({5037:OFFLINE})
        client=host.discover_existing_server(base,sockets,self.tools,host.SERIAL)
        self.assertEqual(client.port,5037);client.bind.assert_not_called()
    def test_explicit_borrow_needs_no_adb_executable_key_environment_or_ensure_server(self):
        client=wire.Client(5037,host.SERIAL)
        with patch.object(host,'load_connector',return_value=base),patch.object(host,'load_socket_client',return_value=wire),patch.object(wire.Client,'devices',return_value=READY),patch.object(base,'ensure_server',side_effect=AssertionError('no lifecycle')),patch.object(base,'key_environment',side_effect=AssertionError('no key changes')):
            usb=host.USB(self.tools,existing_server_port=5037)
        self.assertEqual(usb.port,5037);self.assertEqual(usb.selector,['-t','12']);self.assertIsNotNone(usb.borrowed)

    def test_borrowed_global_also_works_without_any_local_tools_directory(self):
        missing=self.tools/'not-provisioned'
        with patch.object(host,'load_connector',return_value=base),patch.object(host,'load_socket_client',return_value=wire),patch.object(wire.Client,'devices',return_value=READY),patch.object(base,'ensure_server',side_effect=AssertionError('no lifecycle')):
            usb=host.USB(missing,existing_server_port=5037)
        self.assertEqual(usb.port,5037);self.assertFalse(missing.exists())

    def test_automatic_wait_rediscovery_can_follow_ready_transport_on_another_existing_port(self):
        first=wire.Client(5037,host.SERIAL);next_=wire.Client(5053,host.SERIAL)
        def inventory(client):return OFFLINE if client.port==5037 else READY
        with patch.object(host,'load_connector',return_value=base),patch.object(host,'load_socket_client',return_value=wire),patch.object(host,'discover_existing_server',side_effect=[first,next_]),patch.object(wire.Client,'devices',autospec=True,side_effect=inventory),patch.object(host.time,'sleep'),patch.object(base,'ensure_server',side_effect=AssertionError('no lifecycle')):
            usb=host.USB(self.tools)
        self.assertEqual(usb.port,5053);self.assertIs(usb.borrowed,next_);self.assertEqual(usb.selector,['-t','12'])


if __name__=='__main__':unittest.main()
