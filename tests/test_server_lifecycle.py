import unittest
from unittest.mock import patch

from camera.camerad import server
from camera.camerad.service import CameraError


class FakeAuthority:
    def __init__(self, busy=False):
        self.closed = False
        self.aborted = False
        self.busy = busy

    def close(self):
        self.closed = True
        if self.busy:
            raise CameraError('camera_busy', 'injected active work')

    def abort(self):
        self.aborted = True


class ServerLifecycleTests(unittest.TestCase):
    def test_bind_failure_closes_owned_authority(self):
        for busy in (False, True):
            with self.subTest(busy=busy):
                authority = FakeAuthority(busy)
                with patch('sys.argv', ['server', '--port', '8787']), \
                     patch.object(server, 'CameraService', return_value=authority), \
                     patch.object(server, 'handler_for', return_value=object()), \
                     patch.object(server, 'BoundedHTTPServer', side_effect=OSError('injected bind failure')):
                    with self.assertRaises(OSError):
                        server.main()
                self.assertTrue(authority.closed)
                self.assertEqual(authority.aborted, busy)

    def test_invalid_port_rejected_before_starting_authority(self):
        with patch('sys.argv', ['server', '--port', '65536']), \
             patch.object(server, 'CameraService') as constructor, \
             patch.object(server.argparse.ArgumentParser, 'error', side_effect=ValueError('invalid port')):
            with self.assertRaisesRegex(ValueError, 'invalid port'):
                server.main()
            constructor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
