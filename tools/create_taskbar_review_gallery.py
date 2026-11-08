"""Create an offline player for verified, no-shutter Qt Taskbar recordings.

Original PNG bytes are embedded unchanged. Playback uses recorded ready-time
differences, not scheduled offsets, synthetic tweening or a frame-rate claim.
Only selected evidence is displayed; private paths and the raw report are not.
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
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.create_ui_review_gallery import (
    _directory, _read_file, _png_dimensions, _check_directory, _identity, _is_link,
    _unique, _invalid_constant)


MAX_REPORT_BYTES = 128*1024
MAX_IMAGE_BYTES = 10*1024**2
MAX_TOTAL_IMAGE_BYTES = 64*1024**2
MAX_STATIC = 8
MAX_FRAMES = 32
NAME = re.compile(r'[a-z0-9-]{1,120}\.png\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'[A-Za-z0-9_.+ -]{1,80}\Z')
FORMAT = 'DaOS-taskbar-ui-review-v1'
FORMAT_V2 = 'DaOS-taskbar-ui-review-v2'
BACKEND = 'Python reference authority; no native daemon'
PREVIEW = 'Python synthetic Bayer PreviewStream; separate from capture'


def number(value, maximum=120000, *, minimum=0):
    if type(value) not in (int,float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('bounded finite numeric evidence required')
    return value


def observation(value):
    if not isinstance(value, dict):
        raise ValueError('request and ready observations required')
    result = {'elapsed_ms':number(value.get('elapsed_ms')),
              'progress':number(value.get('progress'),1),
              'viewfinder_width':number(value.get('viewfinder_width'),8192,minimum=1),
              'viewfinder_height':number(value.get('viewfinder_height'),8192,minimum=1)}
    return result


def load_review(review_dir):
    directory, identity = _directory(review_dir)
    raw = _read_file(directory,'review.json',MAX_REPORT_BYTES,identity)
    try:
        report = json.loads(raw,object_pairs_hook=_unique,parse_constant=_invalid_constant)
    except RecursionError as error:
        raise ValueError('review JSON nesting is excessive') from error
    if (not isinstance(report,dict) or report.get('format') not in (FORMAT,FORMAT_V2)
            or any(report.get(key) is not True for key in ('passed','synthetic','fixture_closed_cleanly','source_unchanged'))
            or any(report.get(key) is not False for key in ('physical_camera_validated','physical_display_validated','appliance_image_boot_validated'))
            or report.get('backend') != BACKEND or report.get('preview_source') != PREVIEW
            or report.get('qml_warnings') != []):
        raise ValueError('successful synthetic reference-only review required')
    for key in ('journal_rows','planned_shutter_actions','observed_shutter_actions'):
        if type(report.get(key)) is not int or report[key] != 0:
            raise ValueError('zero-shutter/zero-journal review required')
    before,after = report.get('source_sha256_before'),report.get('source_sha256_after')
    if (not isinstance(before,dict) or not 1 <= len(before) <= 1000 or before != after
            or any(not isinstance(key,str) or len(key)>512 or not isinstance(value,str) or not SHA.fullmatch(value)
                   for key,value in before.items())):
        raise ValueError('unchanged bounded source fingerprints required')
    if (not isinstance(report.get('camera_before'),dict)
            or report['camera_before'] != report.get('camera_after')
            or report['camera_before'].get('request_id') != ''
            or not isinstance(report['camera_before'].get('settings'),dict)
            or not report['camera_before']['settings']):
        raise ValueError('unchanged fresh camera identity/settings required')
    runtime = report.get('runtime')
    if not isinstance(runtime,dict):
        raise ValueError('actual runtime identity required')
    allowed_runtime = ('system','machine','qpa','qt','pyqt','python','release')
    selected = {}
    for key in allowed_runtime:
        value = runtime.get(key)
        if key=='release' and value is None:
            continue
        if not isinstance(value,str) or not TOKEN.fullmatch(value):
            raise ValueError('bounded runtime token required')
        selected[key] = value
    if selected['system'] not in ('Windows','FreeBSD','Linux','Darwin') or selected['qpa'] != 'offscreen':
        raise ValueError('known offscreen runtime required; no physical-display claim')
    static,frames = report.get('screenshots'),report.get('animation_frames')
    version2 = report['format']==FORMAT_V2
    if (not isinstance(static,list) or not 1 <= len(static) <= MAX_STATIC
            or (version2 and len(static)!=5)
            or not isinstance(frames,list) or not (2 if version2 else 4) <= len(frames) <= MAX_FRAMES
            or report.get('animation_frame_count') != len(frames)):
        raise ValueError('bounded static images and animation frames required')
    sequences = {}
    if version2:
        entries = report.get('transition_sequences')
        if not isinstance(entries,list) or len(entries)!=2:
            raise ValueError('two explicit transition sampling sequences required')
        for entry in entries:
            if not isinstance(entry,dict) or entry.get('direction') not in ('collapse','expand') or entry['direction'] in sequences:
                raise ValueError('unique collapse/expand sampling sequences required')
            start = number(entry.get('sampling_start_elapsed_ms'))
            dispatch = entry.get('mouse_release_dispatch')
            if not isinstance(dispatch,dict):
                raise ValueError('mouse release dispatch observation interval required')
            before = number(dispatch.get('before_elapsed_ms'))
            after = number(dispatch.get('after_elapsed_ms'))
            finished = number(entry.get('finished_elapsed_ms'))
            encoding_start = number(entry.get('png_encoding_started_elapsed_ms'))
            encoding_end = number(entry.get('png_encoding_finished_elapsed_ms'))
            if (not start <= before <= after <= finished <= encoding_start <= encoding_end
                    or number(entry.get('sampling_interval_ms'))!=32
                    or type(entry.get('frame_count')) is not int or not 1 <= entry['frame_count'] <= 16):
                raise ValueError('bounded deferred-encoding sampling sequence required')
            sequences[entry['direction']] = {'origin_ms':start,'dispatch':{'before_elapsed_ms':before,'after_elapsed_ms':after},
                                             'finished_ms':finished,'frame_count':entry['frame_count']}
    images, names, total = [],set(),0
    for entry in static+frames:
        if not isinstance(entry,dict):
            raise ValueError('image evidence object required')
        name = entry.get('file')
        if not isinstance(name,str) or not NAME.fullmatch(name) or name in names:
            raise ValueError('unique contained PNG basename required')
        names.add(name)
        digest = entry.get('sha256')
        if not isinstance(digest,str) or not SHA.fullmatch(digest):
            raise ValueError('PNG SHA256 evidence required')
        data = _read_file(directory,name,MAX_IMAGE_BYTES,identity)
        total += len(data)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError('total image bound exceeded')
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('PNG SHA256 mismatch')
        dimensions = _png_dimensions(data)
        if (any(type(entry.get(key)) is not int for key in ('width','height'))
                or dimensions != (entry['width'],entry['height']) or dimensions != (800,600)):
            raise ValueError('PNG dimensions differ from recorded evidence')
        request,ready = observation(entry.get('requested_observation')),observation(entry.get('ready_observation'))
        if ready['elapsed_ms'] < request['elapsed_ms']:
            raise ValueError('ready observation precedes request')
        result = {'file':name,'width':dimensions[0],'height':dimensions[1],'sha256':digest,
                  'request':request,'ready':ready,'data':data}
        if entry in frames:
            direction = entry.get('direction')
            if direction not in ('collapse','expand') or entry.get('route') != 'capture':
                raise ValueError('capture-route collapse/expand direction required')
            request_ms = number(entry.get('request_after_sampling_start_ms' if version2 else 'request_after_release_ms'))
            ready_ms = number(entry.get('ready_after_sampling_start_ms' if version2 else 'ready_after_release_ms'))
            if ready_ms < request_ms or abs((ready_ms-request_ms)-(ready['elapsed_ms']-request['elapsed_ms'])) > .02:
                raise ValueError('recorded timing observations disagree')
            intermediate = .01 < request['progress'] < .99 and .01 < ready['progress'] < .99
            if type(entry.get('intermediate_observation_interval')) is not bool or entry['intermediate_observation_interval'] != intermediate:
                raise ValueError('intermediate evidence flag disagrees with observations')
            result.update({'direction':direction,'request_ms':request_ms,'ready_ms':ready_ms,
                           'clock':'sampling_start' if version2 else 'release','intermediate':intermediate})
            if version2:
                result.update({'origin_ms':sequences[direction]['origin_ms'],'dispatch':sequences[direction]['dispatch']})
            else:
                result['scheduled_ms'] = number(entry.get('scheduled_offset_ms'))
        images.append(result)
    static_images,animation = images[:len(static)],images[len(static):]
    counts = report.get('observed_intermediate_intervals')
    for direction in ('collapse','expand'):
        group = [frame for frame in animation if frame['direction']==direction]
        if len(group)<(1 if version2 else 2) or (version2 and len(group)>16):
            raise ValueError('both animation directions required')
        observed = [frame[key]['progress'] for frame in group for key in ('request','ready')]
        sign = -1 if direction=='collapse' else 1
        if any(sign*(b-a)<-.000001 for a,b in zip(observed,observed[1:])):
            raise ValueError('animation observations reverse direction')
        if any(b['ready_ms']<a['ready_ms'] for a,b in zip(group,group[1:])):
            raise ValueError('recorded ready times must be monotonic')
        release_origins = [frame[key]['elapsed_ms']-frame[offset] for frame in group
                           for key,offset in (('request','request_ms'),('ready','ready_ms'))]
        if max(release_origins)-min(release_origins) > .02:
            raise ValueError('animation frames disagree about their common '+('sampling' if version2 else 'release')+' origin')
        if version2:
            sequence = sequences[direction]
            if (any(abs(origin-sequence['origin_ms'])>.02 for origin in release_origins)
                    or len(group)!=sequence['frame_count']
                    or group[-1]['ready']['elapsed_ms']>sequence['finished_ms']+.02
                    or abs(group[-1]['ready']['progress']-(0 if direction=='collapse' else 1))>.001):
                raise ValueError('frame evidence disagrees with its declared sampling origin, count or terminal state')
        if any(b['request']['elapsed_ms']<a['ready']['elapsed_ms'] for a,b in zip(group,group[1:])):
            raise ValueError('sequential grab observation intervals overlap or reverse')
        actual = sum(frame['intermediate'] for frame in group)
        if not isinstance(counts,dict) or type(counts.get(direction)) is not int or counts[direction] != actual or actual<1:
            raise ValueError('both directions need genuine intermediate observations')
    return selected,static_images,animation,hashlib.sha256(raw).hexdigest()


STYLE = '''
:root{font-family:"Segoe UI",system-ui,sans-serif;color-scheme:light;background:#ffffff;color:#000000}
*{box-sizing:border-box}body{margin:0}header,main,footer{max-width:1680px;margin:auto;padding:24px}
h1{font-size:32px;margin:8px 0}p{line-height:1.7;color:#444444}button,input{font:inherit}
button{border:1px solid #555555;border-radius:5px;padding:8px 16px;background:#333333;color:#ffffff;cursor:pointer}
button:focus-visible,input:focus-visible{outline:3px solid #000000;outline-offset:3px}
.players,.gallery{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin:20px 0}
article,figure{margin:0;border:1px solid #cccccc;background:#eeeeee;min-width:0}h2,figcaption{padding:12px 16px;margin:0;font-size:18px}
img{display:block;width:100%;height:auto;background:#000000}.controls{display:flex;align-items:center;gap:12px;padding:12px;flex-wrap:wrap}
input[type=range]{flex:1;min-width:120px;accent-color:#666666}.caption{font-size:12px;padding:0 16px 16px;margin:0;overflow-wrap:anywhere}
.note{border-left:4px solid #666666;padding:12px 16px;background:#eeeeee}.counter{font-size:12px;min-width:60px}
footer{font-size:12px;overflow-wrap:anywhere;color:#444444}@media(max-width:900px){.players,.gallery{grid-template-columns:1fr}header,main,footer{padding:16px}}
'''

SCRIPT = '''
"use strict";
const recordings=JSON.parse(document.getElementById("taskbar-data").textContent);
for(const direction of ["collapse","expand"]){
 const frames=recordings[direction],root=document.getElementById(direction),image=root.querySelector("img"),
 button=root.querySelector("button"),range=root.querySelector("input"),caption=root.querySelector(".caption"),counter=root.querySelector(".counter");
 let index=0,playing=false,timer=null;const assets=[];
 button.disabled=true;range.disabled=true;
 function stop(){playing=false;clearTimeout(timer);timer=null;button.textContent="播放 / Play";button.setAttribute("aria-pressed","false");}
 function show(){const frame=frames[index];image.src=frame.src;range.value=String(index);counter.textContent=(index+1)+" / "+frames.length;
  const clock=frame.clock==="sampling_start"?"Observed relative to sampling origin":"Observed after release";
  const dispatch=frame.clock==="sampling_start"?" Sampling origin at elapsed "+frame.origin_ms+" ms; mouse release dispatch observed at elapsed ["+frame.dispatch.before_elapsed_ms+", "+frame.dispatch.after_elapsed_ms+"] ms.":"";
  caption.textContent=clock+": request "+frame.request_ms+" ms → ready "+frame.ready_ms+" ms; progress "+frame.request.progress.toFixed(4)+" → "+frame.ready.progress.toFixed(4)+". Viewfinder "+frame.request.viewfinder_width.toFixed(2)+"×"+frame.request.viewfinder_height.toFixed(2)+" → "+frame.ready.viewfinder_width.toFixed(2)+"×"+frame.ready.viewfinder_height.toFixed(2)+". Render instant lies within the observation interval."+dispatch;}
 function schedule(){if(index>=frames.length-1){stop();return;}const current=frames[index],next=frames[index+1];
  const delay=next.ready_ms-current.ready_ms;
  timer=setTimeout(()=>{if(!playing)return;index+=1;show();schedule();},delay);}
 button.addEventListener("click",()=>{if(playing){stop();return;}if(index===frames.length-1){index=0;show();}playing=true;button.textContent="暂停 / Pause";button.setAttribute("aria-pressed","true");schedule();});
 range.addEventListener("input",()=>{stop();index=Number(range.value);show();});show();
 Promise.all(frames.map(frame=>new Promise((resolve,reject)=>{const asset=new Image();assets.push(asset);
  asset.onload=()=>resolve();asset.onerror=()=>reject(new Error("PNG decode failed"));asset.src=frame.src;
  if(asset.complete&&asset.naturalWidth>0)resolve();
 }))).then(()=>{button.disabled=false;range.disabled=false;show();}).catch(()=>{caption.textContent="原始 PNG 解码失败，播放保持禁用。 / Original PNG decoding failed; playback stays disabled.";});
}
'''


def render_gallery(runtime,static,frames,report_sha):
    def uri(image):
        return 'data:image/png;base64,'+base64.b64encode(image['data']).decode('ascii')
    cards = ''.join('<figure><figcaption>'+html.escape(image['file'])+'</figcaption><img alt="'+html.escape(image['file'])+'" width="'+str(image['width'])+'" height="'+str(image['height'])+'" src="'+uri(image)+'"></figure>' for image in static)
    recordings,players = {},[]
    for direction,title in (('collapse','左滑收起 / Collapse'),('expand','右滑展开 / Expand')):
        group = [frame for frame in frames if frame['direction']==direction]
        recordings[direction] = [{key:frame[key] for key in ('file','request_ms','ready_ms','scheduled_ms','intermediate','request','ready','clock','origin_ms','dispatch') if key in frame} | {'src':uri(frame)} for frame in group]
        players.append('<article id="'+direction+'"><h2>'+title+'</h2><img alt="'+title+' actual Qt sample"><div class="controls"><button type="button" aria-pressed="false">播放 / Play</button><input type="range" min="0" max="'+str(len(group)-1)+'" value="0" step="1" aria-label="'+title+' frame"><span class="counter"></span></div><p class="caption"></p></article>')
    data = json.dumps(recordings,ensure_ascii=True,separators=(',',':'),allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    identity = ' / '.join(runtime[key] for key in ('system','machine','qt','qpa'))
    clock_note = ('V2 sampling-origin clock: offsets are measured from sampling start; the mouse-release dispatch is a separate observation interval.'
                  if frames[0]['clock']=='sampling_start' else
                  'V1 release-origin clock: offsets follow the recorded mouse-release return origin.')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; connect-src \'none\'; base-uri \'none\'; form-action \'none\'"><title>DaOS Taskbar 实际动画审阅</title><style>'+STYLE+'</style><header><h1>DaOS · Taskbar 动画与取景联动</h1><p>'+html.escape(identity)+'</p><p class="note">实际 Qt 原始帧，Python 参考相机服务与合成预览；零拍摄、没有原生进程。不代表实体相机、物理屏幕或新启动镜像验证。</p><p>播放器按记录的 ready 时间差展示采样帧，末帧停止，可暂停或拖动逐帧查看。无补帧、插值或 FPS 承诺；浏览器调度不保证复现原始动画速度。每帧标注请求与就绪观察区间，不能当作精确渲染时刻。</p></header><main><section class="players">'+''.join(players)+'</section><h2>静态实际界面 / Static Qt views</h2><section class="gallery">'+cards+'</section></main><footer>review.json SHA256: '+report_sha+'</footer><script id="taskbar-data" type="application/json">'+data+'</script><script>'+SCRIPT+'</script></html>'
    page = page.replace('</header>','<p>'+html.escape(clock_note)+'</p></header>',1)
    return page.encode('utf-8')


def create_gallery(review_dir,output):
    output = Path(output).absolute()
    parent,identity = _directory(output.parent)
    output = parent/output.name
    if output.suffix.lower() != '.html' or os.path.lexists(output):
        raise ValueError('new HTML output required; existing output refused')
    runtime,static,frames,report_sha = load_review(review_dir)
    data = render_gallery(runtime,static,frames,report_sha)
    descriptor,name = tempfile.mkstemp(prefix='.DaOS-taskbar-review-',suffix='.tmp',dir=parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        _check_directory(parent,identity)
        os.link(temporary,output)
        if _identity(temporary.lstat()) != _identity(output.lstat()) or _is_link(output.lstat()):
            raise ValueError('published HTML identity changed')
    finally:
        temporary.unlink(missing_ok=True)
    return {'format':'DaOS-taskbar-review-gallery-v1','created':True,'output':str(output),
            'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'source_review_sha256':report_sha,
            'runtime':runtime,'static_count':len(static),'animation_frame_count':len(frames),
            'images_embedded_unchanged':True,'review_format':FORMAT_V2 if frames[0]['clock']=='sampling_start' else FORMAT,
            'playback_clock':'recorded '+('ready_after_sampling_start_ms' if frames[0]['clock']=='sampling_start' else 'ready_after_release_ms')+' differences',
            'no_shutter':True,'no_native_camera':True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        result = create_gallery(args.review_dir,args.output)
    except (OSError,ValueError,UnicodeError) as error:
        parser.exit(2,'Taskbar gallery refused: '+str(error)+'\n')
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
