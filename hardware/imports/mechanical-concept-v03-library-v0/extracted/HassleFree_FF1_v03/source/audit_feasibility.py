"""All-pair solid interference plus conservative, continuous service sweeps.

Run after build_camera.py. These checks cover modeled geometry only, not
unmodeled screws, clips, wiring, full electronic components or actual lenses.
"""
import itertools,json
from pathlib import Path
import cadquery as cq
from thermal_bridge import build as build_thermal
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
R=Path(__file__).resolve().parents[1]
M=json.loads((R/'exports/assembly_manifest.json').read_text())
S={p['name']:cq.importers.importStep(str(R/'exports/parts'/(p['name']+'.step'))) for p in M}

def bbox(s):
    b=Bnd_Box();BRepBndLib.AddOptimal_s(s.val().wrapped,b,False,False);return cq.BoundBox(b)
def volume(s):return sum(v.Volume() for v in s.solids().vals())
def intersect(a,b):
    aa=bbox(a);bb=bbox(b)
    if any(getattr(aa,d+'max')<getattr(bb,d+'min')-1e-7 or getattr(bb,d+'max')<getattr(aa,d+'min')-1e-7 for d in ['x','y','z']):return 0
    return volume(a.intersect(b))
def union_names(names):
    return cq.Workplane(obj=cq.Compound.makeCompound([S[n].val() for n in names]))
def swept_prisms(shape,delta):
    """Union of swept AABBs for each individual solid, a conservative superset.
    Splitting into convex AABBs preserves holes only between distinct solids.
    If this superset is clear, the true straight translation is clear too.
    """
    chunks=[]
    for solid in shape.solids().vals():
        b=bbox(cq.Workplane(obj=solid));lo=[b.xmin,b.ymin,b.zmin];hi=[b.xmax,b.ymax,b.zmax]
        lower=[min(l,l+d) for l,d in zip(lo,delta)];upper=[max(h,h+d) for h,d in zip(hi,delta)]
        d=[h-l for l,h in zip(lower,upper)];c=[(h+l)/2 for l,h in zip(lower,upper)]
        chunks.append(cq.Workplane('XY').box(*d).translate(tuple(c)).val())
    return cq.Workplane(obj=cq.Compound.makeCompound(chunks))

allpairs={}
for variant in ['E','EF']:
    names=[p['name'] for p in M if p['group'] not in ['E','EF'] or p['group']==variant]
    bad=[];count=0
    for a,b in itertools.combinations(names,2):
        count+=1;v=intersect(S[a],S[b])
        if v>1e-6:bad.append({'a':a,'b':b,'intersection_mm3':v})
    allpairs[variant]={'pair_count':count,'interferences':bad,'pass':not bad}

service=[]
def sweep_check(name,moving,delta,stationary,removed,moving_shape=None):
    swept=swept_prisms(moving_shape if moving_shape is not None else union_names(moving),delta)
    bad=[]
    for n in stationary:
        v=intersect(swept,S[n])
        if v>1e-6:bad.append({'stationary':n,'conservative_intersection_mm3':v})
    service.append({'check':name,'moving':moving,'delta_mm':delta,'removed_first':removed,'method':'Continuous union of swept per-solid AABBs; conservative sufficient test','interferences':bad,'pass':not bad})

fixed=[p['name'] for p in M if p['group'] not in ['EF']]
sweep_check('Battery downward service', ['17_replaceable_battery_envelope'],(0,-110,0),
    [n for n in fixed if n not in ['17_replaceable_battery_envelope','18_battery_service_door']],['18_battery_service_door'])
sweep_check('Storage outward service',['19_removable_storage_envelope'],(80,0,0),
    [n for n in fixed if n not in ['19_removable_storage_envelope','20_storage_service_door']],['20_storage_service_door'])
# These sensor parts are constant XY cross-sections extruded along Z. Recover
# each top profile and re-extrude it over its original thickness plus travel.
# Verify the original-thickness reconstruction before trusting a full sweep.
sensor_names=['27_removable_sensor_bridge','10_independent_sensor_carrier','11_sensor_package_placeholder','12_sensor_active_36x24','13_three_point_sensor_spacers']
sensor_sweeps=[];reconstruction=[]
for name in sensor_names:
    for solid in S[name].solids().vals():
        original=cq.Workplane(obj=solid);b=bbox(original)
        wires=original.faces('>Z').wires().vals()
        rebuild=cq.Workplane('XY').add(wires).toPending().extrude(-b.zlen)
        error=volume(original.cut(rebuild))+volume(rebuild.cut(original))
        reconstruction.append({'part':name,'symmetric_difference_mm3':error})
        if error>1e-5:raise ValueError('Cannot certify a constant-section sweep: '+name)
        sensor_sweeps.append(cq.Workplane('XY').add(wires).toPending().extrude(-b.zlen-110).val())
sw=cq.Workplane(obj=cq.Compound.makeCompound(sensor_sweeps))
sensor_kept=[p['name'] for p in M if p['name'] not in sensor_names and p['group'] not in ['electronics','thermal','rear','EF']]
sensor_bad=[]
for name in sensor_kept:
    v=intersect(sw,S[name])
    if v>1e-6:sensor_bad.append({'stationary':name,'continuous_sweep_intersection_mm3':v})
service.append({'check':'Sensor bridge rear service','moving':sensor_names,'delta_mm':[0,0,-110],
 'removed_first':['Rear cover/display/control caps','Control PCB, component envelopes, rails and spacers','Main processor-board envelope','Whole thermal module, including adjustable landing and clamp envelopes','All relevant wires and retaining hardware, currently unmodeled'],
 'method':'Continuous exact Z sweep of reconstructed constant-section solids; reconstruction symmetric difference checked',
 'reconstruction_checks':reconstruction,
 'interferences':sensor_bad,'pass':not sensor_bad})

control_names=['16_control_board_provisional_envelope','26_control_board_component_envelopes','28_removable_control_mount_rails','29_control_board_mount_spacers']
control_sweeps=[];control_recon=[]
for name in control_names:
    for solid in S[name].solids().vals():
        original=cq.Workplane(obj=solid);b=bbox(original);wires=original.faces('>Z').wires().vals()
        rebuilt=cq.Workplane('XY').add(wires).toPending().extrude(-b.zlen)
        error=volume(original.cut(rebuilt))+volume(rebuilt.cut(original));control_recon.append({'part':name,'symmetric_difference_mm3':error})
        if error>1e-5:raise ValueError('Cannot certify control-section sweep: '+name)
        control_sweeps.append(cq.Workplane('XY').add(wires).toPending().extrude(-b.zlen-110).val())
cs=cq.Workplane(obj=cq.Compound.makeCompound(control_sweeps));control_bad=[]
for p in M:
    if p['name'] in control_names or p['group'] in ['rear','EF']:continue
    v=intersect(cs,S[p['name']])
    if v>1e-6:control_bad.append({'stationary':p['name'],'continuous_sweep_intersection_mm3':v})
service.append({'check':'Control PCB and rails rear service','moving':control_names,'delta_mm':[0,0,-110],
 'removed_first':['Rear cover/display/control caps','Unplug wires and release retaining hardware, currently unmodeled'],
 'method':'Continuous exact Z sweep of verified constant-section modeled solids',
 'reconstruction_checks':control_recon,'interferences':control_bad,'pass':not control_bad})
thermal_names=[p['name'] for p in M if p['group']=='thermal']
parameters=json.loads((R/'source/parameters.json').read_text())
tb=build_thermal(parameters['thermal_bridge'])
bus_chunks=[tb[k].mirror('YZ') if parameters.get('right_hand_grip',True) else tb[k] for k in ['collector','beam','fixed']]
bus_union=bus_chunks[0].union(bus_chunks[1]).union(bus_chunks[2])
bus_error=volume(bus_union.cut(S['14_heat_spreader_placeholder']))+volume(S['14_heat_spreader_placeholder'].cut(bus_union))
assert bus_error<1e-5,'Construction subparts must reconstruct the integral fixed bus.'
thermal_sweep_parts=cq.Workplane(obj=cq.Compound.makeCompound([v.val() for v in bus_chunks]+[S[n].val() for n in thermal_names if n!='14_heat_spreader_placeholder']))
sweep_check('Whole thermal module rear service',thermal_names,(0,0,-110),
 [p['name'] for p in M if p['group'] not in ['thermal','electronics','rear','EF']],
 ['Rear cover/display/control caps','Control electronics, rails and main-board envelope','Thermal mount retaining hardware and cables, currently unmodeled'],thermal_sweep_parts)
service[-1]['method']='Continuous conservative AABB sweeps of construction subparts; integral-bus reconstruction verified.'
service[-1]['bus_reconstruction_symmetric_difference_mm3']=bus_error

carrier=S['10_independent_sensor_carrier'];package=S['11_sensor_package_placeholder']
seat=intersect(carrier,package.translate((0,0,-.001)))/.001
gap=package.val().distance(S['14_heat_spreader_placeholder'].val())
result={'revision':'0.3','all_pairs':allpairs,'service_paths':service,
 'sensor_placeholder_seat_area_mm2':seat,'sensor_placeholder_to_thermal_module_min_clearance_mm':gap,'original_axial_reference_gap_mm':19,
 'mount_radial_outer_ligament_mm':36-64/2-3.2/2,
 'geometric_checks_passed':all(x['pass'] for x in allpairs.values()) and all(x['pass'] for x in service),
 'engineering_release_ready':False,
 'limits':['Model intersections are not tolerance-stack proof.','Real populated PCB, mating connectors, wires and selected sensor package are not provided.','Service checks omit all unmodeled retaining hardware and cables.','Thermal route ends at an isolated side landing; actual sensor heat interface is unresolved.','HUMI rings are axial datum references, not functional E/EF interfaces.']}
(R/'validation/feasibility_audit.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
if not result['geometric_checks_passed']:raise SystemExit('Feasibility audit found modeled interference.')
