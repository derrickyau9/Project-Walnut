import ast,csv,hashlib,importlib.util,json,re,sys,xml.etree.ElementTree as ET
from pathlib import Path
root=Path(__file__).resolve().parents[2]
imports=root/'hardware/imports'
mech=imports/'mechanical-concept-v01-library-v0/extracted/HassleFree_FF1_3D_Concept_v01'
pcb=imports/'control-board-a0-library-v0/extracted/hasslefree-ff1-pcb'
sensor=imports/'sensor-integration-a0-library-v0/extracted/ff1-sensor-integration'
for p in imports.rglob('*.py'):ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))

def parse_sexpr(path):
 tokens=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',path.read_text(encoding='utf-8-sig')); pos=0
 def atom(t):return json.loads(t) if t.startswith('"') else t
 def node():
  nonlocal pos
  assert tokens[pos]=='(';pos+=1;out=[]
  while tokens[pos]!=')':
   if tokens[pos]=='(':out.append(node())
   else:out.append(atom(tokens[pos]));pos+=1
  pos+=1;return out
 out=node();assert pos==len(tokens);return out

def children(n,key):return [i for i in n if isinstance(i,list) and i and i[0]==key]
def first(n,key):return next(iter(children(n,key)),None)
board=parse_sexpr(pcb/'hardware/FF1_Control_A0.kicad_pcb')
sch=parse_sexpr(pcb/'hardware/FF1_Control_A0.kicad_sch')
footprints=children(board,'footprint');pads={};holes=[];references=[];positions={}
for fp in footprints:
 ref=next(n[2] for n in children(fp,'property') if n[1]=='Reference');references.append(ref);positions[ref]=first(fp,'at')[1:]
 if ref.startswith('H'):holes.append({'ref':ref,'at':first(fp,'at')[1:],'drills':[first(p,'drill')[1:] for p in children(fp,'pad')]})
 for pad in children(fp,'pad'):
  net=first(pad,'net')
  if net:pads[(ref,pad[1])]=net[2]
netlist=ET.parse(pcb/'reports/netlist.xml').getroot();xmlpins={}
for net in netlist.findall('./nets/net'):
 for node in net.findall('node'):xmlpins[(node.attrib['ref'],node.attrib['pin'])]=net.attrib['name']
assert pads==xmlpins, {'pcb_only':list(pads.items()-xmlpins.items()),'netlist_only':list(xmlpins.items()-pads.items())}
assert {pin:net.lstrip('/') for (ref,pin),net in pads.items() if ref=='J1'}=={'1':'V3V3','2':'GND','3':'HALF_N','4':'FULL_N','5':'RESET_N','6':'GND'}
expected_refs={x.attrib['ref'] for x in netlist.findall('./components/comp') if not x.attrib['ref'].startswith('H')}
actual_refs=set(references)-{x['ref'] for x in holes}
assert expected_refs==actual_refs
assert len(pads)==35 and len(footprints)==19 and len(expected_refs)==15
assert len(children(board,'segment'))==272 and len(children(board,'via'))==5
labels=sorted(n[1] for n in children(sch,'label'))
symbol_refs=[]
for sym in children(sch,'symbol'):
 ref=next((p[2] for p in children(sym,'property') if p[1]=='Reference'),None)
 if ref:symbol_refs.append(ref)
assert expected_refs.issubset(set(symbol_refs))
for p in pcb.rglob('*.kicad_mod'):parse_sexpr(p)
parse_sexpr(pcb/'hardware/FF1.kicad_sym');json.loads((pcb/'hardware/FF1_Control_A0.kicad_pro').read_text())

p=json.loads((mech/'source/parameters.json').read_text());assert p['sensor_plane_z']==0 and p['humi_plane_z']==12 and p['e_flange_z']==18 and p['ef_flange_z']==44
# Validate exported binary STL bounds independently; no author generators run.
import struct
bounds={}
for name in ['07_e_mount_reference_flange','09_ef_mount_reference_flange','12_sensor_active_36x24','16_control_board_provisional_envelope']:
 b=(mech/'exports/mesh'/f'{name}.stl').read_bytes();n=struct.unpack_from('<I',b,80)[0];assert len(b)==84+50*n
 coords=[struct.unpack_from('<9f',b,84+i*50+12) for i in range(n)]
 points=[c[j:j+3] for c in coords for j in (0,3,6)]
 bounds[name]=[min(v[k] for v in points) for k in range(3)]+[max(v[k] for v in points) for k in range(3)]
assert abs(bounds['07_e_mount_reference_flange'][5]-18)<1e-5
assert abs(bounds['09_ef_mount_reference_flange'][5]-44)<1e-5
assert bounds['12_sensor_active_36x24']==[-18,-12,-0.15000000596046448,18,12,0]
assert (mech/'source/FF1_Control_A0_board_only.step').read_bytes()==(pcb/'preview/FF1_Control_A0_board_only.step').read_bytes()
# Inspect the active v0.2 exports and the scope of supplied CAD reports.
# These checks do not import CadQuery or execute any supplied generator.
mech_v02=imports/'mechanical-concept-v02-library-v0/extracted/HassleFree_FF1_v02_Engineering_Review'
v02_parameters=json.loads((mech_v02/'source/parameters.json').read_text())
v02_manifest=json.loads((mech_v02/'exports/assembly_manifest.json').read_text())
v02_audit=json.loads((mech_v02/'validation/feasibility_audit.json').read_text())
v02_qa=json.loads((mech_v02/'validation/final_QA.json').read_text())
assert len(v02_manifest)==29 and len({part['name'] for part in v02_manifest})==29
v02_bounds={};maximum_bounds_difference=0
for part in v02_manifest:
 name=part['name'];data=(mech_v02/'exports/mesh'/f'{name}.stl').read_bytes()
 count=struct.unpack_from('<I',data,80)[0];assert len(data)==84+50*count
 vertices=[struct.unpack_from('<9f',data,84+i*50+12) for i in range(count)]
 points=[v[j:j+3] for v in vertices for j in (0,3,6)]
 bb=[min(v[k] for v in points) for k in range(3)]+[max(v[k] for v in points) for k in range(3)]
 v02_bounds[name]=bb
 maximum_bounds_difference=max(maximum_bounds_difference,max(abs(a-b) for a,b in zip(bb,part['bbox_mm'],strict=True)))
 # Mesh tessellation and float32 rounding are not manufactured tolerances.
 assert all(abs(a-b)<0.081 for a,b in zip(bb,part['bbox_mm'],strict=True)),name
 step=(mech_v02/'exports/parts'/f'{name}.step').read_bytes()
 assert step.startswith(b'ISO-10303-21;') and b'END-ISO-10303-21;' in step
for variant in ['E','EF']:
 parts=[part for part in v02_manifest if part['group'] not in ['E','EF'] or part['group']==variant]
 expected_pair_count=len(parts)*(len(parts)-1)//2
 assert len(parts)==27 and expected_pair_count==351
 assert v02_audit['all_pairs'][variant]['pair_count']==expected_pair_count
 assert v02_audit['all_pairs'][variant]['interferences']==[]
 assert v02_audit['all_pairs'][variant]['pass'] is True
 assert v02_qa['all_pair_count_per_variant'][variant]==expected_pair_count
assert len(v02_audit['service_paths'])==4
assert all(path['pass'] is True and not path['interferences'] for path in v02_audit['service_paths'])
assert v02_audit['engineering_release_ready'] is False
assert v02_audit['sensor_to_thermal_reservation_gap_mm']==19
assert v02_parameters['humi_plane_z']==12 and v02_parameters['e_flange_z']==18 and v02_parameters['ef_flange_z']==44
assert v02_parameters['cartridge_outer_diameter']==72 and v02_parameters['reference_flange_outer_diameter']==74
assert abs(v02_bounds['07_e_mount_reference_flange'][5]-18)<1e-5
assert abs(v02_bounds['09_ef_mount_reference_flange'][5]-44)<1e-5
package_bounds=v02_bounds['11_sensor_package_placeholder']
assert abs(package_bounds[3]-package_bounds[0]-40)<1e-5
assert abs(package_bounds[4]-package_bounds[1]-28)<1e-5
thermal_gap=package_bounds[2]-v02_bounds['14_heat_spreader_placeholder'][5]
assert abs(thermal_gap-19)<1e-5
assert (mech_v02/'source/FF1_Control_A0_board_only.step').read_bytes()==(pcb/'preview/FF1_Control_A0_board_only.step').read_bytes()
v02_review={'status':'static_source_mesh_and_report_consistency_only','mesh_groups_inspected':29,
 'maximum_STL_vs_supplied_manifest_bounds_difference_mm':maximum_bounds_difference,
 'selected_stl_bounds_mm':{name:v02_bounds[name] for name in ['07_e_mount_reference_flange','09_ef_mount_reference_flange','11_sensor_package_placeholder','12_sensor_active_36x24','14_heat_spreader_placeholder','16_control_board_provisional_envelope']},
 'independently_inspected_sensor_placeholder_mm':[40,28],
 'independently_inspected_Z_gap_to_thermal_reservation_mm':thermal_gap,
 'shared_board_STEP_identical':True,
 'supplied_report_only':{'part_groups':29,'part_pairs_per_variant':351,'service_paths':v02_qa['service_paths_passed'],'engineering_release_ready':False},
 'cad_intersections_or_sweeps_rerun':False,
 'limits':['No actual sensor package or optical stack','No validated E/EF bayonet/lens fit','HUMI Z12 remains an assumption','Thermal path unbridged','No populated electronics/cable/retaining-hardware fit','No tolerance-stack or manufacturing release']}

# Active v0.3: inspect exports, keepout arithmetic and supplied result consistency.
# No CAD generator, collision solver or thermal/contact simulation is run here.
mech_v03=imports/'mechanical-concept-v03-library-v0/extracted/HassleFree_FF1_v03'
v03_parameters=json.loads((mech_v03/'source/parameters.json').read_text())
v03_manifest=json.loads((mech_v03/'exports/assembly_manifest.json').read_text())
v03_audit=json.loads((mech_v03/'validation/feasibility_audit.json').read_text())
v03_thermal=json.loads((mech_v03/'validation/thermal_audit.json').read_text())
v03_qa=json.loads((mech_v03/'validation/final_QA.json').read_text())
v03_keepout=json.loads((mech_v03/'keepouts/README.json').read_text())
v03_regression=json.loads((mech_v03/'validation/parameter_regression.json').read_text())
assert len(v03_manifest)==32 and len({part['name'] for part in v03_manifest})==32

def mesh_bounds(path):
 data=path.read_bytes();count=struct.unpack_from('<I',data,80)[0]
 assert len(data)==84+50*count
 vertices=[struct.unpack_from('<9f',data,84+i*50+12) for i in range(count)]
 points=[v[j:j+3] for v in vertices for j in (0,3,6)]
 return [min(v[k] for v in points) for k in range(3)]+[max(v[k] for v in points) for k in range(3)]

v03_bounds={};v03_maximum_bounds_difference=0
for part in v03_manifest:
 name=part['name'];bb=mesh_bounds(mech_v03/'exports/mesh'/f'{name}.stl');v03_bounds[name]=bb
 difference=max(abs(a-b) for a,b in zip(bb,part['bbox_mm'],strict=True))
 v03_maximum_bounds_difference=max(v03_maximum_bounds_difference,difference)
 assert difference<0.081,name
 step=(mech_v03/'exports/parts'/f'{name}.step').read_bytes()
 assert step.startswith(b'ISO-10303-21;') and b'END-ISO-10303-21;' in step
for variant in ['E','EF']:
 parts=[part for part in v03_manifest if part['group'] not in ['E','EF'] or part['group']==variant]
 assert len(parts)==30 and len(parts)*(len(parts)-1)//2==435
 assert v03_audit['all_pairs'][variant]['pair_count']==435
 assert v03_audit['all_pairs'][variant]['interferences']==[]
 assert v03_audit['all_pairs'][variant]['pass'] is True
 assert v03_qa['all_pair_count_per_variant'][variant]==435
assert len(v03_audit['service_paths'])==5 and all(path['pass'] is True and not path['interferences'] for path in v03_audit['service_paths'])
assert [position['landing_front_z_mm'] for position in v03_thermal['positions']]==[-5,-4,-3,-2,-1]
assert all(position['pass'] is True and not position['interferences'] and position['guard_intersection_mm3']==0 for position in v03_thermal['positions'])
assert len(v03_thermal['nominal_slot_stop_tests'])==2
assert all(test['pass_if_shaft_is_retained'] is True for test in v03_thermal['nominal_slot_stop_tests'])
assert v03_regression['all_passed'] is True and all(case['pass'] is True for case in v03_regression['cases'])
for report in [v03_audit,v03_thermal,v03_qa]:assert report['engineering_release_ready'] is False
assert v03_thermal['no_sensor_heat_connection'] is True and v03_thermal['no_thermal_performance_claim'] is True
assert v03_thermal['package_dimensions_and_height_known'] is False
assert v03_keepout['verified_chip_xy_mm']==[41.071,30.815]
assert v03_keepout['z_extent_mm']==[-21,0] and v03_keepout['guard_xy_margin_mm']==2
keepout_bounds={name:mesh_bounds(mech_v03/'keepouts'/f'IMX410_{name}.stl') for name in ['chip_corridor','guard']}
for name,xy in [('chip_corridor',[41.071,30.815]),('guard',[45.071,34.815])]:
 bb=keepout_bounds[name]
 assert abs((bb[3]-bb[0])-xy[0])<1e-5 and abs((bb[4]-bb[1])-xy[1])<1e-5
 assert bb[2]==-21 and bb[5]==0
# AABB separation proves only these nominal exported envelopes avoid this guard;
# a bounding-box overlap elsewhere is inconclusive and no whole-assembly claim follows.
landing_guard_separations={}
guard=keepout_bounds['guard']
for name in ['32_thermal_adjustable_landing','33_thermal_clamp_screw_reservation','34_thermal_clamp_nut_reservation']:
 bb=v03_bounds[name]
 separation=max(max(bb[k]-guard[k+3],guard[k]-bb[k+3],0) for k in range(3))
 assert separation>0
 landing_guard_separations[name]=separation
assert abs(v03_bounds['07_e_mount_reference_flange'][5]-18)<1e-5 and abs(v03_bounds['09_ef_mount_reference_flange'][5]-44)<1e-5
package=v03_bounds['11_sensor_package_placeholder']
assert abs(package[3]-package[0]-40)<1e-5 and abs(package[4]-package[1]-28)<1e-5
thermal_parameters=v03_parameters['thermal_bridge']
assert thermal_parameters['front_z']==-2 and thermal_parameters['collector_front_z']==-21
assert thermal_parameters['front_z']-thermal_parameters['collector_front_z']==19
assert thermal_parameters['usable_front_min_z']==-4.5 and thermal_parameters['usable_front_max_z']==-1.5
assert (mech_v03/'source/FF1_Control_A0_board_only.step').read_bytes()==(pcb/'preview/FF1_Control_A0_board_only.step').read_bytes()
v03_review={'status':'static_source_mesh_keepout_and_report_consistency_only','mesh_groups_inspected':32,
 'maximum_STL_vs_supplied_manifest_bounds_difference_mm':v03_maximum_bounds_difference,
 'selected_stl_bounds_mm':{name:v03_bounds[name] for name in ['07_e_mount_reference_flange','09_ef_mount_reference_flange','11_sensor_package_placeholder','14_heat_spreader_placeholder','32_thermal_adjustable_landing','33_thermal_clamp_screw_reservation','34_thermal_clamp_nut_reservation']},
 'keepout_stl_bounds_mm':keepout_bounds,
 'nominal_exported_landing_and_clamp_AABB_separation_from_guard_mm':landing_guard_separations,
 'published_chip_XY_only_mm':[41.071,30.815],'guard_XY_mm':[45.071,34.815],
 'guard_Z_is_provisional_routing_bay_not_package_height':True,
 'sensor_placeholder_mm':[40,28],'nominal_axial_routing_span_mm':19,
 'shared_board_STEP_identical':True,
 'supplied_report_only':{'part_groups':32,'part_pairs_per_variant':435,'service_paths':v03_qa['service_paths_passed'],'thermal_adjustment_positions_z_mm':[-5,-4,-3,-2,-1],'parameter_regression_cases':len(v03_regression['cases']),'engineering_release_ready':False},
 'cad_intersections_service_sweeps_parameter_cases_and_thermal_positions_rerun':False,
 'sensor_heat_connection_complete':False,'thermal_performance_validated':False,
 'limits':['Actual sensor package and approved heat interface unknown','Chip-sized routing exclusion is not a package model','Thermal adjustment sampled at five supplied positions, not continuous swept range verification','No thermal contact resistance, preload, thermoelastic or temperature validation','No real fastening, lens fit, tolerance-stack or manufacturing release']}

# Independently recompute all persisted budget numbers rather than rewriting the supplied JSON.
budget=json.loads((sensor/'analysis/throughput_budget.json').read_text());recomputed=[]
for m in budget['modes']:
 pixels=m['active_width']*m['active_height'];packed=pixels*m['bits']/8;container=2*pixels;fps=m['fps']
 expected={'packed_frame_bytes':packed,'container16_frame_bytes':container,'active_payload_bits_per_second':pixels*m['bits']*fps,'packed_active_MB_per_second':packed*fps/1e6,'container16_MB_per_second':container*fps/1e6,'four_frame_buffer16_bytes':container*4,'one_write_one_read_container16_GB_per_second':container*fps*2/1e9}
 for key,value in expected.items():assert abs(m[key]-value)<=max(1e-7,abs(value)*1e-12),(key,m[key],value)
 recomputed.append({'mode':m['mode'],**expected})
with (sensor/'design/zcu102_hpc0_rx.csv').open(newline='') as f:rows=list(csv.DictReader(f))
xdc=(sensor/'design/zcu102_hpc0_rx_pins.xdc').read_text()
assert [int(r['channel']) for r in rows]==list(range(8))
assert len({r[k] for r in rows for k in ['p_pin','n_pin']})==16
assert len({r[k] for r in rows for k in ['fpga_p_pin','fpga_n_pin']})==16
for r in rows:
 for pol in ['p','n']:assert f"PACKAGE_PIN {r['fpga_'+pol+'_pin']} [get_ports {{hpc0_rx_{pol}[{r['channel']}]}}]" in xdc
req=json.loads((sensor/'design/interface_requirements.json').read_text());assert req['receiver_candidate']['fabrication_release'] is False and req['receiver_candidate']['carrier_DP_to_sensor_lane_assignment'] is None
primary=json.loads((imports/'primary-source-review.json').read_text(encoding='utf-8-sig'))
for r,pair in zip(rows,primary['amd']['host_rx_pairs'],strict=True):
 assert int(r['channel'])==pair['channel']
 for source_key,review_key in [('p_pin','j5_p'),('n_pin','j5_n'),('fpga_p_pin','fpga_p'),('fpga_n_pin','fpga_n')]:assert r[source_key]==pair[review_key]
assert primary['sony']['checked']['page2_chip_size_mm_not_package_extents']==[41.071,30.815]
assert 40<41.071 and 28<30.815

# Validate vendor-provided file inventories, without trusting them as proof of function.
checksums=[]
for base in [pcb,sensor]:
 sums=json.loads((base/'SHA256SUMS.json').read_text());assert isinstance(sums,dict)
 for name,want in sums.items():
  target=(base/name).resolve();assert target.is_relative_to(base.resolve())
  assert hashlib.sha256(target.read_bytes()).hexdigest()==want,(name,'checksum mismatch')
 checksums.append({'package':base.name,'files_verified':len(sums)})
result={'status':'PASS for static consistency only','reviewed_at_utc':'2026-11-05','python_sources_ast_parsed':len(list(imports.rglob('*.py'))),'pcb':{'footprints':len(footprints),'electrical_components':len(expected_refs),'pin_net_assignments':len(pads),'segments':len(children(board,'segment')),'vias':len(children(board,'via')),'j1_pin_nets':{pin:net for (ref,pin),net in pads.items() if ref=='J1'},'mounting_holes':holes,'source_schematic_refs':symbol_refs,'schematic_labels':labels,'source_pcb_netlist_parity':True,'schematic_connectivity_regeneration':False},'mechanical':{'active_revision':'0.3','historical_v01_stl_bounds_mm':bounds,'historical_v02':v02_review,'v03':v03_review},'sensor':{'persisted_budget_recomputed':recomputed,'csv_xdc_consistent':True,'csv_matches_independent_primary_review':True,'primary_source_pinout_validation':'Official AMD UG1182 v1.7 tables 3-44/45 independently reviewed in this session; see primary-source-review.json; sensor lane order/PHY still unverified'},'supplied_checksum_manifests':checksums,'tools_available':{'cadquery':importlib.util.find_spec('cadquery') is not None,'pcbnew':importlib.util.find_spec('pcbnew') is not None},'not_performed':['KiCad ERC/DRC rerun','CadQuery B-rep regeneration','FPGA synthesis/implementation/timing','electrical bench tests','optical alignment','mechanical fit','supplier approval','fabrication']}
# Confirm every preserved archive and extracted member against recorded hashes.
manifest=json.loads((imports/'provenance.json').read_text(encoding='utf-8'))
for source in manifest['sources']:
 archive=(imports/source['archive_relative_path']).resolve()
 assert archive.is_relative_to(imports.resolve())
 assert archive.stat().st_size==source['size_bytes']
 assert hashlib.sha256(archive.read_bytes()).hexdigest()==source['archive_sha256']
 for item in source['files']:
  member=(imports/item['path']).resolve()
  assert member.is_relative_to(imports.resolve())
  assert member.stat().st_size==item['size_bytes']
  assert hashlib.sha256(member.read_bytes()).hexdigest()==item['sha256']
print('Provenance verification passed: '+str(len(manifest['sources']))+' archives / '+str(sum(len(x['files']) for x in manifest['sources']))+' extracted files')
(imports/'integration-validation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
