"""Conditional geometry checks only. No temperature or thermal-resistance model."""
from pathlib import Path
import itertools,json,math
import cadquery as cq
from thermal_bridge import build
R=Path(__file__).resolve().parents[1]
P=json.loads((R/'source/parameters.json').read_text());M=json.loads((R/'exports/assembly_manifest.json').read_text())
S={p['name']:cq.importers.importStep(str(R/'exports/parts'/(p['name']+'.step'))) for p in M}
def V(a):return sum(s.Volume() for s in a.solids().vals())
def overlap(a,b):
    aa=a.val().BoundingBox();bb=b.val().BoundingBox()
    if any(getattr(aa,d+'max')<getattr(bb,d+'min') or getattr(bb,d+'max')<getattr(aa,d+'min') for d in ['x','y','z']):return 0.0
    return V(a.intersect(b))
def pose(s):return s.mirror('YZ') if P.get('right_hand_grip',True) else s
mapping={'14_heat_spreader_placeholder':'fixed_bus','32_thermal_adjustable_landing':'sliding','33_thermal_clamp_screw_reservation':'screw','34_thermal_clamp_nut_reservation':'nut'}
optics=['10_independent_sensor_carrier','11_sensor_package_placeholder','12_sensor_active_36x24','13_three_point_sensor_spacers','27_removable_sensor_bridge']
static={p['name']:S[p['name']] for p in M if p['group'] not in ['thermal','EF']}
results=[]
for z in [-5,-4,-3,-2,-1]:
    t=build(P['thermal_bridge'],z);moving={n:pose(t[k]) for n,k in mapping.items()};bad=[]
    for n,a in moving.items():
        assert a.val().isValid()
        for sn,b in static.items():
            v=overlap(a,b)
            if v>1e-6:bad.append({'a':n,'b':sn,'volume_mm3':v})
    for (na,a),(nb,b) in itertools.combinations(moving.items(),2):
        v=overlap(a,b)
        if v>1e-6:bad.append({'a':na,'b':nb,'volume_mm3':v})
    guard=pose(t['guard']);gv=sum(overlap(a,guard) for a in moving.values())
    optical_gaps={n:min(a.val().distance(S[n].val()) for a in moving.values()) for n in optics}
    route_gap=min(pose(t[k]).val().distance(guard.val()) for k in ['fixed','sliding','screw','nut'])
    joints={'one-piece fixed bus -> slider':pose(t['fixed_bus']).val().distance(pose(t['sliding']).val())}
    results.append({'landing_front_z_mm':z,'axial_span_mm':t['axial_span_mm'],'interferences':bad,'guard_intersection_mm3':gv,
      'clearances_to_existing_optical_parts_mm':optical_gaps,'side_route_to_guard_clearance_mm':route_gap,
      'nominal_joint_surface_gaps_mm':joints,
      'pass':not bad and gv<1e-6 and min(optical_gaps.values())>0.01 and all(d<1e-6 for d in joints.values())})
stops=[]
for z,delta in [(-5,-.2),(-1,.2)]:
    t=build(P['thermal_bridge'],z)
    # Overtravel is tested as a rigid slider shift, outside accepted parameters.
    # With the screw shaft retained, the nominal slot end must block this shift.
    v=overlap(t['sliding'].translate((0,0,delta)),t['screw'])
    stops.append({'nominal_endpoint_z':z,'attempted_extra_travel_mm':delta,'shaft_interference_mm3':v,'pass_if_shaft_is_retained':v>1e-6})
report={'revision':'0.3','classification':'NONCONTACT ROUTING / ADJUSTMENT CONCEPT ONLY','positions':results,'nominal_slot_stop_tests':stops,
 'chip_xy_source':'https://www.sony-semicon.com/files/62/pdf/p-13_IMX410CQK_Flyer.pdf',
 'verified_chip_xy_lower_bound_mm':[41.071,30.815],
 'package_dimensions_and_height_known':False,
 'guard_axial_extent_basis':'Z=-21..0 is the chosen existing routing bay, not a chip/package thickness.',
 'no_sensor_heat_connection':True,'no_thermal_performance_claim':True,'no_direct_optical_part_contact':all(min(x['clearances_to_existing_optical_parts_mm'].values())>0.01 for x in results),
 'engineering_release_ready':False,
 'geometric_checks_passed':all(x['pass'] for x in results) and all(x['pass_if_shaft_is_retained'] for x in stops),
 'conditions':['Only a supplier-approved package/module heat interface can close the sensor heat path.','Current 40x28 sensor placeholder is smaller than the verified chip XY lower bound; no sensor mounting is released.','Clamp screw/nut are fit envelopes, not manufactured threads or validated fastening hardware.','Shared-chassis thermoelastic deformation, joint preload, thermal contact resistance, temperatures, reliability and manufacturability remain unverified.','No contact with or pressure on bare die, optical cover or unapproved package surfaces is authorized by this model.']}
(R/'validation/thermal_audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if not report['geometric_checks_passed']:raise SystemExit('Thermal geometry check failed.')
