"""Bounded Qt frame transport and verified, original-pixel DNG review.

The live source here is explicitly a UI simulator. It is independent of the
native RAW capture authority. Images cross the worker/GUI boundary as immutable
QImages, never as PNG, base64 or per-pixel QVariant/JSON arrays.
"""
from __future__ import annotations

import bisect
from collections import OrderedDict
import math
import os
from pathlib import Path
import struct
import tempfile
import threading
import time

from PyQt5.QtCore import Qt, QSize, QRectF, QBuffer, QIODevice
from PyQt5.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QTransform
from PyQt5.QtQuick import QQuickImageProvider


class FrameStore:
    """Keep at most three published images: live, review fit, review tile."""
    def __init__(self):
        self._lock = threading.Lock()
        self._images = OrderedDict()
        self._generation = 0

    def publish(self, channel, image):
        if channel not in {'live', 'review', 'tile'} or not isinstance(image, QImage) or image.isNull():
            raise ValueError('invalid frame')
        with self._lock:
            self._generation += 1
            self._images[channel] = QImage(image)
            return 'image://daosframes/' + channel + '/' + str(self._generation)

    def clear(self, channel):
        with self._lock:
            self._images.pop(channel, None)

    def image(self, channel):
        with self._lock:
            return QImage(self._images.get(channel, QImage()))

    @property
    def retained_bytes(self):
        with self._lock:
            return sum(image.sizeInBytes() for image in self._images.values())


class FrameImageProvider(QQuickImageProvider):
    def __init__(self, store):
        super().__init__(QQuickImageProvider.Image)
        self.store = store

    def requestImage(self, identifier, requestedSize):
        image = self.store.image(identifier.split('/', 1)[0])
        # Native size is intentional: the QML scene owns display scaling.
        return image, image.size()


class SyntheticViewfinder:
    """Fast Qt-rendered simulation; never advertised as sensor live view."""
    def __init__(self, width=640, height=424, fps=30):
        self.width, self.height, self.fps = width, height, fps
        self.running = False
        self.frame_id = 0
        self._base = QImage(width, height, QImage.Format_RGB32)
        painter = QPainter(self._base)
        sky = QLinearGradient(0, 0, 0, height)
        sky.setColorAt(0, QColor('#7ba1b6'))
        sky.setColorAt(.58, QColor('#e0d3b5'))
        sky.setColorAt(1, QColor('#737a56'))
        painter.fillRect(self._base.rect(), sky)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#6e775a'))
        painter.drawEllipse(QRectF(-width*.15, height*.45, width*.9, height*.55))
        painter.setBrush(QColor('#454e40'))
        painter.drawEllipse(QRectF(width*.4, height*.5, width*.8, height*.6))
        painter.setBrush(QColor('#bb9d72'))
        painter.drawRect(QRectF(width*.36, height*.35, width*.24, height*.4))
        painter.setBrush(QColor('#433e35'))
        painter.drawRect(QRectF(width*.44, height*.49, width*.08, height*.26))
        painter.setBrush(QColor('#ddd6b9'))
        painter.drawEllipse(QRectF(width*.41, height*.37, width*.14, height*.1))
        painter.end()

    def start(self): self.running = True
    def stop(self): self.running = False

    def snapshot(self, settings):
        if not self.running:
            return None
        started = time.perf_counter_ns()
        self.frame_id += 1
        image = self._base.copy()
        painter = QPainter(image)
        # Moving target proves frame progression without inventing AF telemetry.
        x = (self.frame_id * 4) % (self.width + 64) - 32
        painter.setPen(QPen(QColor('#e7dfcf'), 2))
        painter.setBrush(QColor('#b4b0a0'))
        painter.drawEllipse(QRectF(x, self.height*.69, 28, 20))
        painter.setPen(QPen(QColor('#222222'), 1))
        painter.drawLine(x+6, int(self.height*.69+20), x+3, int(self.height*.69+27))
        painter.drawLine(x+22, int(self.height*.69+20), x+25, int(self.height*.69+27))
        # This is an illustrative exposure preview, not calibrated metering.
        ev = math.log2(max(.01, settings['requested_exposure_us'] / 10000 * settings['requested_analogue_gain']))
        if ev < 0:
            painter.fillRect(image.rect(), QColor(0, 0, 0, min(235, round(-ev * 28))))
        elif ev > 0:
            painter.fillRect(image.rect(), QColor(255, 255, 255, min(220, round(ev * 25))))
        painter.end()
        return {'image': image, 'frame_id': self.frame_id, 'simulated': True,
                'produced_ns': started, 'timestamp': time.time(),
                'render_ms': (time.perf_counter_ns()-started)/1e6}


def _open_capture(client, request_id):
    # Both local authorities and the loopback attachment return verified handles.
    if hasattr(client, 'open_capture'):
        return client.open_capture(request_id)
    return client.download_capture(request_id)


def render_retained(client, request_id, *, region=None, max_size=(1000, 660)):
    """Render verified Bayer samples into a bounded RGB image.

    In original-pixel mode each output pixel is one source coordinate. A simple
    nearest-colour RGGB reconstruction is used, not an enlarged thumbnail. RAW
    samples and the DNG are untouched. Fit review is explicitly subsampled.
    """
    from camera.dng.inspect import inspect_dng
    from camera.camerad.retained import _verified_stream, _snapshot
    with _open_capture(client, request_id) as stream:
        before = _verified_stream(stream, request_id)
        info = inspect_dng(stream)
        width, height = info['width'], info['height']
        stream.seek(0)
        endian = '<' if stream.read(2) == b'II' else '>'
        if region is None:
            x0 = y0 = 0
            rw, rh = width, height
            ratio = min(1., max_size[0]/width, max_size[1]/height)
            ow, oh = max(1, int(width*ratio)), max(1, int(height*ratio))
        else:
            x0, y0, rw, rh = region
            if any(type(value) is not int for value in region) or not 1 <= rw <= 1024 or not 1 <= rh <= 768:
                raise ValueError('review tile exceeds its bound')
            x0, y0 = max(0, min(x0, width-1)), max(0, min(y0, height-1))
            rw, rh = min(rw, width-x0), min(rh, height-y0)
            ow, oh = rw, rh
        starts, strips, first = [], [], 0
        for offset, count in zip(info['strip_offsets'], info['strip_byte_counts']):
            rows = count // (width*2)
            starts.append(first)
            strips.append((first, rows, offset))
            first += rows
        if first != height:
            raise ValueError('invalid retained strip layout')
        rows_cache = OrderedDict()
        def row(number):
            if number in rows_cache:
                return rows_cache[number]
            first, count, offset = strips[bisect.bisect_right(starts, number)-1]
            if not first <= number < first+count:
                raise ValueError('invalid retained row')
            stream.seek(offset+(number-first)*width*2)
            raw = stream.read(width*2)
            if len(raw) != width*2:
                raise ValueError('truncated retained row')
            values = struct.unpack(endian + str(width) + 'H', raw)
            rows_cache[number] = values
            while len(rows_cache) > 4:
                rows_cache.popitem(last=False)
            return values
        lut = bytes(round((max(0, value-64)/16319)**(1/2.2)*255) for value in range(16384))
        rgb = bytearray(ow*oh*3)
        for dy in range(oh):
            sy = y0 + min(rh-1, dy*rh//oh)
            upper, lower = row(sy & ~1), row(min(height-1, (sy & ~1)+1))
            for dx in range(ow):
                sx = x0 + min(rw-1, dx*rw//ow)
                even = sx & ~1
                r, g1 = upper[even], upper[min(width-1, even+1)]
                g2, b = lower[even], lower[min(width-1, even+1)]
                if max(r, g1, g2, b) > 16383:
                    raise ValueError('invalid retained sample')
                g = g1 if sy % 2 == 0 and sx % 2 else g2 if sy % 2 and not sx % 2 else (g1+g2)//2
                at = (dy*ow+dx)*3
                rgb[at:at+3] = bytes((lut[r], lut[g], lut[b]))
        if _snapshot(os.fstat(stream.fileno())) != before:
            raise ValueError('retained source changed during review')
        image = QImage(bytes(rgb), ow, oh, ow*3, QImage.Format_RGB888).copy()
        return {'request_id': request_id, 'image': image, 'width': width, 'height': height,
                'x': x0, 'y': y0, 'tile_width': rw, 'tile_height': rh,
                'source': 'retained-dng', 'rendering': 'uncalibrated-rggb'}


def apply_review_edits(image, edits):
    """Bounded uncalibrated display edits; no modification of sensor samples."""
    exposure = float(edits.get('exposure', 0))
    contrast = float(edits.get('contrast', 1))
    saturation = float(edits.get('saturation', 1))
    rotation = int(edits.get('rotation', 0))
    if (not all(math.isfinite(v) for v in (exposure, contrast, saturation))
            or not -5 <= exposure <= 5 or not 0 <= contrast <= 3
            or not 0 <= saturation <= 3 or rotation not in (0,90,180,270)):
        raise ValueError('invalid non-destructive edits')
    image = image.convertToFormat(QImage.Format_RGB888)
    width, height, stride = image.width(), image.height(), image.bytesPerLine()
    pointer = image.constBits(); pointer.setsize(image.sizeInBytes())
    data = bytearray(pointer)
    lut = bytes(max(0,min(255,round((((value/255)**2.2*2**exposure)**(1/2.2)-.5)*contrast*255+127.5)))
                for value in range(256))
    for y in range(height):
        for x in range(width):
            at = y*stride+x*3
            r,g,b = (lut[data[at]],lut[data[at+1]],lut[data[at+2]])
            luma = .2126*r+.7152*g+.0722*b
            data[at:at+3] = bytes(max(0,min(255,round(luma+(channel-luma)*saturation))) for channel in (r,g,b))
    image = QImage(bytes(data),width,height,stride,QImage.Format_RGB888).copy()
    crop = edits.get('crop')
    if crop is not None:
        if (not isinstance(crop,(list,tuple)) or len(crop)!=4
                or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in crop)):
            raise ValueError('invalid normalized crop')
        x,y,w,h = crop
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1.000001 and y+h <= 1.000001):
            raise ValueError('crop exceeds the photograph')
        image = image.copy(int(x*width),int(y*height),max(1,int(w*width)),max(1,int(h*height)))
    if rotation:
        image = image.transformed(QTransform().rotate(rotation))
    return image


def export_review_jpeg(client, request_id, destination, edits=None):
    """Export a <=2048px SDR proof, explicitly labelled uncalibrated.

    New local files only. Full-resolution/colour-calibrated in-camera JPEG is
    a separate ISP capability, not implied by this proof exporter.
    """
    from camera.camerad.retained import canonical_id
    from apps_controller import _checked_directory, _publish_new
    canonical_id(request_id)
    destination = Path(destination)
    if not destination.is_absolute() or destination.suffix.lower() not in ('.jpg','.jpeg'):
        raise ValueError('choose an absolute new JPEG destination')
    parent = _checked_directory(destination.parent)
    packet = render_retained(client, request_id, max_size=(2048,2048))
    image = apply_review_edits(packet['image'], edits or {})
    image.setText('Software','DaOS Camera / uncalibrated SDR proof')
    image.setText('Description','Synthetic retained DNG '+request_id+'; maximum 2048px; original RAW unchanged')
    buffer = QBuffer()
    if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer,'JPEG',92):
        raise ValueError('JPEG encoder unavailable')
    raw = bytes(buffer.data())
    if len(raw)>16*1024*1024:
        raise ValueError('JPEG proof exceeds its bound')
    _publish_new(parent,destination.name,raw)
    return {'request_id':request_id,'destination':str(destination),'byte_count':len(raw),
            'width':image.width(),'height':image.height(),'calibrated':False,'max_edge':2048}
