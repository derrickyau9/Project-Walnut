#!/usr/bin/env python3
"""Portable wire-format checks, not a FreeBSD build or USB enumeration claim."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('descriptors', HERE / 'generate_descriptors.py')
generated = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generated)

class WireTest(unittest.TestCase):
    def test_generated_header_is_current(self):
        self.assertEqual((HERE / 'daos_ms_os.h').read_text(), generated.content())

    def test_os_string_and_compatible_id(self):
        desc = generated.descriptors()
        self.assertEqual(desc['daos_ms_os_string'], b'\x12\x03' + 'MSFT100'.encode('utf-16le') + b'\xda\0')
        blob = desc['daos_ms_compatible_id']
        self.assertEqual(struct.unpack_from('<IHHB', blob), (len(blob), 0x100, 4, 1))
        self.assertEqual(len(blob), 40)
        self.assertEqual(blob[16:26], b'\0\1WINUSB\0\0')
        self.assertEqual(blob[26:], bytes(14))

    def test_guid_multisz_offsets_and_lengths(self):
        blob = generated.descriptors()['daos_ms_extended_properties']
        self.assertEqual(struct.unpack_from('<IHHH', blob), (len(blob), 0x100, 5, 1))
        size, kind, name_size = struct.unpack_from('<IIH', blob, 10)
        self.assertEqual(size, len(blob) - 10)
        self.assertEqual(kind, 7)  # REG_MULTI_SZ
        self.assertEqual(blob[20:20+name_size].decode('utf-16le'), 'DeviceInterfaceGUIDs\0')
        data_size, = struct.unpack_from('<I', blob, 20+name_size)
        value = blob[24+name_size:]
        self.assertEqual(len(value), data_size)
        self.assertEqual(value.decode('utf-16le'), '{F72FE0D4-CBCB-407D-8814-9ED673D0DD6B}\0\0')
        self.assertEqual((len(blob), name_size, data_size), (146, 42, 80))

    def test_actual_c_callback_and_packet_guard(self):
        with tempfile.TemporaryDirectory(prefix='daos-usb-wire-') as work:
            binary = str(Path(work) / 'wire-test')
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I'+str(HERE),
                            str(HERE/'tests/wire_test.c'), '-o', binary], check=True)
            subprocess.run([binary], check=True)

    def test_helper_real_pipes_and_mock_usb_transfers(self):
        with tempfile.TemporaryDirectory(prefix='daos-usb-bridge-') as work:
            work = Path(work)
            binary = str(work / 'mock-bridge')
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror',
                            '-D__aligned(x)=__attribute__((aligned(x)))',
                            '-I'+str(HERE/'upstream/lib/libusb'), str(HERE/'daos_usb_adb.c'),
                            str(HERE/'tests/mock_libusb20.c'), '-o', binary], check=True)
            env = dict(os.environ, MOCK_CAPTURE=str(work/'capture'))
            listed = subprocess.run([binary, '--list'], env=env, capture_output=True, text=True, check=True)
            self.assertIn('device ffff:da05 ff/42/01 IN=81 OUT=01', listed.stdout)
            for rejected in ['MOCK_HOST', 'MOCK_WRONG_INTERFACE', 'MOCK_UNCONFIGURED']:
                result = subprocess.run([binary, '--list'], env=dict(env, **{rejected:'1'}), capture_output=True)
                self.assertEqual(result.returncode, 1, rejected)
                self.assertFalse(result.stdout)
            daemon = work/'daemon.py'
            daemon.write_text('''import os, struct, time
def packet(command, payload):
    command = int.from_bytes(command, 'little')
    return struct.pack('<6I', command, 0, 0, len(payload), 0, command ^ 0xffffffff) + payload
for byte in packet(b'CNXN', b'HELLO') + packet(b'WRTE', b'Z'*(128*1024+7)) + packet(b'OKAY', b''):
    os.write(1, bytes([byte]))
data = b''
while len(data) < 13:
    chunk = os.read(0, 13-len(data))
    if not chunk: raise RuntimeError('lost USB OUT bytes')
    data += chunk
assert data == b'HOST-fragment'
time.sleep(0.2)
''')
            result = subprocess.run([binary, '--', sys.executable, '-B', str(daemon)], env=env,
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)  # child exit returns to supervisor
            blob = (work/'capture').read_bytes()
            transfers = []
            while blob:
                size, = struct.unpack_from('<I', blob)
                transfers.append(blob[4:4+size]); blob = blob[4+size:]
            self.assertEqual([len(t) for t in transfers], [24, 5, 24] + [2048] * 64 + [7, 24])
            self.assertEqual(transfers[0][:4], b'CNXN')
            self.assertEqual(transfers[1], b'HELLO')
            self.assertEqual(transfers[2][:4], b'WRTE')
            self.assertEqual(b''.join(transfers[3:-1]), b'Z'*(128*1024+7))
            self.assertEqual(transfers[-1][:4], b'OKAY')
            self.assertEqual(result.stdout, b'')

    def test_pinned_dwc_fifo_boundaries_need_no_later_host_traffic(self):
        with tempfile.TemporaryDirectory(prefix='daos-usb-dwc-fifo-') as folder:
            work = Path(folder); binary = str(work/'mock-bridge')
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror',
                            '-D__aligned(x)=__attribute__((aligned(x)))',
                            '-I'+str(HERE/'upstream/lib/libusb'), str(HERE/'daos_usb_adb.c'),
                            str(HERE/'tests/mock_libusb20.c'), '-o', binary], check=True)
            for size in (2048, 2049, 2559, 2560, 4097, 65537):
                with self.subTest(payload_bytes=size):
                    capture=work/('capture-'+str(size)); daemon=work/'daemon.py'
                    daemon.write_text('''import os,struct,time
def packet(command,payload):
    command=int.from_bytes(command,'little')
    return struct.pack('<6I',command,0,0,len(payload),0,command^0xffffffff)+payload
data=packet(b'WRTE',b'Q'*'''+str(size)+''')+packet(b'OKAY',b'')
while data:
    count=os.write(1,data);data=data[count:]
received=b''
while len(received)<13:
    received+=os.read(0,13-len(received))
assert received==b'HOST-fragment'
time.sleep(.2)
''')
                    result=subprocess.run([binary,'--',sys.executable,'-B',str(daemon)],
                        env=dict(os.environ,MOCK_CAPTURE=str(capture),MOCK_DWC_FIFO='1'),
                        capture_output=True,timeout=5)
                    self.assertEqual(result.returncode,1)
                    blob=capture.read_bytes();transfers=[]
                    while blob:
                        count,=struct.unpack_from('<I',blob)
                        transfers.append(blob[4:4+count]);blob=blob[4+count:]
                    self.assertEqual(transfers[0][:4],b'WRTE')
                    self.assertEqual(transfers[-1][:4],b'OKAY')
                    self.assertTrue(all(len(piece)<=2048 for piece in transfers[1:-1]))
                    self.assertEqual(b''.join(transfers[1:-1]),b'Q'*size)

if __name__ == '__main__': unittest.main(verbosity=2)
