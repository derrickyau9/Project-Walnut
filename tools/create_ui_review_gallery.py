"""Create a bounded, self-contained gallery from a successful synthetic Qt review.

Only review.json and its simple PNG children are read. Images are verified and
embedded unchanged; no Qt process, camera, VM, network or external asset is used.
Publication is create-only. The HTML contains selected evidence, not the raw
report or its private paths.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
import unicodedata
import zlib

MAX_REPORT_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGES = 32
MAX_DIMENSION = 8192
REPORT_FORMATS = frozenset({'DaOS-camera-app-shell-review-v1', 'DaOS-camera-first-review-v1'})
CAMERA_FIRST_PREVIEW = 'Python synthetic Bayer PreviewStream; separate scene, not sensor readout'
CAMERA_FIRST_CAPTURE = 'C++ hf-camerad / HF-Sim'
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
SIMPLE_PNG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.png\Z', re.IGNORECASE)
RUNTIME_TEXT = re.compile(r'[A-Za-z0-9_.+ -]{1,80}\Z')
PRIVATE_PATH = re.compile(r'(?:\b[A-Za-z]:[\\/]|/(?:home|Users|mnt/[a-z]/Users)/)')

LABELS = {
    '01-camera-live.png': ('相机与实时取景', 'Camera & live preview', '拍摄'),
    '02-camera-saved.png': ('拍摄完成', 'Committed photograph', '拍摄'),
    '03-photos.png': ('照片与回放', 'Retained photos', '拍摄'),
    '04-settings-exposure.png': ('曝光设置', 'Exposure settings', '设置'),
    '05-settings-display.png': ('显示与外观', 'Display & appearance', '设置'),
    '06-recipes.png': ('拍摄配方', 'Capture recipes', '应用'),
    '07-diagnostics.png': ('诊断与执行记录', 'Diagnostics & evidence', '应用'),
    '08-devices.png': ('设备与能力', 'Devices & capabilities', '应用'),
    '09-system.png': ('系统信息', 'Actual runtime identity', '系统'),
    '10-launcher.png': ('应用启动器', 'App launcher', '系统'),
    '11-command-palette.png': ('菜单与命令搜索', 'Command menu', '系统'),
    '12-compact-settings.png': ('紧凑布局 · 设置', '800 × 600 settings', '紧凑'),
    '13-compact-camera-dark.png': ('紧凑布局 · 深色相机', 'Compact dark camera', '紧凑'),
    '01-live-800.png': ('取景与拍摄主画面', '800 × 600 primary camera', '拍摄'),
    '02-quick-800.png': ('Q · 快速参数', 'Quick camera controls', 'Q'),
    '03-menu-exposure-800.png': ('MENU · 曝光', 'Camera exposure menu', '菜单'),
    '04-menu-display-800.png': ('MENU · 显示', 'Camera display menu', '菜单'),
    '05-menu-tools-800.png': ('MENU · 工具', 'Optional tools behind MENU', '菜单'),
    '06-saved-800.png': ('拍摄确认', 'Single native capture confirmed', '拍摄'),
    '07-playback-800.png': ('单张照片回放', 'Retained DNG playback', '回放'),
    '08-live-1280.png': ('开发预览 · 宽画面', 'Secondary 1280 × 800 host view', '开发预览'),
}


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _identity(info):
    return info.st_dev, info.st_ino


def _directory(path):
    path = Path(path).absolute()
    # Inspect every component before resolving, including junctions on Windows.
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('existing plain directory without links/reparse points required')
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise ValueError('canonical directory required')
    return resolved, _identity(resolved.lstat())


def _check_directory(path, expected):
    actual, identity = _directory(path)
    if actual != path or identity != expected:
        raise ValueError('review/output directory identity changed')


def _read_file(directory, name, maximum, directory_identity):
    _check_directory(directory, directory_identity)
    path = directory / name
    before = path.lstat()
    if (_is_link(before) or not stat.S_ISREG(before.st_mode) or
            getattr(before, 'st_nlink', 1) != 1 or not 0 < before.st_size <= maximum):
        raise ValueError('bounded regular input file without links required')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            opened = os.fstat(descriptor)
            if _is_link(opened) or not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                raise ValueError('input file identity changed before read')
            data = source.read(maximum + 1)
            after = os.fstat(descriptor)
        observed = path.lstat()
        if (not 0 < len(data) <= maximum or len(data) != before.st_size or
                _identity(after) != _identity(before) or _identity(observed) != _identity(before) or
                after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns or
                getattr(after, 'st_nlink', 1) != 1 or
                _is_link(observed)):
            raise ValueError('input changed or exceeded its bound while read')
    finally:
        os.close(descriptor)
    _check_directory(directory, directory_identity)
    return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate review JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('nonfinite JSON constant refused')


def _png_dimensions(data):
    if not data.startswith(PNG_MAGIC):
        raise ValueError('PNG signature required')
    offset, size, saw_data, saw_end = 8, None, False, False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('truncated PNG chunk')
        length = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data) or not re.fullmatch(b'[A-Za-z]{4}', kind):
            raise ValueError('invalid PNG chunk bounds/type')
        payload = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack_from('>I', data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != checksum:
            raise ValueError('PNG chunk CRC mismatch')
        if size is None:
            if kind != b'IHDR' or length != 13:
                raise ValueError('first PNG chunk must be a complete IHDR')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if (not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION or
                    depth not in depths.get(color, ()) or compression or filtering or interlace not in (0, 1)):
                raise ValueError('invalid or oversized PNG IHDR')
            size = width, height
        elif kind == b'IHDR':
            raise ValueError('duplicate PNG IHDR')
        if kind == b'acTL':
            raise ValueError('animated PNG is not a static screenshot')
        if kind == b'IDAT':
            saw_data = True
        if kind == b'IEND':
            if length or end != len(data):
                raise ValueError('PNG IEND must end the file')
            saw_end = True
        offset = end
    if size is None or not saw_data or not saw_end:
        raise ValueError('complete static PNG required')
    return size


def _text(value, maximum=1000):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('bounded display text required')
    if PRIVATE_PATH.search(value):
        return '本机路径已省略 / Local filesystem path omitted'
    return value


def _runtime_token(value):
    if not isinstance(value, str) or not RUNTIME_TEXT.fullmatch(value):
        raise ValueError('bounded runtime identity token required')
    return value


def _positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 86400000000


def _number_label(value, suffix):
    return f'{value:g}{suffix}' if _positive_number(value) else '未记录 / Not recorded'


def _count_label(value):
    return str(value) if type(value) is int and 0 <= value <= 100000 else '未记录'


def load_review(review_dir):
    directory, identity = _directory(review_dir)
    report_data = _read_file(directory, 'review.json', MAX_REPORT_BYTES, identity)
    report = json.loads(report_data, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if (not isinstance(report, dict) or not isinstance(report.get('format'), str) or
            report['format'] not in REPORT_FORMATS or
            report.get('passed') is not True or report.get('synthetic') is not True or
            report.get('physical_camera_validated') is not False or report.get('physical_display_validated') is not False):
        raise ValueError('successful synthetic-only app-shell review required')
    entries = report.get('screenshots')
    runtime = report.get('runtime')
    if (not isinstance(entries, list) or not 1 <= len(entries) <= MAX_IMAGES or
            not isinstance(runtime, dict)):
        raise ValueError('bounded screenshot list and actual runtime identity required')
    selected_runtime = {key: _runtime_token(runtime.get(key)) for key in ('system', 'machine', 'qpa', 'qt')}
    for key in ('python', 'pyqt', 'release'):
        if key in runtime:
            selected_runtime[key] = _runtime_token(runtime[key])
    if report['format'] == 'DaOS-camera-first-review-v1':
        if (report.get('preview_source') != CAMERA_FIRST_PREVIEW or
                report.get('capture_backend') != CAMERA_FIRST_CAPTURE):
            raise ValueError('camera-first independent Python preview/native capture provenance required')
    images, seen, total = [], set(), 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('screenshot record required')
        name = entry.get('file')
        if (not isinstance(name, str) or not SIMPLE_PNG.fullmatch(name) or
                name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1, 10)), *('LPT'+str(i) for i in range(1, 10))}):
            raise ValueError('simple contained PNG filename required')
        key = unicodedata.normalize('NFC', name).casefold()
        if key in seen:
            raise ValueError('duplicate screenshot filename')
        seen.add(key)
        expected = entry.get('sha256')
        width, height = entry.get('width'), entry.get('height')
        if (not isinstance(expected, str) or not SHA256.fullmatch(expected) or
                type(width) is not int or type(height) is not int or
                not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION):
            raise ValueError('screenshot hash and bounded integer dimensions required')
        data = _read_file(directory, name, MAX_IMAGE_BYTES, identity)
        total += len(data)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError('total screenshot bytes exceed gallery bound')
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('screenshot SHA256 mismatch: ' + name)
        if _png_dimensions(data) != (width, height):
            raise ValueError('screenshot dimensions differ from report: ' + name)
        description = _text(entry.get('description', name))
        title, english, group = LABELS.get(name, (description[:100], 'Additional screenshot', '其他'))
        images.append({'file': name, 'sha256': expected, 'width': width, 'height': height,
                       'description': description, 'title': title, 'english': english,
                       'group': group, 'data': data})
    _check_directory(directory, identity)
    return report, selected_runtime, images, hashlib.sha256(report_data).hexdigest()


STYLE = '''
:root{color-scheme:light;--bg:#ffffff;--paper:#ffffff;--ink:#000000;--muted:#555555;--line:#cccccc;--accent:#666666;--soft:#eeeeee;font-family:Inter,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink)}button,a{-webkit-tap-highlight-color:#cccccc}button,a:focus-visible{outline:3px solid var(--accent);outline-offset:4px}a{color:inherit}button{font:inherit;cursor:pointer}main,header,footer{max-width:1440px;margin:auto;padding:0 32px}.mast{display:flex;justify-content:space-between;align-items:center;padding-top:28px;padding-bottom:22px;gap:20px}.brand{font-size:20px;font-weight:750;letter-spacing:-.04em}.brand b{color:var(--accent)}.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}.hero{display:grid;grid-template-columns:1fr auto;gap:32px;align-items:end;margin:22px 0 24px}.hero h1{font-size:clamp(30px,4.6vw,54px);line-height:1.14;letter-spacing:-.045em;font-weight:650;margin:8px 0 12px}.lead{max-width:820px;color:var(--muted);line-height:1.8;font-size:15px;margin:0}.runtime{background:var(--paper);border:1px solid var(--line);padding:18px 22px;border-radius:20px;min-width:220px}.runtime strong{display:block;margin:6px 0;font-size:19px}.runtime small{color:var(--muted);line-height:1.7}.pill{display:inline-block;background:var(--soft);color:#333333;border:1px solid #cccccc;border-radius:999px;padding:6px 10px;font-size:11px;margin:0 6px 6px 0}.notice{margin:22px 0;background:#eeeeee;border:1px solid #cccccc;border-radius:16px;padding:14px 18px;color:#444444;line-height:1.75;font-size:13px}.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0 22px}.fact{background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:16px;min-width:0}.fact span{display:block;font-size:11px;color:var(--muted);margin-bottom:7px}.fact strong{font-size:19px;font-weight:600;overflow-wrap:anywhere}.request{font-size:12px;line-height:1.8;color:var(--muted);overflow-wrap:anywhere}.request code{font-family:ui-monospace,monospace;font-size:11px}.jump{display:flex;flex-wrap:wrap;gap:8px;margin:25px 0 18px}.jump a{border:1px solid var(--line);background:var(--paper);border-radius:10px;padding:8px 11px;text-decoration:none;font-size:12px}.instructions{font-size:12px;color:var(--muted);line-height:1.7;margin:0 0 20px}.gallery{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.card{border:1px solid var(--line);border-radius:22px;background:var(--paper);overflow:hidden;scroll-margin-top:22px}.card-head{display:flex;justify-content:space-between;gap:12px;padding:20px 22px 16px}.card h2{font-size:19px;font-weight:650;letter-spacing:-.025em;margin:0 0 6px}.card h2 small{font-size:11px;font-weight:500;color:var(--muted);display:block;margin-top:7px;letter-spacing:0}.card .number{font-family:ui-monospace,monospace;font-size:12px;color:#666666}.image-button{display:block;border:0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);width:100%;padding:0;background:#eeeeee;text-align:left}.image-button img{display:block;width:100%;height:auto}.caption{padding:14px 22px 18px}.caption p{font-size:12px;line-height:1.65;color:var(--muted);margin:9px 0}.meta{font-size:10px;letter-spacing:.02em;color:var(--muted)}.caption details{font-size:11px;color:var(--muted);line-height:1.8}.caption summary{cursor:pointer}.caption code{font-size:10px;overflow-wrap:anywhere}.provenance{margin:36px 0 20px;border:1px solid var(--line);border-radius:20px;background:var(--paper);padding:20px 24px;font-size:12px;line-height:1.9;color:var(--muted);overflow-wrap:anywhere}.provenance h2{margin:0 0 8px;font-size:16px;color:var(--ink)}footer{padding-top:14px;padding-bottom:32px;font-size:11px;color:var(--muted);line-height:1.8}dialog{border:1px solid #666666;background:#111111;color:#ffffff;border-radius:16px;padding:0;width:96vw;max-width:1800px;max-height:94vh;box-shadow:0 24px 90px #000000}dialog::backdrop{background:#000000}dialog .viewer-bar{display:flex;align-items:center;gap:10px;padding:12px 14px}dialog .viewer-title{flex:1;min-width:0;font-size:13px}dialog button,dialog a{border:1px solid #666666;background:#222222;color:#ffffff;border-radius:8px;padding:8px 12px;text-decoration:none;font-size:12px}dialog img{display:block;max-width:100%;height:auto;margin:auto}dialog .viewer-image{overflow:auto;max-height:calc(94vh - 64px);background:#000000}dialog.full-size img{max-width:none;width:auto}dialog .viewer-note{font-size:11px;color:#cccccc}body:has(dialog[open]){overflow:hidden}@media(max-width:900px){main,header,footer{padding-left:18px;padding-right:18px}.hero{grid-template-columns:1fr}.runtime{min-width:0}.facts{grid-template-columns:repeat(2,minmax(0,1fr))}.gallery{grid-template-columns:1fr}.mast .eyebrow{max-width:150px;text-align:right}.viewer-note{display:none}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{.jump,.instructions,dialog{display:none}.gallery{display:block}.card{break-inside:avoid;margin-bottom:20px}.image-button{break-inside:avoid}}
'''

CAMERA_FIRST_STYLE = '''
.camera-first .hero h1{font-size:clamp(32px,4.4vw,52px)}.camera-first .gallery .card:first-child{grid-column:1/-1}.camera-first .gallery .card:first-child .image-button{display:flex;justify-content:center;background:#000000}.camera-first .gallery .card:first-child .image-button img{max-width:800px;width:100%;height:auto}.pipeline-evidence{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:18px 0}.pipeline-evidence>div{border-left:3px solid var(--accent);padding:12px 16px;background:var(--paper);border-radius:0 12px 12px 0}.pipeline-evidence span{display:block;color:var(--muted);font-size:11px;margin-bottom:7px}.pipeline-evidence strong{font-size:12px;line-height:1.7;font-weight:550;display:block}.facts.expanded-facts{grid-template-columns:repeat(5,minmax(0,1fr))}@media(max-width:900px){.pipeline-evidence{grid-template-columns:1fr}.facts.expanded-facts{grid-template-columns:repeat(2,minmax(0,1fr))}}
'''

SCRIPT = '''
(() => {
  const shots = Array.from(document.querySelectorAll('.image-button'));
  const dialog = document.getElementById('viewer');
  const picture = document.getElementById('viewer-image');
  const title = document.getElementById('viewer-title');
  const download = document.getElementById('viewer-download');
  let current = 0, previousFocus = null;
  function display(index) {
    current = (index + shots.length) % shots.length;
    const source = shots[current].querySelector('img');
    picture.src = source.src;
    picture.alt = source.alt;
    title.textContent = (current + 1) + ' / ' + shots.length + ' · ' + source.alt;
    download.href = source.src;
    download.download = shots[current].dataset.file;
  }
  function close() {
    if (typeof dialog.close === 'function') dialog.close();
    else { dialog.removeAttribute('open'); previousFocus?.focus(); }
  }
  shots.forEach((button, index) => button.addEventListener('click', () => {
    previousFocus = button;
    display(index);
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    document.getElementById('viewer-close').focus();
  }));
  document.getElementById('viewer-close').addEventListener('click', close);
  document.getElementById('viewer-prev').addEventListener('click', () => display(current - 1));
  document.getElementById('viewer-next').addEventListener('click', () => display(current + 1));
  document.getElementById('viewer-size').addEventListener('click', (event) => {
    const full = dialog.classList.toggle('full-size');
    event.currentTarget.setAttribute('aria-pressed', String(full));
    event.currentTarget.textContent = full ? '适应窗口' : '原始尺寸';
  });
  dialog.addEventListener('close', () => previousFocus?.focus());
  dialog.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowLeft') { event.preventDefault(); display(current - 1); }
    else if (event.key === 'ArrowRight') { event.preventDefault(); display(current + 1); }
    else if (event.key === 'Escape' && typeof dialog.close !== 'function') close();
  });
})();
'''


def render_gallery(report, runtime, images, report_sha256):
    e = lambda value: html.escape(str(value), quote=True)
    camera_first = report['format'] == 'DaOS-camera-first-review-v1'
    page_title = ('DaOS Camera · 相机操作界面审阅' if camera_first else
                  'DaOS Camera · 设置、菜单与应用审阅')
    phase = 'R12 · Camera operating UI' if camera_first else 'R11 · Actual Qt screenshots'
    eyebrow = '800 × 600 primary camera · Q · MENU · PLAY' if camera_first else 'Camera settings · Menus · Apps'
    headline = '摄影优先的<br>相机操作界面' if camera_first else '相机设置、菜单<br>与 DaOS 应用'
    introduction = (f'{len(images)} 张实际 Qt 运行截图，以 800 × 600 取景画面为主：Q 快速参数、相机 MENU、拍摄确认和单张照片回放。1280 × 800 仅为次要开发预览。点击原图放大。'
                    if camera_first else f'{len(images)} 张实际 Qt 运行截图，包含左侧导航、相机与相册、分组设置、拍摄配方、诊断、启动器与命令搜索。点击缩略图查看完整原图。')
    actual_runtime = f"{runtime['system']} · {runtime['machine']}"
    offscreen = runtime['qpa'] == 'offscreen'
    render_mode = '离屏 Qt 渲染 / Offscreen Qt' if offscreen else f"Qt {runtime['qpa']} rendering"
    settings = report.get('settings') if isinstance(report.get('settings'), dict) else {}
    metadata = report.get('dng_metadata') if isinstance(report.get('dng_metadata'), dict) else {}
    dimensions = '未记录 / Not recorded'
    if all(type(metadata.get(key)) is int and 1 <= metadata[key] <= 100000 for key in ('width', 'height')):
        dimensions = f"{metadata['width']} × {metadata['height']}"
    dng_hash, export_hash = report.get('dng_sha256'), report.get('export_dng_sha256')
    export_match = (isinstance(dng_hash, str) and SHA256.fullmatch(dng_hash) and dng_hash == export_hash)
    export_label = '导出哈希一致' if export_match else '未记录一致性'
    request = report.get('request_id')
    request_label = request if isinstance(request, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request) else '未记录'
    source = report.get('retail_source') if isinstance(report.get('retail_source'), dict) else {}
    repo, revision = source.get('repository'), source.get('revision')
    reference = '源报告未提供可验证的 Retail 版本 / Not recorded'
    if (isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) and
            isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision)):
        reference = f'<a href="https://github.com/{e(repo)}/tree/{e(revision)}">{e(repo)}</a> · <code>{e(revision)}</code>'
    pipeline = ''
    if camera_first:
        pipeline = ('<section class="pipeline-evidence" aria-label="Separate preview and capture sources">'
                    '<div><span>实时取景 / Independent preview source</span><strong>' + e(report['preview_source']) + '</strong></div>'
                    '<div><span>照片采集与保存 / Native capture backend</span><strong>' + e(report['capture_backend']) + '</strong></div></section>')
    actual_gain = report.get('actual_analogue_gain')
    actual_gain_fact = ('<div class="fact"><span>实际模拟增益 / Actual gain</span><strong>' +
                        e(_number_label(actual_gain, ' ×')) + '</strong></div>') if _positive_number(actual_gain) else ''
    source_reference = '' if camera_first else f'<p>Retail 参考版本：{reference}</p>'
    if camera_first:
        sources = report.get('source_sha256')
        if isinstance(sources, dict):
            for path in ('ui/on-device/Main.qml', 'ui/on-device/CameraMenu.qml', 'ui/on-device/CameraPlayback.qml'):
                checksum = sources.get(path)
                if isinstance(checksum, str) and SHA256.fullmatch(checksum):
                    source_reference += f'<p><code>{e(path)}</code><br><code>{e(checksum)}</code></p>'
    scope_note = ('此页展示摄影优先的原生 Qt 操作界面。Python 实时合成预览与 C++ 拍摄保存是独立链路；预览不是传感器读出或已保存 DNG 的实时重放。本工具未执行拍摄、虚拟机或新 BSD 镜像启动。'
                  if camera_first else '外观和导航是对已核实 DaOS Retail 设计的原生 Qt 适配；这些截图不表示完整 Retail 业务模块、原始框架运行时、物理相机或新 BSD 镜像已交付。')
    image_boot = ('源报告标记已验证；本页未重新执行镜像启动' if
                  report.get('arm64_image_boot_validated') is True and report.get('bsd_image_boot_validated') is True
                  else '此组截图不建立 ARM64 / FreeBSD 镜像启动结论')
    cards, navigation = [], []
    for index, image in enumerate(images, 1):
        title, english = e(image['title']), e(image['english'])
        navigation.append(f'<a href="#screen-{index:02d}">{index:02d} {title}</a>')
        encoded = base64.b64encode(image['data']).decode('ascii')
        alt = f"{image['title']} / {image['english']} · {image['width']} × {image['height']}"
        cards.append(f'''<article class="card" id="screen-{index:02d}">
<div class="card-head"><div><div class="eyebrow">{e(image['group'])}</div><h2>{title}<small>{english}</small></h2></div><span class="number">{index:02d}</span></div>
<button type="button" class="image-button" data-file="{e(image['file'])}" aria-label="放大 {title} / Enlarge screenshot" aria-haspopup="dialog"><img src="data:image/png;base64,{encoded}" alt="{e(alt)}" width="{image['width']}" height="{image['height']}" loading="lazy"></button>
<div class="caption"><div class="meta">{e(image['file'])} · {image['width']} × {image['height']} · 原始 PNG / Unchanged PNG</div><p>{e(image['description'])}</p><details><summary>SHA-256 / 文件校验值</summary><code>{e(image['sha256'])}</code></details></div></article>''')
    html_text = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{e(page_title)}</title><style>{STYLE}{CAMERA_FIRST_STYLE if camera_first else ''}</style></head><body class="{'camera-first' if camera_first else 'app-shell'}">
<header class="mast"><div class="brand">DaOS <b>Camera</b></div><div class="eyebrow">{e(phase)}<br>离线 UI 审阅</div></header>
<main><section class="hero"><div><div class="eyebrow">{e(eyebrow)}</div><h1>{headline}</h1><p class="lead">{e(introduction)}</p></div>
<aside class="runtime"><div class="eyebrow">Actual runtime / 本次运行环境</div><strong>{e(actual_runtime)}</strong><small>Qt {e(runtime['qt'])} · {e(runtime['qpa'])}<br>{e(render_mode)}</small></aside></section>
<div><span class="pill">HF-Sim · 合成 Bayer</span><span class="pill">快门 {_count_label(report.get('shutter_actions'))} 次 · 日志 {_count_label(report.get('journal_rows'))} 条</span><span class="pill">{e(export_label)} · 源报告</span></div>
<div class="notice">本次运行于 <strong>{e(actual_runtime)}</strong>，模式为 <strong>{e(render_mode)}</strong>。{e(image_boot)}；未验证实体传感器或物理显示器。截图未重绘、未裁剪，PNG 原始字节已按报告中的 SHA-256 和尺寸逐一校验。</div>
{pipeline}<section class="facts{' expanded-facts' if actual_gain_fact else ''}" aria-label="Capture evidence"><div class="fact"><span>请求曝光 / Requested exposure</span><strong>{e(_number_label(settings.get('requested_exposure_us'), ' μs'))}</strong></div><div class="fact"><span>实际曝光 / Actual exposure</span><strong>{e(_number_label(report.get('actual_exposure_us'), ' μs'))}</strong></div><div class="fact"><span>请求模拟增益 / Requested gain</span><strong>{e(_number_label(settings.get('requested_analogue_gain'), ' ×'))}</strong></div>{actual_gain_fact}<div class="fact"><span>合成 Bayer / Synthetic Bayer</span><strong>{e(dimensions)}</strong></div></section>
<p class="request">原始请求 / Original request: <code>{e(request_label)}</code></p>
<nav class="jump" aria-label="Screenshot navigation">{''.join(navigation)}</nav><p class="instructions">放大后使用 ← / → 翻页，Esc 关闭；“原始尺寸”显示像素尺寸，“下载 PNG”保存未经修改的源图。无网络依赖。<br>Click a screenshot to enlarge; use arrow keys to browse and Escape to close. All images are embedded locally.</p>
<section class="gallery" aria-label="Actual UI screenshots">{''.join(cards)}</section>
<section class="provenance"><h2>来源与验证 / Provenance</h2>{source_reference}<p>源 review.json SHA-256：<code>{e(report_sha256)}</code></p><p>{'DNG 与导出文件的 SHA-256 在源报告中相同：<code>'+e(dng_hash)+'</code>。本工具仅校验报告与 PNG，不重新读取 DNG 或执行拍摄。' if export_match else '此报告未给出可确认的 DNG / 导出哈希一致性。'}</p><p>{e(scope_note)}</p></section>
</main><footer>Project Walnut · DaOS FF-1 · Synthetic camera UI review<br>此 HTML 自包含全部原始截图。运行环境和限制取自源报告；本机私有路径、原始报告对象及业务数据未嵌入页面元数据。</footer>
<dialog id="viewer" aria-labelledby="viewer-title"><div class="viewer-bar"><button id="viewer-prev" type="button" aria-label="上一张 / Previous">←</button><div id="viewer-title" class="viewer-title"></div><button id="viewer-next" type="button" aria-label="下一张 / Next">→</button><button id="viewer-size" type="button" aria-pressed="false">原始尺寸</button><a id="viewer-download" download>下载 PNG</a><button id="viewer-close" type="button">关闭 / Close</button></div><div class="viewer-image"><img id="viewer-image" alt=""></div></dialog>
<script>{SCRIPT}</script></body></html>'''
    return html_text.encode('utf-8')


def create_gallery(review_dir, output):
    output = Path(output).absolute()
    parent, parent_identity = _directory(output.parent)
    output = parent / output.name
    if output.suffix.lower() != '.html' or output.name in {'', '.', '..'}:
        raise ValueError('new HTML output filename required')
    if os.path.lexists(output):
        raise ValueError('existing output refused')
    report, runtime, images, report_sha = load_review(review_dir)
    data = render_gallery(report, runtime, images, report_sha)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.DaOS-ui-review-', suffix='.tmp', dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        _check_directory(parent, parent_identity)
        # Atomic create-only publication: never replace another owner's file.
        os.link(temporary, output)
        if _identity(temporary.lstat()) != _identity(output.lstat()) or _is_link(output.lstat()):
            raise ValueError('published output identity changed')
    finally:
        temporary.unlink(missing_ok=True)
    return {'format': 'DaOS-ui-review-gallery-v1', 'created': True,
            'review_format': report['format'], 'presentation': 'camera-first' if report['format'] == 'DaOS-camera-first-review-v1' else 'app-shell',
            'output': str(output), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'source_review_sha256': report_sha, 'runtime': runtime, 'synthetic': True,
            'images_embedded_unchanged': True, 'image_count': len(images),
            'images': [{key: image[key] for key in ('file', 'width', 'height', 'sha256')} for image in images]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = create_gallery(args.review_dir, args.output)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(2, 'gallery refused: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
