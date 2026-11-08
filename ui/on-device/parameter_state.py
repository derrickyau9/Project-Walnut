"""Pure, bounded parameter intent model shared by controls and profile validation."""
from __future__ import annotations

import math
from camera_capabilities import exposure_steps

KEYS = frozenset(('requested_exposure_us', 'requested_analogue_gain', 'sensor_mode'))
DEFAULT = {'requested_exposure_us':10000, 'requested_analogue_gain':1.0, 'sensor_mode':'sim-small'}
GAIN_STEPS = tuple(round(2 ** (i/3), 2) for i in range(13))


def validate_patch(patch, *, full=False):
    if not isinstance(patch, dict) or not patch or not set(patch) <= KEYS or (full and set(patch) != KEYS):
        raise ValueError('参数字段无效。')
    result = dict(patch)
    if 'requested_exposure_us' in result:
        value=result['requested_exposure_us']
        if type(value) is not int or not 100 <= value <= 30000000: raise ValueError('快门范围为 100–30000000 μs。')
    if 'requested_analogue_gain' in result:
        value=result['requested_analogue_gain']
        if type(value) not in (int,float) or not math.isfinite(value) or not 1 <= value <= 16: raise ValueError('增益范围为 1–16×。')
        result['requested_analogue_gain']=round(float(value),2)
    if 'sensor_mode' in result and result['sensor_mode'] not in ('sim-small','sim-full'):
        raise ValueError('当前相机不支持该图像模式。')
    return result


class SettingsIntent:
    def __init__(self):
        self.desired=dict(DEFAULT)
        self.confirmed={}
        self.revision=0
        self.confirmed_revision=0

    def adopt(self, values):
        values=validate_patch(values,full=True)
        if self.confirmed != values or self.desired != values:self.revision+=1
        self.confirmed=values
        self.desired=dict(values)
        self.confirmed_revision=self.revision

    def edit(self, patch):
        patch=validate_patch(patch)
        updated=dict(self.desired,**patch)
        if updated != self.desired:
            self.desired=updated
            self.revision+=1
        return self.revision

    def acknowledge(self, values, revision):
        self.confirmed=validate_patch(values,full=True)
        self.confirmed_revision=revision

    def difference(self, baseline=None):
        baseline=self.confirmed if baseline is None else baseline
        return {key:value for key,value in self.desired.items() if baseline.get(key) != value}

    def nudge(self, key, ticks):
        key={'exposure':'requested_exposure_us','gain':'requested_analogue_gain'}.get(key,key)
        if type(ticks) is not int or not -1000000 <= ticks <= 1000000: raise ValueError('拨轮刻度无效。')
        if not ticks: return self.revision
        if key == 'requested_exposure_us':
            values=[step['us'] for step in exposure_steps()]
        elif key == 'requested_analogue_gain':
            values=GAIN_STEPS
        else: raise ValueError('该参数不能用拨轮调整。')
        current=self.desired[key]
        if current in values:target=values.index(current)+ticks
        else:
            forward=(lambda value:value<current) if key=='requested_exposure_us' else (lambda value:value>current)
            if ticks>0:
                target=next((i for i,value in enumerate(values) if forward(value)),len(values)-1)+ticks-1
            else:
                target=next((i for i in range(len(values)-1,-1,-1) if not forward(values[i])),0)+ticks+1
        return self.edit({key:values[max(0,min(len(values)-1,target))]})
