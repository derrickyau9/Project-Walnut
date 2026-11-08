"""Regression tests for supported guard/adjustment parameter rejection."""
from pathlib import Path
import json
from thermal_bridge import build
R=Path(__file__).resolve().parents[1];base=json.loads((R/'source/parameters.json').read_text())['thermal_bridge']
cases=[('disable unsupported module',{'enabled':False}),('shrink guard rear',{'guard_rear_z':-10}),('shrink guard front',{'guard_front_z':-15}),('understate verified chip width',{'chip_width_lower_bound':40}),('inconsistent slot stroke',{'slot_length':6.7}),('negative material thickness',{'leaf_thickness':-1}),('route into guard',{'author_x':-20}),('set nominal at hard limit',{'front_z':-5})]
rows=[]
for name,change in cases:
    rejected=False
    try:build(base|change)
    except (AssertionError,ValueError):rejected=True
    rows.append({'check':name,'expected':'rejected','pass':rejected})
for z in [-4.5,-3,-2,-1.5]:
    t=build(base|{'front_z':z});rows.append({'check':f'usable setting {z}','expected':'valid connected fixed bus','pass':t['fixed_bus'].val().isValid() and len(t['fixed_bus'].solids().vals())==1})
t=build(base|{'clamp_shaft_diameter':2.9,'clamp_clearance_diameter':3.1,'slot_length':6.9})
v=sum(s.Volume() for s in t['screw'].intersect(t['nut']).solids().vals())
rows.append({'check':'nut bore follows changed clamp clearance','intersection_mm3':v,'pass':v<1e-6})
t=build(base|{'collector_front_z':-22,'guard_rear_z':-22})
rows.append({'check':'collector movement preserves connected bus','pass':t['fixed_bus'].val().isValid() and len(t['fixed_bus'].solids().vals())==1})
result={'revision':'0.3','cases':rows,'all_passed':all(r['pass'] for r in rows),'limitation':'These regression cases do not certify arbitrary parameter combinations; full geometric audits remain required.'}
(R/'validation/parameter_regression.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
assert result['all_passed']
