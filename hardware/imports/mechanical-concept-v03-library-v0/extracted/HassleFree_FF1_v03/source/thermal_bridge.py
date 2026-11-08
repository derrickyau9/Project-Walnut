"""Independent, adjustable lateral thermal-interface CONCEPT.

No sensor connection is made. This mechanically spans the 19 mm axial region
to an isolated landing beside the sensor, not a closed or validated heat path.
All dimensions except public chip XY are provisional packaging choices.
Authoring coordinates are mirrored by build_camera.add() for right-hand output.
"""
import cadquery as cq

DEFAULTS={
 'enabled':True,'front_z':-2.0,'front_min_z':-5.0,'front_max_z':-1.0,
 'usable_front_min_z':-4.5,'usable_front_max_z':-1.5,
 'collector_front_z':-21.0,'author_x':-42.0,
 'chip_width_lower_bound':41.071,'chip_height_lower_bound':30.815,
 'guard_xy_margin':2.0,'guard_rear_z':-21.0,'guard_front_z':0.0,
 'fixed_leaf_width':10.6,'slider_width':8.0,'guide_clear_width':8.6,
 'leaf_thickness':1.5,'clamp_shaft_diameter':2.5,'clamp_clearance_diameter':2.7,
 'clamp_z':-10.0,'slot_length':6.5,'mount_y':25.5,
 'status':'CONDITIONAL NONCONTACT ROUTING CONCEPT; NO SENSOR HEAT INTERFACE OR THERMAL PERFORMANCE CLAIM'
}

def build(config=None,front_z=None):
    p=DEFAULTS|dict(config or {}); f=p['front_z'] if front_z is None else float(front_z)
    assert p.get('enabled',True) is True,'This builder requires its thermal module; use the preserved v0.2 for no-module geometry.'
    assert p['front_min_z']<p['front_max_z']<0
    assert p['front_min_z']<p['usable_front_min_z']<=p['usable_front_max_z']<p['front_max_z']
    if front_z is None:
        assert p['usable_front_min_z']<=f<=p['usable_front_max_z'],'Nominal setting must keep at least the chosen clearance from slot hard limits. Audit overrides can inspect the limits.'
    assert p['front_min_z']<=f<=p['front_max_z']<0,'Nominal adjustment stays behind active plane Z=0.'
    x=p['author_x']; ymount=p['mount_y'];c=p['collector_front_z'];t=p['leaf_thickness']
    back_y=6.5;fixed_front_y=back_y+t;slider_front_y=back_y+2*t;guide_front_y=slider_front_y+1
    fixed_top=p['front_min_z']+1
    assert c<fixed_top and t>0 and p['fixed_leaf_width']>p['guide_clear_width']>p['slider_width']>p['clamp_clearance_diameter']+2
    assert p['guide_clear_width']>6.2 and p['clamp_clearance_diameter']>p['clamp_shaft_diameter']>0
    assert p['clamp_clearance_diameter']<4,'Reserved nut envelope must retain radial wall.'
    assert abs(p['slot_length']-p['clamp_shaft_diameter']-(p['front_max_z']-p['front_min_z']))<1e-9
    assert abs(p['clamp_z']-((p['front_min_z']+p['front_max_z'])/2-7))<1e-9
    assert p['clamp_clearance_diameter']<=p['slot_length']<10
    assert p['front_min_z']-14>=c+.5,'Slider must not reach the collector.'
    assert p['chip_width_lower_bound']>=41.071 and p['chip_height_lower_bound']>=30.815
    assert p['guard_xy_margin']>=0 and abs(p['guard_rear_z']-c)<1e-9 and p['guard_front_z']==0,'Guard must cover the complete collector-front-to-Z=0 routing bay.'
    assert x+p['fixed_leaf_width']/2<-(p['chip_width_lower_bound']/2+p['guard_xy_margin']),'Lateral route must stay outside the entire guarded XY corridor.'
    def box(w,h,d,x0=0,y0=0,z0=0):return cq.Workplane('XY').box(w,h,d).translate((x0,y0,z0))
    def cyl(r,z0,z1,x0=0,y0=0):return cq.Workplane('XY').circle(r).extrude(z1-z0).translate((x0,y0,z0))
    def ycyl(r,y0,y1,x0=0,z0=0):return cyl(r,0,y1-y0).rotate((0,0,0),(1,0,0),-90).translate((x0,y0,z0))
    collector=box(56,48,2,0,0,c-1).edges('|Z').fillet(3)
    collector=collector.union(box(56,6,2,0,ymount-.5,c-1))
    collector=collector.union(box(23,10,2,(x-31)/2,10,c-1))
    beam=box(94,5,2,-9,ymount,c-3)
    for xx in [-53,35]:beam=beam.cut(cyl(1.35,c-5,c-1,xx,ymount))
    fixed=box(p['fixed_leaf_width'],t,fixed_top-c,x,back_y+t/2,(fixed_top+c)/2)
    side=(p['fixed_leaf_width']-p['guide_clear_width'])/2
    for sign in [-1,1]:
        xx=x+sign*(p['guide_clear_width']/2+side/2)
        z0=c+1;z1=fixed_top-2
        fixed=fixed.union(box(side,t+1,z1-z0,xx,fixed_front_y+(t+1)/2,(z0+z1)/2))
    fixed=fixed.cut(ycyl(p['clamp_clearance_diameter']/2,back_y-.5,guide_front_y+.5,x,p['clamp_z']))
    sliding=box(p['slider_width'],t,14,x,fixed_front_y+t/2,f-7)
    sliding=sliding.union(box(p['slider_width'],6,2,x,fixed_front_y+3,f-1))
    radius=p['clamp_clearance_diameter']/2;straight=p['slot_length']-2*radius;sc=f-7
    slot=box(2*radius,t+1,straight,x,fixed_front_y+t/2,sc)
    for zz in [sc-straight/2,sc+straight/2]:slot=slot.union(ycyl(radius,fixed_front_y-.5,slider_front_y+.5,x,zz))
    sliding=sliding.cut(slot)
    # Space reservations for a future selected threaded fastener, not printed
    # or machinable screw/nut definitions. No threads or torque are specified.
    screw=ycyl(2.5,back_y-2.5,back_y,x,p['clamp_z']).union(ycyl(p['clamp_shaft_diameter']/2,back_y,slider_front_y+3,x,p['clamp_z']))
    nut=ycyl(3,slider_front_y,slider_front_y+2.5,x,p['clamp_z']).cut(ycyl(p['clamp_clearance_diameter']/2,slider_front_y-.5,slider_front_y+3,x,p['clamp_z']))
    chip=box(p['chip_width_lower_bound'],p['chip_height_lower_bound'],p['guard_front_z']-p['guard_rear_z'],0,0,(p['guard_front_z']+p['guard_rear_z'])/2)
    guard=box(p['chip_width_lower_bound']+2*p['guard_xy_margin'],p['chip_height_lower_bound']+2*p['guard_xy_margin'],p['guard_front_z']-p['guard_rear_z'],0,0,(p['guard_front_z']+p['guard_rear_z'])/2)
    fixed_bus=collector.union(beam).union(fixed)
    assert len(fixed_bus.solids().vals())==1 and fixed_bus.val().isValid(),'Fixed thermal bus must be one connected solid.'
    return {'collector':collector,'beam':beam,'fixed':fixed,'fixed_bus':fixed_bus,'sliding':sliding,'screw':screw,'nut':nut,'chip_corridor':chip,'guard':guard,
      'parameters':p,'front_z':f,'axial_span_mm':f-p['collector_front_z']}
