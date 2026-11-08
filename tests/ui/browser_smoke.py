"""Optional live browser smoke test for HF-Sim, using Edge and websocket-client.

Run from the project root: python tests/ui/browser_smoke.py
Optional: --edge PATH --screenshot PATH. The test uses temporary data and ports.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--edge', default=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    parser.add_argument('--screenshot', help='Optional path for a 390 px mobile screenshot')
    args = parser.parse_args()
    if not Path(args.edge).is_file() or importlib.util.find_spec('websocket') is None:
        print('SKIP: install websocket-client and provide a local Edge executable with --edge')
        return 0

    import websocket
    from camera.camerad.service import CameraService
    from camera.camerad.server import BoundedHTTPServer, handler_for

    with tempfile.TemporaryDirectory(prefix='hf-ui-test-', ignore_cleanup_errors=True) as temp:
        service = CameraService(Path(temp) / 'data', lease_seconds=12)
        server = BoundedHTTPServer(('127.0.0.1', 0), handler_for(service))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            debug_port = probe.getsockname()[1]
        edge = subprocess.Popen([
            args.edge, '--headless', '--disable-gpu', '--no-first-run',
            f'--remote-debugging-port={debug_port}', '--remote-allow-origins=*',
            '--user-data-dir=' + str(Path(temp) / 'edge'), url,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        try:
            for _ in range(100):
                try:
                    with urlopen(f'http://127.0.0.1:{debug_port}/json', timeout=1) as response:
                        pages = json.load(response)
                    tab = next(x for x in pages if x.get('type') == 'page' and x.get('url', '').startswith(url))
                    break
                except Exception:
                    time.sleep(.1)
            else:
                raise AssertionError('Edge page did not start')
            ws = websocket.create_connection(tab['webSocketDebuggerUrl'], timeout=5)
            sequence = 0

            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                request_id = sequence
                ws.send(json.dumps({'id': request_id, 'method': method, 'params': params or {}}))
                while True:
                    response = json.loads(ws.recv())
                    if response.get('id') == request_id:
                        if 'error' in response:
                            raise AssertionError(response['error'])
                        return response['result']

            def js(expression):
                result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
                if 'exceptionDetails' in result:
                    raise AssertionError(result['exceptionDetails'])
                return result['result'].get('value')

            def wait(expression, timeout=12):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        if js(expression):
                            return
                    except (AssertionError, KeyError, TypeError):
                        pass  # Page may be navigating after reload.
                    time.sleep(.1)
                raise AssertionError('Timed out: ' + expression)

            wait("!document.getElementById('acquire').disabled")
            js("document.getElementById('acquire').click()")
            wait("!document.getElementById('capture').disabled")
            call('Page.reload')
            wait("document.readyState==='complete' && !document.getElementById('capture').disabled")
            js("document.getElementById('preview-start').click()")
            wait("!document.getElementById('preview-image').hidden")
            js("document.getElementById('fault').value='delay';document.getElementById('capture').click()")
            wait("document.getElementById('preview-message').textContent==='Preview paused'")
            assert js("document.getElementById('preview-image').hidden")
            wait("document.getElementById('capture-status').textContent==='COMPLETE'")
            assert js("document.getElementById('actual-value').textContent") == '10000 µs'
            assert js("document.getElementById('file-path').textContent").endswith('.dng')
            wait("!document.getElementById('preview-image').hidden")
            js("document.getElementById('fault').value='storage_full';document.getElementById('capture').click()")
            wait("document.getElementById('capture-status').textContent==='FAILED'")
            assert 'storage_full' in js("document.getElementById('result-message').textContent")
            time.sleep(13)  # Exceeds the injected 12-second lease; heartbeat must renew it.
            assert service.get_state()['control'] is not None
            js("document.getElementById('fault').value='disconnect';document.getElementById('capture').click()")
            wait("document.getElementById('device-state').textContent==='Disconnected'")
            assert js("document.getElementById('capture').disabled")
            js("document.getElementById('release').click()")
            wait("document.getElementById('owner').textContent==='Unowned'")
            wait("!document.getElementById('acquire').disabled")
            js("document.getElementById('acquire').click()")
            wait("!document.getElementById('reconnect').disabled")
            js("document.getElementById('reconnect').click()")
            wait("document.getElementById('device-state').textContent==='idle'")
            print('PASS: simulated disconnect, release/acquire offline, and reconnect')
            js("document.getElementById('preview-stop').click()")
            wait("document.getElementById('preview-image').hidden")
            js("document.getElementById('release').click()")
            wait("document.getElementById('owner').textContent==='Unowned'")
            assert not js("sessionStorage.getItem('hasslefree-console-control-v1')")
            js("document.getElementById('workflow-form').requestSubmit()")
            wait("document.getElementById('workflow-status').textContent==='COMPLETED'")
            call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': True})
            dimensions = js('({viewport:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
            assert dimensions == {'viewport': 390, 'scroll': 390}, dimensions
            if args.screenshot:
                path = Path(args.screenshot)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(call('Page.captureScreenshot', {'format': 'png'})['data']))
            print('PASS: reload/control, preview, capture, injected faults, lease heartbeat, release, workflow and 390px width')
        finally:
            if ws:
                ws.close()
            edge.terminate()
            edge.wait(timeout=10)
            server.shutdown()
            server.server_close()
            service.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
