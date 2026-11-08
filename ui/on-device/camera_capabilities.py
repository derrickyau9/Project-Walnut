"""Capability projection for the UI. Unknown hardware defaults to unavailable."""
from __future__ import annotations

import math


def capability_model(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    simulated = raw.get('simulated') is True
    connected = bool(raw)
    def feature(available, reason='', **extra):
        return dict(available=bool(available), supported=bool(available),
                    simulated=simulated if available else False, reason=reason, **extra)
    pending = '相机能力尚未确认'
    result = {
        'schema_version': 1, 'device': str(raw.get('device', '未连接相机')),
        'simulated': simulated, 'authority_confirmed': connected,
        'exposure': feature(connected, '' if connected else pending, unit='us',
                            minimum=raw.get('exposure_us', {}).get('min', 100),
                            maximum=raw.get('exposure_us', {}).get('max', 30000000)),
        'gain': feature(connected, '' if connected else pending, unit='x',
                        minimum=raw.get('analogue_gain', {}).get('min', 1.),
                        maximum=raw.get('analogue_gain', {}).get('max', 16.)),
        'capture': feature(connected and 'dng' in raw.get('output_formats', []),
                           '' if connected else pending),
        'preview': feature(simulated, '独立合成取景，未接入实体传感器' if simulated else '实时图像源未接入'),
        'playback': feature(connected and raw.get('retained_captures', {}).get('listing') is True),
        'workflow': feature(connected, '' if connected else pending),
        'grid': feature(True), 'clean_view': feature(True),
        'modes': raw.get('modes', []), 'formats': raw.get('output_formats', []),
    }
    missing = {
        'iso': '传感器增益尚未完成 ISO 标定',
        'aperture': '未接入电子光圈控制', 'white_balance': '尚未完成真实图像白平衡和色彩管线',
        'auto_exposure': '未接入真实图像测光', 'focus': '未接入可驱动镜头',
        'autofocus': '未接入镜头驱动及自动对焦闭环',
        'tracking': '需要实时图像、检测计算和镜头驱动',
        'burst': '当前相机服务仅支持单张在途拍摄',
        'pre_capture': '需要传感器连续读出与原生缓存',
        'video': '未接入真实音画采集和编码服务',
        'histogram': '需要与采集帧同步的真实图像分析',
        'peaking': '需要真实图像和焦点评估',
        'level': '未检测到 IMU', 'stabilization': '未接入防抖硬件',
        'flash': '未接入闪光同步硬件', 'hdr': '当前屏幕按 SDR 使用',
        'battery': '未接入电池遥测',
    }
    result.update({name: feature(False, reason) for name, reason in missing.items()})
    return result


def exposure_steps():
    # Standard rounded photographic 1/3-stop values, longest first.
    seconds = [30,25,20,15,13,10,8,6,5,4,3.2,2.5,2,1.6,1.3,1,.8,.6,.5,.4,.3]
    denominators = [4,5,6,8,10,13,15,20,25,30,40,50,60,80,100,125,160,200,250,320,
                    400,500,640,800,1000,1250,1600,2000,2500,3200,4000,5000,6400,8000,10000]
    return [dict(label=(('%g' % value)+' s'), us=round(value*1000000)) for value in seconds] + [
        dict(label='1/'+str(value), us=round(1000000/value)) for value in denominators]
