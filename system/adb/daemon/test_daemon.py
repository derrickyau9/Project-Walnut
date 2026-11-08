"""Protocol and authorization guards. No USB device or unrelated adb server."""
from __future__ import annotations

import base64
import argparse
import contextlib
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from daemon.auth import PublicKey, load_authorized_keys
from daemon.protocol import COMMANDS, Disconnected, MAX_PAYLOAD, ProtocolError, Transport, VERSION
from daemon.server import Connection, parse_listen
from daemon.server import service_allowed
from daemon.shell_service import parse_service
from daemon.sync_service import SyncService


ADB = shutil.which('adb') or '/home/dy09/daos-adb-development/tools/platform-tools/adb'

class DeveloperPolicy(unittest.TestCase):
    def test_daily_camera_protocol_survives_developer_shell_disabled(self):
        with mock.patch('daemon.server.developer_access',return_value=False):
            self.assertTrue(service_allowed('exec:/usr/local/bin/python3.12 -B /usr/local/libexec/DaOS-camera-link.py'))
            for destination in ('sync:','shell:','shell,v2,raw:cat /etc/master.passwd','exec:/bin/sh',
                                'exec:/usr/local/bin/python3.12 -B /usr/local/libexec/DaOS-camera-link.py; id'):
                self.assertFalse(service_allowed(destination))


class FramingGuards(unittest.TestCase):
    def pair(self):
        left, right = socket.socketpair()
        client, server = Transport(left.fileno(), left.fileno(), left), Transport(right.fileno(), right.fileno(), right)
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        return client, server

    def test_fragmented_header_and_payload_are_reassembled(self):
        client, server = self.pair()
        payload = b'fragmented\x00payload'
        cmd = COMMANDS['WRTE']
        data = struct.pack('<6I', cmd, 7, 3, len(payload), sum(payload), cmd ^ 0xffffffff) + payload
        def send():
            for offset in range(0, len(data), 3):
                client._write_all(data[offset:offset + 3])
        worker = threading.Thread(target=send)
        worker.start()
        self.assertEqual(server.receive(VERSION), ('WRTE', 7, 3, payload))
        worker.join(timeout=2)

    def test_corrupt_magic_oversized_payload_and_unknown_command_close_protocol(self):
        for word, length, magic in ((COMMANDS['OPEN'], MAX_PAYLOAD + 1, COMMANDS['OPEN'] ^ 0xffffffff),
                                    (COMMANDS['OPEN'], 0, 0), (0x12345678, 0, 0x12345678 ^ 0xffffffff)):
            client, server = self.pair()
            client._write_all(struct.pack('<6I', word, 0, 0, length, 0, magic))
            with self.assertRaises(ProtocolError):
                server.receive(VERSION)

    def test_legacy_checksum_is_additive_and_validated(self):
        client, server = self.pair()
        cmd = COMMANDS['AUTH']
        payload = b'abc'
        client._write_all(struct.pack('<6I', cmd, 1, 0, len(payload), 123, cmd ^ 0xffffffff) + payload)
        with self.assertRaisesRegex(ProtocolError, 'checksum'):
            server.receive(0x01000000)

    def test_listener_rejects_non_loopback_or_implicit_hosts(self):
        self.assertEqual(parse_listen('127.0.0.1:0'), ('127.0.0.1', 0))
        for address in ('0.0.0.0:5555', ':5555', 'localhost:5555', '127.0.0.1:70000'):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_listen(address)

    def test_shell_options_are_bounded_and_unknown_services_are_rejected(self):
        self.assertTrue(parse_service('shell,v2,TERM=xterm-256color,pty:')['pty'])
        self.assertFalse(parse_service('shell,v2,raw:cat')['pty'])
        for service in ('tcp:22', 'root:', 'shell,v2,raw,pty:echo bad', 'shell,v2,TERM=x\nBAD:echo bad'):
            with self.assertRaises(ValueError):
                parse_service(service)

    def test_sync_root_rejects_parent_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory(prefix='daos-sync-test-') as temporary:
            root = Path(temporary) / 'root'
            root.mkdir()
            outside = Path(temporary) / 'outside'
            outside.write_text('keep outside')
            (root / 'redirect').symlink_to(outside)
            service = SyncService(None, root)
            self.assertEqual(service.path(b'/normal/file.bin'), root / 'normal/file.bin')
            for value in (b'../outside', b'/redirect'):
                with self.assertRaises(ValueError):
                    service.path(value)
            self.assertEqual(outside.read_text(), 'keep outside')


@unittest.skipUnless(Path(ADB).is_file() and shutil.which('openssl'), 'official adb and openssl required for RSA guard tests')
class AuthorizationGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='daos-adb-auth-test-')
        cls.root = Path(cls.temporary.name)
        cls.key = cls.root / 'host-key'
        subprocess.run([ADB, 'keygen', str(cls.key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.key.chmod(0o600)
        cls.authorized = load_authorized_keys(str(cls.key) + '.pub')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def signature(self, token):
        return subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key),
                               '-pkeyopt', 'digest:sha1'], input=token, capture_output=True, check=True).stdout

    def connection(self):
        left, right = socket.socketpair()
        client = Transport(left.fileno(), left.fileno(), left)
        device = Connection(Transport(right.fileno(), right.fileno(), right), self.authorized)
        device.log = lambda *args, **kwargs: None
        worker = threading.Thread(target=device.run)
        worker.start()
        def clean():
            client.close()
            device.transport.close()
            worker.join(timeout=4)
            self.assertFalse(worker.is_alive())
        self.addCleanup(clean)
        client.send('CNXN', VERSION, MAX_PAYLOAD, b'host::features=shell_v2;\0')
        challenge = client.receive(VERSION)
        self.assertEqual(challenge[:3], ('AUTH', 1, 0))
        self.assertEqual(len(challenge[3]), 20)
        return client, device, challenge[3]

    def test_real_android_public_key_and_pkcs1_sha1_digest_signature(self):
        token = os.urandom(20)
        signature = self.signature(token)
        self.assertTrue(self.authorized[0].verify(token, signature))
        self.assertFalse(self.authorized[0].verify(bytes([token[0] ^ 1]) + token[1:], signature))
        self.assertFalse(self.authorized[0].verify(token, signature[:-1]))
        self.assertFalse(self.authorized[0].verify(token, b'\xff' * 256))

    def test_windows_nul_terminated_public_key_format_is_supported(self):
        line = Path(str(self.key) + '.pub').read_text()
        key, _, comment = line.partition(' ')
        compatible = PublicKey.parse(key + '\0 ' + comment)
        self.assertEqual(compatible.fingerprint, self.authorized[0].fingerprint)

    def test_invalid_public_key_montgomery_metadata_is_rejected(self):
        raw = bytearray(base64.b64decode(Path(str(self.key) + '.pub').read_text().split()[0]))
        raw[4] ^= 1
        with self.assertRaisesRegex(ValueError, 'Montgomery'):
            PublicKey(bytes(raw))

    def test_authenticated_host_receives_connect_and_unknown_service_is_closed(self):
        client, device, token = self.connection()
        client.send('AUTH', 2, 0, self.signature(token))
        connected = client.receive(VERSION)
        self.assertEqual(connected[0], 'CNXN')
        self.assertIn(b'features=shell_v2', connected[3])
        self.assertTrue(device.authenticated)
        client.send('OPEN', 5, 0, b'tcp:22\0')
        self.assertEqual(client.receive(VERSION), ('CLSE', 0, 5, b''))

    def test_shell_before_authorization_never_spawns_a_process(self):
        with mock.patch('daemon.shell_service.subprocess.Popen') as process:
            client, device, token = self.connection()
            client.send('OPEN', 5, 0, b'shell:echo MUST_NOT_RUN\0')
            with self.assertRaises((Disconnected, OSError)):
                client.receive(VERSION)
            self.assertFalse(device.authenticated)
            process.assert_not_called()

    def test_received_public_key_is_not_automatically_enrolled(self):
        client, device, token = self.connection()
        client.send('AUTH', 3, 0, Path(str(self.key) + '.pub').read_bytes() + b'\0')
        next_token = client.receive(VERSION)
        self.assertEqual(next_token[:3], ('AUTH', 1, 0))
        self.assertNotEqual(next_token[3], token)
        self.assertFalse(device.authenticated)

    def test_unacknowledged_second_write_closes_transport(self):
        class DeliberatelyUnreadService:
            def __init__(self, stream, *args):
                self.stream = stream
            def run(self):
                self.stream.closed.wait(3)
            def cleanup(self):
                pass
        with mock.patch('daemon.server.ShellService', DeliberatelyUnreadService):
            client, device, token = self.connection()
            client.send('AUTH', 2, 0, self.signature(token))
            self.assertEqual(client.receive(VERSION)[0], 'CNXN')
            client.send('OPEN', 9, 0, b'shell,v2,raw:cat\0')
            ready = client.receive(VERSION)
            self.assertEqual(ready[0], 'OKAY')
            local = ready[1]
            client.send('WRTE', 9, local, b'first')
            client.send('WRTE', 9, local, b'no credit for this write')
            with self.assertRaises((Disconnected, OSError)):
                client.receive(VERSION)

    def test_stdio_sigterm_reaps_a_live_shell(self):
        with tempfile.TemporaryFile('w+b') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('daos_adbd.py')),
                                      '--stdio', '--authorized-keys', str(self.key) + '.pub'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0)
            client = Transport(child.stdout.fileno(), child.stdin.fileno())
            try:
                client.send('CNXN', VERSION, MAX_PAYLOAD, b'host::features=shell_v2;\0')
                token = client.receive(VERSION)[3]
                client.send('AUTH', 2, 0, self.signature(token))
                self.assertEqual(client.receive(VERSION)[0], 'CNXN')
                client.send('OPEN', 2, 0, b'shell,v2,raw:echo $$; exec sleep 60\0')
                ready = client.receive(VERSION)
                self.assertEqual(ready[0], 'OKAY')
                output = client.receive(VERSION)
                self.assertEqual(output[0], 'WRTE')
                kind, length = struct.unpack_from('<BI', output[3])
                self.assertEqual(kind, 1)
                shell_pid = int(output[3][5:5 + length].strip())
                client.send('OKAY', 2, ready[1])
                child.terminate()
                self.assertEqual(child.wait(timeout=5), 0)
                with self.assertRaises(ProcessLookupError):
                    os.kill(shell_pid, 0)
            finally:
                client.close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdin.close()
                child.stdout.close()


if __name__ == '__main__':
    unittest.main()
