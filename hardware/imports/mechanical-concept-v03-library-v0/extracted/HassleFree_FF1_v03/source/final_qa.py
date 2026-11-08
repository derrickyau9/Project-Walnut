"""Read back final exports and consolidate existing v0.3 audits; no engineering release."""
from pathlib import Path
import json, struct
import cadquery as cq

root = Path(__file__).resolve().parents[1]
val = root / 'validation'
load = lambda name: json.loads((val / name).read_text())
geometry = load('geometry_report.json')
feasibility = load('feasibility_audit.json')
thermal = load('thermal_audit.json')
parameters = load('parameter_regression.json')
roundtrip = []
for variant in ('E', 'EF'):
    relative = f'exports/HassleFree_FF1_{variant}_assembly.step'
    shape = cq.importers.importStep(str(root / relative)).val()
    solids = shape.Solids()
    roundtrip.append({'file': relative, 'valid': shape.isValid() and all(s.isValid() for s in solids),
                      'solid_count': len(solids), 'volume_mm3': shape.Volume()})
(val / 'step_roundtrip.json').write_text(json.dumps(roundtrip, indent=2))
b = (root / 'exports/HassleFree_FF1_E_preview.glb').read_bytes()
assert b[:4] == b'glTF'
length, kind = struct.unpack_from('<II', b, 12)
assert kind == 0x4e4f534a
gltf = json.loads(b[20:20+length])
mesh_nodes = [n for n in gltf['nodes'] if 'mesh' in n]
scales = [n.get('scale', [1,1,1]) for n in mesh_nodes]
assert mesh_nodes and all(all(abs(v - .001) < 1e-9 for v in scale) for scale in scales)
assert geometry['brep_valid'] and all(c['pass'] for c in geometry['checks'])
assert feasibility['geometric_checks_passed'] and thermal['geometric_checks_passed']
assert parameters['all_passed'] and all(s['valid'] for s in roundtrip)
report = {
    'revision': '0.3', 'date': '2026-11-05',
    'status': 'MODELED GEOMETRY CHECKS PASS; ENGINEERING RELEASE BLOCKED',
    'engineering_release_ready': False,
    'part_groups': geometry['part_groups'],
    'basic_geometry_checks_passed': len(geometry['checks']),
    'all_pair_count_per_variant': {v: d['pair_count'] for v,d in feasibility['all_pairs'].items()},
    'service_paths_passed': [p['check'] for p in feasibility['service_paths'] if p['pass']],
    'thermal_positions_passed_z_mm': [p['landing_front_z_mm'] for p in thermal['positions'] if p['pass']],
    'nominal_axial_span_mm': 19,
    'candidate_usable_span_mm': [16.5, 19.5],
    'hard_slot_geometric_span_mm': [16, 20],
    'no_sensor_heat_connection': True,
    'no_thermal_performance_claim': True,
    'direct_contact_to_modeled_sensor_or_support': False,
    'parameter_regression_all_passed': parameters['all_passed'],
    'step_roundtrip': roundtrip,
    'glb_mesh_nodes': len(mesh_nodes), 'glb_mm_to_m_scale': scales[0],
    'sensor_chip_xy_lower_bound_mm': [41.071,30.815],
    'sensor_package_dimensions_known': False,
    'source': thermal['chip_xy_source'],
    'open_p0': [
        'Selected sensor/module package drawing, optical datum and approved heat interface',
        'Power dissipation, operating conditions and temperature limits',
        'Real populated electronics, mating connectors and wiring',
        'Materials, manufacturability, retention hardware, preload and tolerances',
        'Actual lens interface and rear-group/optical clearance'],
    'limits': feasibility['limits'] + thermal['conditions'],
}
(val / 'final_QA.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
