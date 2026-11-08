"""HassleFree FF-1 editable mechanical CONCEPT, revision 0.2 feasibility review.

Run with Python 3 and CadQuery 2.7. Units mm. X right in FRONT view,
Y up, Z toward lens. The final right-hand grip is at negative X.
The authoring layout below puts the grip at positive X for convenience;
add() reflects mechanical parts, but rigidly rotates electronics, when exporting
the default right-hand version. No real PCB is mirrored.
The active sensor surface is Z=0. E/EF lens planes are Z=18/44.
This is packaging CAD, not a manufacturing drawing or verified mount design.
No bayonet, thread, optical stack, lens contacts or fit tolerances are supplied.
"""
from pathlib import Path
import json, math, csv
import cadquery as cq
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "source/parameters.json").read_text())
HZ=P["humi_plane_z"]; EZ=P["e_flange_z"]; EFZ=P["ef_flange_z"]
assert P["sensor_plane_z"]==0, "The fixed reference datum is Z=0; move other parts instead."
assert EZ>HZ+2 and EFZ>HZ+2, "Cartridge must have positive length."
OUT = ROOT / "exports"
for d in [OUT / "parts", OUT / "mesh", ROOT / "validation"]:
    d.mkdir(parents=True, exist_ok=True)

def box(w,h,d,x=0,y=0,z=0):
    return cq.Workplane("XY").box(w,h,d).translate((x,y,z))

def rounded(w,h,z0,z1,r=4,x=0,y=0):
    obj = cq.Workplane("XY").box(w,h,z1-z0).edges("|Z").fillet(r)
    return obj.translate((x,y,(z0+z1)/2))

def cyl(r,z0,z1,x=0,y=0):
    return cq.Workplane("XY").circle(r).extrude(z1-z0).translate((x,y,z0))

def ring(ro,ri,z0,z1,x=0,y=0):
    return cyl(ro,z0,z1,x,y).cut(cyl(ri,z0-1,z1+1,x,y))

def ycyl(r,y0,y1,x=0,z=0):
    return cyl(r,0,y1-y0).rotate((0,0,0),(1,0,0),-90).translate((x,y0,z))

def holes(obj,pts,r,z0,z1):
    for x,y in pts:
        obj=obj.cut(cyl(r,z0,z1,x,y))
    return obj

def exact_bbox(shape):
    bound=Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped,bound,False,False)
    return cq.BoundBox(bound)

COLORS = {
    "shell": [0.085,0.105,0.12,1], "grip": [0.033,0.041,0.046,1],
    "frame": [0.47,0.53,0.58,1], "mount": [0.70,0.73,0.75,1],
    "accent": [0.12,0.66,0.60,1], "carrier": [0.36,0.25,0.14,1],
    "sensor": [0.12,0.32,0.37,1], "pcb": [0.05,0.24,0.16,1],
    "black": [0.025,0.035,0.04,1], "glass": [0.025,0.060,0.072,1],
    "battery": [0.18,0.20,0.22,1], "copper": [0.67,0.34,0.18,1]
}
parts=[]
def add(name,shape,color,group,explode=(0,0,0),status="PROVISIONAL"):
    if P.get("right_hand_grip",True):
        if name.startswith(("16_","26_")):
            shape=shape.rotate((0,0,0),(0,0,1),180)
        else:
            shape=shape.mirror("YZ")
        explode=(-explode[0],explode[1],explode[2])
    if len(shape.solids().vals())>1:
        shape=cq.Workplane(obj=cq.Compound.makeCompound(shape.solids().vals()))
    if not shape.val().isValid():
        raise ValueError(f"Invalid B-rep: {name}")
    parts.append(dict(name=name,shape=shape,color=COLORS[color],group=group,explode=list(explode),status=status))
    return shape

# Outer form: main shell and integral grip frame. Exterior shell does not define
# the optical datum. Optical loads go through the inner metal chassis.
body = rounded(P["body_width"],P["body_height"],-42,12,9,-10,2)
grip = rounded(P["grip_width"],P["grip_height"],-40,17,12,62.5,1.5)
shell = body.union(grip)
shell = shell.cut(rounded(108,92,-43,10,7,-10,2))
shell = shell.cut(rounded(33,81,-41,13,9,62.5,1.5))
shell = shell.cut(cyl(37.5,9,19))
shell = shell.cut(box(33,14,28,62,-43,-21)) # battery service opening
# Continuous service tunnel removes the rounded-cavity transition that blocked
# downward extraction in v0.1. 0.5 mm per-side clearance is a concept allowance.
bw,bh,bd=P["battery_envelope"];bx,by,bz=P["battery_center"]
shell=shell.cut(box(bw+1,150,bd+1,bx,by+bh/2+.5-75,bz))
shell = shell.cut(box(10,41,29,-65,0,-16)) # storage service opening
shell = shell.cut(box(10,7,10,-65,-31,-15)) # uncommitted I/O opening
shell=shell.cut(ycyl(2.0,39,50,63,2)).cut(ycyl(2.8,44,56,28,-12))
add("01_front_shell",shell,"shell","shell",(0,0,60))

# Tactile outer wrap is a separate replaceable part, not a solid grip block.
wrap=rounded(42.2,87,-39,15,12.3,63.0,1.5).cut(rounded(41.4,88,-41,17,12,62.5,1.5))
wrap=wrap.intersect(box(25,100,70,78,2,-10))
wrap=wrap.cut(shell) # no unspecified compression/interpenetration
add("02_grip_wrap",wrap,"grip","shell",(40,0,30))

rear=rounded(112,96,-45,-42,9,-10,2).union(rounded(41,89,-43,-40,12,62.5,1.5))
rear=rear.cut(rounded(72.5,49.5,-47,-40,3,-10,3))
rear=rear.cut(shell) # separate mating parts, not overlapping solids
rear_specs=[(36,24,3.2,1.5),(36,11,3.2,1.5),(36,-2,6.5,2.5),(36,-20,3.2,1.5),(59,27,4,1.8)]
for x,y,r,stem in rear_specs:rear=rear.cut(cyl(stem+.3,-46,-39,x,y))
add("03_rear_shell",rear,"shell","rear",(0,0,-78))

# Metal cage with front load plate, rear rails and independent sensor supports.
frame=rounded(104,84,-36,10,5,-9,1).cut(rounded(96,76,-37,6,3,-9,1))
frame=frame.cut(cyl(28.5,5,11))
frame=frame.cut(box(8,39,27,-60,0,-16))
support_pts=[(-25,-20),(25,-20),(0,23)]
# Removable sensor bridge seats in front of its rearward withdrawal plane.
bridge_fix=[(xx,yy) for xx in [-53,35] for yy in [-32.5,32.5]]
for xx in [-55,37]:
    for yy in [-32.5,32.5]:frame=frame.union(box(14,8,4,xx,yy,-5))
frame=holes(frame,bridge_fix,1.35,-8,-2)
# PCB rail seats are narrow enough for the sensor bridge's rear service path.
rail_fix=[(xx,yy) for xx in [-53,35] for yy in [-18,18]]
for xx in [-54.5,36.5]:
    for yy in [-18,18]:frame=frame.union(box(7,8,3,xx,yy,-29.9))
frame=holes(frame,rail_fix,1.35,-34,-27)
bolt_pts=[(P["humi_bolt_circle"]/2*math.cos(a*math.pi/3),P["humi_bolt_circle"]/2*math.sin(a*math.pi/3)) for a in range(6)]
frame=holes(frame,bolt_pts,P["humi_clearance_hole"]/2,5,11)
add("04_load_bearing_chassis",frame,"frame","chassis",(0,0,0))
bridge=cq.Workplane("XY")
for yy in [-32.5,32.5]:bridge=bridge.add(box(94,7,4,-9,yy,-9).val())
for xx in [-27,27]:bridge=bridge.add(box(6,65,4,xx,0,-9).val())
for yy in [-20,23]:bridge=bridge.add(box(58,7,4,0,yy,-9).val())
bridge=bridge.combine()
bridge=holes(holes(bridge,bridge_fix,1.35,-12,-6),support_pts,1.3,-12,-6)
add("27_removable_sensor_bridge",bridge,"frame","sensor",(0,0,-14),"REAR-SERVICE BRIDGE; FASTENER/THREAD DESIGN NOT RELEASED")
rails=cq.Workplane("XY")
for yy in [-18,18]:rails=rails.add(box(94,5,2,-9,yy,-32.4).val())
rails=holes(rails,rail_fix,1.35,-34,-30)
pcb_mounts=[(xx,yy) for xx in [-35,19] for yy in [-18,18]]
rails=holes(rails,pcb_mounts,1.35,-34,-30)
add("28_removable_control_mount_rails",rails,"frame","electronics",(0,26,-55),"TWO REMOVABLE RAILS; RETAINING HARDWARE TBD")
pcb_posts=cq.Workplane("XY")
for x,y in pcb_mounts:pcb_posts=pcb_posts.add(ring(2.7,1.35,-35.4,-33.4,x,y).val())
add("29_control_board_mount_spacers",pcb_posts,"mount","electronics",(0,26,-55),"2 mm CONCEPT SPACERS; HARDWARE/INSULATION UNVALIDATED")

base=ring(P["mount_base_od"]/2,28.5,HZ-2,HZ)
base=holes(base,bolt_pts,P["humi_clearance_hole"]/2,HZ-3,HZ+1)
add("05_humi_common_interface",base,"accent","humi",(0,0,94))

# Deliberately plain rings: E / EF naming indicates flange datum only.
# Production bayonet lugs, lens lock, release and contact assembly are omitted.
cartridge_r=P.get("cartridge_outer_diameter",72)/2
flange_r=P.get("reference_flange_outer_diameter",74)/2
e_tube=ring(cartridge_r,P["mount_clear_bore"]/2,HZ,EZ-2)
e_flange=ring(flange_r,P["mount_clear_bore"]/2,EZ-2,EZ)
e_tube=holes(e_tube,bolt_pts,1.6,HZ-1,EZ+1)
e_flange=holes(e_flange,bolt_pts,1.6,HZ-1,EZ+1)
add("06_e_mount_cartridge_body",e_tube,"black","E",(0,0,120),"FLANGE DATUM ONLY; NO BAYONET")
add("07_e_mount_reference_flange",e_flange,"mount","E",(0,0,120),"NOMINAL Z=18; NO FIT CLAIM")
ef_tube=ring(cartridge_r,P["mount_clear_bore"]/2,HZ,EFZ-2)
ef_tube=holes(ef_tube,bolt_pts,1.6,HZ-1,EFZ+1)
ef_flange=holes(ring(flange_r,P["mount_clear_bore"]/2,EFZ-2,EFZ),bolt_pts,1.6,HZ-1,EFZ+1)
add("08_ef_mount_cartridge_body",ef_tube,"black","EF",(97,0,100),"ALTERNATIVE TO E; NO BAYONET")
add("09_ef_mount_reference_flange",ef_flange,"mount","EF",(97,0,100),"NOMINAL Z=44; NO FIT CLAIM")

carrier=rounded(58,52,-5,-2,4).cut(rounded(38,26,-6,-1,1))
carrier=holes(carrier,support_pts,1.3,-6,-1)
add("10_independent_sensor_carrier",carrier,"carrier","sensor",(0,0,36))
substrate=box(40,28,1.85,0,0,-1.075)
active=box(P["sensor_width"],P["sensor_height"],.15,0,0,-.075)
add("11_sensor_package_placeholder",substrate,"black","sensor",(0,0,39),"PACKAGE UNSELECTED")
add("12_sensor_active_36x24",active,"sensor","sensor",(0,0,39),"REQUIRED ACTIVE AREA; FRONT Z=0")
posts=cq.Workplane("XY")
for x,y in support_pts:
    v=ring(3,1.3,-7,-5,x,y)
    posts=posts.add(v.val())
add("13_three_point_sensor_spacers",posts,"mount","sensor",(0,0,20),"ADJUSTMENT CONCEPT; NO SHIM TOLERANCE")

heat=rounded(56,48,-23,-21,3)
add("14_heat_spreader_placeholder",heat,"copper","electronics",(0,0,-30),"THERMAL RESERVATION ONLY: 19 mm GAP, NO HEAT PATH IMPLEMENTED")
mainboard=rounded(80,67,-29.5,-27.9,2,-8,0)
# Reserved pass-through around the bench PCB header. This is not a routed PCB.
mainboard=mainboard.cut(box(12,23,4,-31,-5.65 if P.get("right_hand_grip",True) else 5.65,-28.7))
add("15_main_processing_board_envelope",mainboard,"pcb","electronics",(0,0,-48),"ENVELOPE ONLY; NO CIRCUIT DESIGN")
cw,ch,cd=P["control_board_envelope"]
cx,cy,cz=P["control_board_center"]
control=box(cw,ch,cd,cx,cy,cz)
control_holes=[(cx-cw/2+u,cy+ch/2-v) for u,v in [(3,3),(57,3),(3,39),(57,39)]]
control=holes(control,control_holes,1.35,cz-2,cz+2)
pcb_source=ROOT/"source/FF1_Control_A0_board_only.step"
if pcb_source.exists():
    # Verified KiCad coordinates: X 0..60, Y -42..0, Z 0..1.51.
    # Preserve actual STEP planar outline and every hole, but extrude its bottom
    # profile to nominal full board thickness. Raw STEP's 1.51 mm dielectric-only
    # solid must not be mistaken for a confirmed finished board stackup.
    raw=cq.importers.importStep(str(pcb_source))
    control=cq.Workplane("XY").add(raw.faces("<Z").wires().vals()).toPending().extrude(cd)
    control=control.translate((cx-cw/2,cy+ch/2,cz-cd/2))
add("16_control_board_provisional_envelope",control,"pcb","electronics",(0,26,-48),P["control_board_status"])
component_parts=cq.Workplane("XY")
for u,v in [(25.25,9.25),(47.25,9.25)]:
    component_parts=component_parts.add(box(6,6,4.3,cx-cw/2+u,cy+ch/2-v,cz+cd/2+2.15).val())
component_parts=component_parts.add(box(5,16,8,-31,5.65,cz+cd/2+4).val())
add("26_control_board_component_envelopes",component_parts,"black","electronics",(0,26,-48),"BENCH BUTTONS + UPRIGHT HEADER; MATING/CABLE KEEP-OUT TBD")

bw,bh,bd=P["battery_envelope"]
bx,by,bz=P["battery_center"]
battery=rounded(bw,bh,bz-bd/2,bz+bd/2,3,bx,by)
add("17_replaceable_battery_envelope",battery,"battery","battery",(0,-80,0),"NO CELL/PACK SELECTED; NO ELECTRICAL SAFETY CLAIM")
batterydoor=box(31,2.4,26,62,-43,-21).edges("|Y").fillet(1.6)
add("18_battery_service_door",batterydoor,"grip","battery",(0,-113,0),"LATCH AND SEAL NOT ENGINEERED")
storage=box(4,32,22,-51,0,-16)
add("19_removable_storage_envelope",storage,"pcb","storage",(-65,0,0),"MEDIA TYPE AND CONNECTOR UNSELECTED")
carddoor=box(2,39,27,-65.6,0,-16).edges("|X").fillet(1.5)
add("20_storage_service_door",carddoor,"grip","storage",(-85,0,0),"HINGE AND SEAL NOT ENGINEERED")

display=rounded(72,49,-45.8,-42.8,2.8,-10,3)
add("21_display_module_envelope",display,"glass","rear",(0,0,-82),"ACTIVE AREA/PANEL PART UNSELECTED")
# Buttons face rear (-Z), with 1 mm visible stand-off.
keys=cq.Workplane("XY")
for x,y,r,stem in rear_specs:
    key=cyl(r,-47,-45.3,x,y).union(cyl(stem,-45.3,-41,x,y))
    keys=keys.add(key.val())
add("22_rear_buttons",keys,"black","rear",(0,0,-84),"CAPS AND SHAFTS WITH HOLES; SWITCH LINKAGE/TRAVEL UNVALIDATED")
# Top controls rotate cylindrical axis from optical Z into vertical Y.
shutter=ycyl(5,46.4,49.2,63,2).union(ycyl(1.7,40,46.4,63,2))
add("23_shutter_button",shutter,"mount","controls",(0,32,0))
dial=ycyl(8,50.4,54.4,28,-12).union(ycyl(2.5,45,50.4,28,-12))
add("24_top_control_dial",dial,"black","controls",(0,32,0))

# Nominal focal-plane symbol on the SIDE at actual Z=0. The v0.1 front cross
# was not a correct physical sensor-plane indicator. This remains cosmetic,
# not a metrology surface or a calibrated tolerance claim.
marker=ring(3,2.4,0,.4).rotate((0,0,0),(0,1,0),90).translate((-66.4,25,0))
marker=marker.union(box(.4,14,.6,-66.2,25,0))
add("25_sensor_datum_mark",marker,"accent","shell",(0,0,60))

manifest=[]
for part in parts:
    name=part["name"]
    cq.exporters.export(part["shape"],str(OUT/"parts"/(name+".step")))
    cq.exporters.export(part["shape"],str(OUT/"mesh"/(name+".stl")),tolerance=.08,angularTolerance=.15)
    solids=part["shape"].solids().vals()
    bb=exact_bbox(part["shape"].val())
    manifest.append({k:v for k,v in part.items() if k!="shape"}|{
        "solid_count":len(solids),"valid":all(s.isValid() for s in solids),
        "volume_mm3":sum(s.Volume() for s in solids),
        "bbox_mm":[bb.xmin,bb.ymin,bb.zmin,bb.xmax,bb.ymax,bb.zmax]})

for variant in ["E","EF"]:
    assembly=cq.Assembly(name=f"HassleFree_FF1_{variant}_concept")
    for part in parts:
        if part["group"] in ["E","EF"] and part["group"]!=variant: continue
        assembly.add(part["shape"],name=part["name"],color=cq.Color(*part["color"]))
    assembly.save(str(OUT/f"HassleFree_FF1_{variant}_assembly.step"))
    # A single STL is only a visualization reference; named meshes are preferred.
    compound=cq.Compound.makeCompound([p["shape"].val() for p in parts if p["group"] not in ["E","EF"] or p["group"]==variant])
    cq.exporters.export(compound,str(OUT/f"HassleFree_FF1_{variant}_visual_reference.stl"),tolerance=.08,angularTolerance=.15)

(OUT/"assembly_manifest.json").write_text(json.dumps(manifest,indent=2))
with (OUT/"parts_register.csv").open("w",newline="") as f:
    writer=csv.writer(f);writer.writerow(["part","group","status","solid_count","volume_mm3"])
    for p in manifest:writer.writerow([p["name"],p["group"],p["status"],p["solid_count"],round(p["volume_mm3"],2)])

# Selected packaging checks: exact B-rep intersection, not a general interference
# certification. Contact/fastener interfaces intentionally touch and are excluded.
byname={p["name"]:p["shape"] for p in parts}
checks=[]
pairs=[
    ("battery vs outer shell","17_replaceable_battery_envelope","01_front_shell"),
    ("battery vs chassis","17_replaceable_battery_envelope","04_load_bearing_chassis"),
    ("sensor carrier vs chassis","10_independent_sensor_carrier","04_load_bearing_chassis"),
    ("sensor active vs carrier","12_sensor_active_36x24","10_independent_sensor_carrier"),
    ("display vs rear shell","21_display_module_envelope","03_rear_shell"),
    ("main board vs chassis","15_main_processing_board_envelope","04_load_bearing_chassis"),
    ("control board vs chassis","16_control_board_provisional_envelope","04_load_bearing_chassis"),
    ("control board components vs main board","26_control_board_component_envelopes","15_main_processing_board_envelope"),
    ("control board components vs heat spreader","26_control_board_component_envelopes","14_heat_spreader_placeholder"),
    ("storage vs chassis","19_removable_storage_envelope","04_load_bearing_chassis"),
    ("front shell vs chassis","01_front_shell","04_load_bearing_chassis"),
    ("E cartridge vs front shell","06_e_mount_cartridge_body","01_front_shell"),
    ("EF cartridge vs front shell","08_ef_mount_cartridge_body","01_front_shell"),
]
for desc,a,b in pairs:
    common=byname[a].intersect(byname[b]); vol=sum(v.Volume() for v in common.solids().vals())
    checks.append({"check":desc,"intersection_volume_mm3":round(vol,8),"pass":vol<1e-6})
sensor_bb=exact_bbox(byname["12_sensor_active_36x24"].val())
e_distance=exact_bbox(byname["07_e_mount_reference_flange"].val()).zmax-sensor_bb.zmax
ef_distance=exact_bbox(byname["09_ef_mount_reference_flange"].val()).zmax-sensor_bb.zmax
checks.extend([
    {"check":"active sensor width x height","actual":[sensor_bb.xlen,sensor_bb.ylen],"pass":abs(sensor_bb.xlen-36)<1e-6 and abs(sensor_bb.ylen-24)<1e-6},
    {"check":"E flange above sensor datum","actual_mm":e_distance,"pass":abs(e_distance-18)<1e-6},
    {"check":"EF flange above sensor datum","actual_mm":ef_distance,"pass":abs(ef_distance-44)<1e-6},
    {"check":"E/EF adapter thickness difference","actual_mm":ef_distance-e_distance,"pass":abs(ef_distance-e_distance-26)<1e-6},
])
envelopes={}
for variant in ["E","EF"]:
    b=exact_bbox(cq.Compound.makeCompound([p["shape"].val() for p in parts if p["group"] not in ["E","EF"] or p["group"]==variant]))
    envelopes[variant]={"bounds_mm":[b.xmin,b.ymin,b.zmin,b.xmax,b.ymax,b.zmax],"size_mm":[b.xlen,b.ylen,b.zlen]}
report={"cadquery":cq.__version__,"brep_valid":all(p["valid"] for p in manifest),"part_groups":len(parts),"envelopes":envelopes,"checks":checks,
"limits":["No FEA, drop, thermal, EMI, sealing, optical raytrace, lens intrusion, bayonet fit or tolerance-stack validation.","No mounting fit is claimed by STEP/STL files.","Battery, screen, sensor package and electronics are envelopes until vendors and PCB designs are frozen.","PCB component and connector keepouts are provisional; carrier fasteners and chassis assembly joints are not detailed."]}
(ROOT/"validation/geometry_report.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
if not report["brep_valid"] or any(not c["pass"] for c in checks):
    raise SystemExit("Geometry check failed; review interference before delivery.")
