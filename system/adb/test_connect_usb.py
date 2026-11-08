"""Host client selection/server isolation tests. Never connects a USB device."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import connect_usb as client


class ClientGuards(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / 'build/DaOS_USB_Development/host-tools/unit-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='client-', dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_device_parser_never_selects_phone_or_tcp_transport(self):
        output = ('List of devices attached\n'
                  'PHONE_123 device product:redfin model:Pixel_5 device:redfin transport_id:1\n'
                  '127.0.0.1:5555 device product:DaOS model:DaOS_BSD device:daos transport_id:2\n'
                  'DaOS-USB-ADB-DEVELOPMENT device usb:1-2 product:DaOS model:DaOS_BSD device:daos transport_id:3\n')
        selected = client.daos_devices(client.parse_devices(output), client.DEFAULT_SERIAL)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['transport_id'], '3')

    def test_ready_device_requires_daos_metadata_and_exact_serial(self):
        values = [{'serial': client.DEFAULT_SERIAL, 'state': 'device', 'model': 'Pixel'},
                  {'serial': 'DaOS-other', 'state': 'device', 'model': 'DaOS_BSD'},
                  {'serial': client.DEFAULT_SERIAL, 'state': 'unauthorized'}]
        self.assertEqual(client.daos_devices(values, client.DEFAULT_SERIAL), [values[2]])

    def test_multiple_daos_devices_require_explicit_selection(self):
        devices = [{'serial': 'DaOS-one', 'state': 'device', 'transport_id': '11'},
                   {'serial': 'DaOS-two', 'state': 'device', 'transport_id': '12'}]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'Multiple DaOS'):
                client.select_device(devices, interactive=False)
        self.assertEqual(client.select_device(devices, selection=2)['transport_id'], '12')
        with self.assertRaises(ValueError):
            client.select_device(devices[:1], selection=2)

    def test_standard_adb_port_is_rejected_before_loading_keys(self):
        with mock.patch.object(client, 'key_environment') as keys:
            with self.assertRaisesRegex(ValueError, 'excluding 5037'):
                client.ensure_server(self.root, self.root / 'adb', 5037, client.DEFAULT_SERIAL)
            keys.assert_not_called()

    def test_unowned_server_port_is_not_reused_or_stopped(self):
        with mock.patch.object(client, 'key_environment', return_value=({}, 'public-fingerprint')), \
             mock.patch.object(client, 'socket_open', return_value=True), \
             mock.patch.object(client.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'unrecorded server'):
                client.ensure_server(self.root, self.root / 'adb', 5059, client.DEFAULT_SERIAL)
            spawn.assert_not_called()

    def test_changed_process_identity_is_not_trusted(self):
        state = self.root / 'state'
        state.mkdir()
        fake_adb = self.root / 'adb'
        fake_adb.write_text('fixture')
        receipt = {'pid': 123456, 'process': {'pid': 123456, 'start_ticks': 1, 'executable': str(fake_adb)},
                   'serial_filter': client.DEFAULT_SERIAL, 'authorized_public_fingerprint': 'fingerprint'}
        (state / 'server-5059.json').write_text(json.dumps(receipt))
        actual = dict(receipt['process'], start_ticks=2)
        with mock.patch.object(client, 'key_environment', return_value=({}, 'fingerprint')), \
             mock.patch.object(client, 'process_stamp', return_value=actual), \
             mock.patch.object(client.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'another key/device configuration'):
                client.ensure_server(self.root, fake_adb, 5059, client.DEFAULT_SERIAL)
            spawn.assert_not_called()

    def test_foreign_serial_is_rejected_before_any_adb_command(self):
        with mock.patch.object(sys, 'argv', ['connect_usb.py', '--serial', 'PHONE_123']), \
             mock.patch.object(client, 'ensure_server') as server, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                client.main()
            server.assert_not_called()


if __name__ == '__main__':
    unittest.main()
