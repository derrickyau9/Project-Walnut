"""Actual headless Edge/native HTTP round-6 UI smoke; no screenshots are taken."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edge', default=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    parser.add_argument('--native-daemon', default=str(ROOT / 'build/native/Release/hf-camerad.exe'))
    args = parser.parse_args()
    if not Path(args.edge).is_file() or not Path(args.native_daemon).is_file() or importlib.util.find_spec('websocket') is None:
        print('SKIP: Edge, built native authority, and websocket-client are needed')
        return 0
    import websocket
    from camera.camerad.native_service import NativeService
    from camera.camerad.server import BoundedHTTPServer, handler_for
    from daos.capability_runtime.recipes import loads_recipe
    with tempfile.TemporaryDirectory(prefix='hf-round6-browser-', ignore_cleanup_errors=True) as temporary:
        base = Path(temporary)
        executable = base / 'hf-camerad.exe'
        shutil.copy2(args.native_daemon, executable)  # Keep the build output unlocked.
        service = NativeService(base / 'authority', command=[str(executable)])
        handler = handler_for(service)
        handler.log_message = lambda *unused: None
        server = BoundedHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        downloads = base / 'downloads'
        downloads.mkdir()
        url = f'http://127.0.0.1:{server.server_port}/'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            debug_port = probe.getsockname()[1]
        edge = subprocess.Popen([args.edge, '--headless', '--disable-gpu', '--no-first-run',
            f'--remote-debugging-port={debug_port}', '--remote-allow-origins=*',
            '--user-data-dir=' + str(base / 'edge-profile'), url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        try:
            for _ in range(100):
                try:
                    with urlopen(f'http://127.0.0.1:{debug_port}/json', timeout=1) as response:
                        pages = json.load(response)
                    tab = next(value for value in pages if value.get('type') == 'page' and value.get('url', '').startswith(url))
                    break
                except Exception:
                    time.sleep(.1)
            else:
                raise AssertionError('Edge page did not start')
            ws = websocket.create_connection(tab['webSocketDebuggerUrl'], timeout=10)
            sequence = 0
            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                ws.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
                while True:
                    result = json.loads(ws.recv())
                    if result.get('id') == sequence:
                        if 'error' in result: raise AssertionError(result['error'])
                        return result['result']
            def js(expression):
                value = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'userGesture': True})
                if 'exceptionDetails' in value: raise AssertionError(value['exceptionDetails'])
                return value['result'].get('value')
            def wait(expression, timeout=15):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if js(expression): return
                    time.sleep(.06)
                raise AssertionError('Timed out: ' + expression)
            def wait_file(path):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if path.is_file() and not list(downloads.glob('*.crdownload')): return
                    time.sleep(.06)
                raise AssertionError('Download did not complete')
            call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(downloads)})
            wait("!document.getElementById('acquire').disabled")
            js("document.getElementById('acquire').click()")
            wait("!document.getElementById('capture').disabled")
            js("document.getElementById('capture').click()")
            wait("document.getElementById('capture-status').textContent==='COMPLETE'")
            request_id = js("document.getElementById('request-id').textContent")
            epoch = service.get_state()['control']['epoch']
            js("document.getElementById('library-refresh').click()")
            wait("document.querySelectorAll('#library-items button').length===1 && !document.querySelector('#library-items button').disabled")
            js("document.querySelector('#library-items button').click()")
            wait("!document.getElementById('library-image').hidden && document.getElementById('library-image').naturalWidth>0")
            assert 'not live preview' in js("document.getElementById('library-message').textContent")
            assert js("document.getElementById('request-id').textContent") == request_id
            js("document.getElementById('library-download').click()")
            copy = downloads / (request_id + '.dng')
            wait_file(copy)
            source = service.photos / (request_id + '.dng')
            assert hashlib.sha256(copy.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
            assert service.get_state()['control']['epoch'] == epoch
            js("document.getElementById('release').click()")
            wait("document.getElementById('owner').textContent==='Unowned'")
            recipe_path = base / 'recipe.json'
            def import_recipe(name, exposures, interval):
                recipe = {'format': 'hasslefree.workflow-recipe.v1', 'name': name,
                          'plan': {'kind': 'interval', 'exposures_us': exposures, 'interval_ms': interval,
                                   'sensor_mode': 'sim-small', 'requested_analogue_gain': 1.0, 'output_format': 'dng'}}
                recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
                document = call('DOM.getDocument')['root']['nodeId']
                node = call('DOM.querySelector', {'nodeId': document, 'selector': '#recipe-file'})['nodeId']
                js("document.getElementById('recipe-message').textContent='Awaiting import'")
                call('DOM.setFileInputFiles', {'nodeId': node, 'files': [str(recipe_path)]})
                wait("document.getElementById('recipe-message').textContent.startsWith('Recipe loaded')")
                return recipe
            recipe = import_recipe('Round6 interval', [5000, 10000], 250)
            assert js("document.getElementById('workflow-status').textContent") == 'NO JOB'
            assert js("document.getElementById('workflow-kind').value") == 'interval'
            js("document.getElementById('recipe-export').click()")
            saved_recipe = downloads / 'Round6-interval.json'
            wait_file(saved_recipe)
            assert loads_recipe(saved_recipe.read_bytes()) == loads_recipe(json.dumps(recipe))
            assert 'token' not in saved_recipe.read_text() and 'job_id' not in saved_recipe.read_text()
            js("document.getElementById('workflow-form').requestSubmit()")
            wait("document.getElementById('workflow-status').textContent==='COMPLETED'")
            first_job = js("document.getElementById('workflow-id').value")
            js("document.getElementById('workflow-list-refresh').click()")
            wait("[...document.getElementById('workflow-recent').options].some(value=>value.value===" + json.dumps(first_job) + ")")
            js("document.getElementById('workflow-recent').value=" + json.dumps(first_job) + ";document.getElementById('workflow-list-open').click()")
            wait("document.getElementById('workflow-details').textContent.includes(" + json.dumps(first_job) + ")")
            import_recipe('Round6 pause', [5000, 5000, 5000], 1500)
            js("document.getElementById('workflow-form').requestSubmit()")
            wait("document.getElementById('workflow-status').textContent==='RUNNING' && JSON.parse(document.getElementById('workflow-details').textContent).steps.some(step=>step.status==='completed')")
            pause_job = js("document.getElementById('workflow-id').value")
            assert pause_job != first_job
            js("document.getElementById('workflow-pause').click()")
            wait("document.getElementById('workflow-status').textContent==='PAUSED'")
            paused = json.loads(js("document.getElementById('workflow-details').textContent"))
            assert len(paused['steps']) == 1
            js("document.getElementById('workflow-resume').click()")
            wait("document.getElementById('workflow-status').textContent==='COMPLETED'", timeout=20)
            import_recipe('Round6 cancel', [5000, 5000, 5000], 5000)
            js("document.getElementById('workflow-form').requestSubmit()")
            wait("document.getElementById('workflow-status').textContent==='RUNNING' && JSON.parse(document.getElementById('workflow-details').textContent).steps.some(step=>step.status==='completed')")
            js("document.getElementById('workflow-cancel').click()")
            wait("document.getElementById('workflow-status').textContent==='CANCELLED'")
            cancelled = json.loads(js("document.getElementById('workflow-details').textContent"))
            assert len(cancelled['steps']) == 1
            call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': True})
            dimensions = js('({viewport:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
            assert dimensions == {'viewport': 390, 'scroll': 390}, dimensions
            assert len(service.list_captures(limit=20)['items']) == 7
            assert service.get_state()['control'] is None
            print('PASS: actual native browser retained-DNG review/download, portable recipe import/export, recent jobs, pause/resume/cancel, and 390px layout; no screenshots captured')
        finally:
            if ws: ws.close()
            edge.terminate()
            try: edge.wait(timeout=10)
            except subprocess.TimeoutExpired:
                edge.kill(); edge.wait(timeout=5)
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            try: service.close()
            except Exception: service.abort()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
