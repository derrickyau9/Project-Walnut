"""Demand-driven synthetic preview with one cached frame and bounded rate.

This renders a separate small synthetic Bayer scene. It is not a downsample of
an actual sensor readout and does not claim mode concurrency on real hardware.
"""
from __future__ import annotations

import math
import struct
import threading
import time
import zlib

from simulation.bayer import generate_bayer


class PreviewUnavailable(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def _png(rgb, width, height):
    scanlines = b''.join(b'\x00' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' +
            _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            _chunk(b'IDAT', zlib.compress(scanlines, 3)) + _chunk(b'IEND', b''))


class PreviewStream:
    """Latest-frame cache; clients never enqueue image processing work.

    Repeated reads inside the rate limit return the same frame ID. Capture
    transitions explicitly return unavailable rather than pretending an old
    frame is live. The clock arguments support deterministic tests.
    """
    def __init__(self, width=320, height=240, fps=5, *, clock=time.monotonic, wall_clock=time.time):
        if type(width) is not int or type(height) is not int or width < 2 or height < 2 or width % 2 or height % 2 or width * height > 640 * 480:
            raise ValueError('preview dimensions must be even and at most 640x480 pixels')
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 0 < fps <= 10:
            raise ValueError('fps must be greater than zero and at most ten')
        self.width, self.height = width, height
        self.interval = 1 / fps
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.Lock()
        self.running = False
        self.frame_id = 0
        self.last = None
        self.last_at = float('-inf')
        self.key = None

    def state(self):
        with self.lock:
            return {'running': self.running, 'simulated': True, 'frame_id': self.frame_id,
                    'width': self.width, 'height': self.height, 'max_fps': 1 / self.interval,
                    'timestamp': self.last['timestamp'] if self.last else None}

    def start(self):
        with self.lock:
            self.running = True
        return self.state()

    def stop(self):
        with self.lock:
            self.running = False
            self.last = None
            self.key = None
        return self.state()

    def snapshot(self, settings, capturing=False):
        exposure = settings.get('requested_exposure_us')
        gain = settings.get('requested_analogue_gain')
        if type(exposure) is not int or not 100 <= exposure <= 30000000 or isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain) or not 1 <= gain <= 16:
            raise ValueError('invalid preview exposure or gain')
        scene = settings.get('simulation_scene', 'scene')
        if scene not in {'scene', 'dark', 'flat'}:
            raise ValueError('invalid synthetic scene')
        key = (exposure, gain, settings.get('sensor_mode'), settings.get('_pipeline_generation'), scene)
        with self.lock:
            if not self.running:
                raise PreviewUnavailable('preview_stopped')
            if capturing:
                # Drop cache across mode transitions. On resume a new frame is
                # produced even if the time between requests is very short.
                self.last = None
                raise PreviewUnavailable('preview_paused_for_capture')
            now = self.clock()
            if self.last and key == self.key and now - self.last_at < self.interval:
                return dict(self.last)
            pixels = generate_bayer(self.width, self.height, exposure, gain, scene=scene)
            self.frame_id += 1
            # A moving white patch makes progression visible. Its trajectory is
            # deterministic for a sequence; it is part of the synthetic scene.
            patch_x = (self.frame_id * 8) % max(2, self.width - 16)
            if scene == 'scene':
                for y in range(min(16, self.height)):
                    for x in range(patch_x, min(patch_x + 16, self.width)):
                        pixels[y * self.width + x] = min(16383, 64 + int(9000 * exposure / 10000 * gain))
            lut = bytes(round((i / 16319) ** (1 / 2.2) * 255) for i in range(16320))
            rgb = bytearray(self.width * self.height * 3)
            for y in range(0, self.height, 2):
                for x in range(0, self.width, 2):
                    i = y * self.width + x
                    block = (pixels[i], (pixels[i + 1] + pixels[i + self.width]) // 2, pixels[i + self.width + 1])
                    color = bytes(lut[max(0, min(16319, value - 64))] for value in block)
                    for dy, dx in ((0, 0), (0, 1), (1, 0), (1, 1)):
                        offset = ((y + dy) * self.width + x + dx) * 3
                        rgb[offset:offset + 3] = color
            self.last = {'png': _png(rgb, self.width, self.height), 'frame_id': self.frame_id,
                         'timestamp': self.wall_clock(), 'simulated': True,
                         'requested_exposure_us': exposure, 'requested_analogue_gain': gain}
            self.key, self.last_at = key, now
            return dict(self.last)
