"""HTTP/workflow adapter over the process-isolated native simulator authority.

Only the small preview renderer lives here. Control, epochs, request IDs,
capture, recovery and final file commits are owned by the native process.
"""
from pathlib import Path

from camera.pipeline.preview import PreviewStream
from camera.camerad.service import CameraError


class NativeService:
    def __init__(self, data_dir, *, command=None, lease_seconds=30, timeout_seconds=5):
        from camera.camerad.native_client import NativeClient
        self.root = Path(data_dir).resolve()
        self.photos = self.root / 'photos'
        self.preview = PreviewStream()
        self.client = NativeClient(self.root, command=command, lease_seconds=lease_seconds,
                                   timeout_seconds=timeout_seconds)

    def _call(self, name, *args, **kwargs):
        try:
            return getattr(self.client, name)(*args, **kwargs)
        except Exception as exc:
            if isinstance(exc, CameraError):
                raise
            code = getattr(exc, 'code', None)
            if code:
                raise CameraError(code, str(exc)) from exc
            raise

    def __getattr__(self, name):
        if name not in {'capabilities', 'acquire', 'heartbeat', 'release', 'configure',
                        'capture', 'query_capture', 'cancel_capture', 'list_captures',
                        'get_capture_asset', 'open_capture','bind_calibration','clear_calibration'}:
            if name not in {'library_rpc','describe_library','list_library_assets','list_library_changes',
                            'get_library_asset','set_library_asset_flags','purge_library_asset','open_library_asset','import_library_asset'}:
                raise AttributeError(name)
        return lambda *args, **kwargs: self._call(name, *args, **kwargs)

    def capture_thumbnail(self, request_id):
        from camera.camerad.retained import thumbnail_retained
        asset = self.get_capture_asset(request_id)
        return thumbnail_retained(self.root, self.query_capture(request_id), asset)

    def capabilities(self):
        result = self._call('capabilities')
        result['retained_captures'] = {**result.get('retained_captures', {}),
            'binary_export': True, 'thumbnail': True, 'thumbnail_source': 'retained-dng',
            'thumbnail_calibrated': False}
        return result

    def export_capture(self, request_id, destination):
        from camera.camerad.retained import export_retained
        asset = self.get_capture_asset(request_id)
        return export_retained(self.root, self.query_capture(request_id), destination, asset)

    def get_state(self):
        state = self._call('get_state')
        state['preview'] = self.preview.state()
        return state

    @property
    def active(self):
        return self.get_state()['camera_state'] == 'capturing'

    def start_preview(self, token, epoch):
        self._call('heartbeat', token, epoch)
        if not self.get_state()['connected']:
            raise CameraError('device_disconnected', 'simulated device is disconnected')
        return self.preview.start()

    def stop_preview(self, token, epoch):
        self._call('heartbeat', token, epoch)
        return self.preview.stop()

    def reconnect(self, token, epoch):
        result = self._call('reconnect', token, epoch)
        self.preview.stop()
        return result

    def close(self):
        self.preview.stop()
        return self._call('close')

    def abort(self):
        self.preview.stop()
        return self.client.abort()
