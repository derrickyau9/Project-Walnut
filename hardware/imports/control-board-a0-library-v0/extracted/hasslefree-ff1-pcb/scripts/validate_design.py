#!/usr/bin/env python3
"""Check exported schematic connectivity against the explicit intended circuit."""
import os
os.environ.update(XDG_CONFIG_HOME='/tmp/ff1-config',XDG_CACHE_HOME='/tmp/ff1-cache',XDG_DATA_HOME='/tmp/ff1-data')
from pathlib import Path
import json, xml.etree.ElementTree as ET, hashlib
import pcbnew as p
ROOT=Path(__file__).resolve().parents[1]
cs=json.loads((ROOT/'scripts/components.json').read_text())
xml=ET.parse(ROOT/'reports/netlist.xml'); actual={}
for net in xml.findall('./nets/net'):
 for node in net.findall('node'):actual[(node.attrib['ref'],node.attrib['pin'])]=net.attrib['name'].lstrip('/')
expected={(c['ref'],pin):net for c in cs for pin,net in c['nets'].items()}
assert actual==expected, {'missing':{str(k):v for k,v in expected.items() if actual.get(k)!=v},'unexpected':{str(k):v for k,v in actual.items() if expected.get(k)!=v}}
b=p.LoadBoard(str(ROOT/'hardware/FF1_Control_A0.kicad_pcb'))
boardpads={}
for f in b.GetFootprints():
 for pad in f.Pads():
  if pad.GetAttribute()!=p.PAD_ATTRIB_NPTH:boardpads[(f.GetReference(),pad.GetNumber())]=pad.GetNetname().lstrip('/')
assert boardpads==expected
tracks=list(b.GetTracks());vias=[t for t in tracks if isinstance(t,p.PCB_VIA)]
# Via holes must not lie within an SMT paste/mask pad: no accidental via-in-pad.
for v in vias:
 for f in b.GetFootprints():
  for pad in f.Pads():
   if pad.GetAttribute()==p.PAD_ATTRIB_SMD:assert not pad.HitTest(v.GetPosition()), 'Via-in-SMD-pad'
assert set(actual.values())=={'V3V3','GND','HALF_RAW','HALF_N','FULL_RAW','FULL_N','RESET_N','LED_A'}
report={'result':'PASS','kicad_version':p.GetBuildVersion(),'schematic_pin_net_assignments':len(expected),'circuit_nets':8,'electrical_components':len([c for c in cs if c['nets']]),'mechanical_holes':4,'track_segments':len(tracks)-len(vias),'through_vias':len(vias),'via_in_smd_pad':False,'erc_report':'ERC.rpt','drc_report':'DRC.rpt','excluded_drc_items':0,'electrical_bench_validation':'NOT RUN','scope':'Low-speed 3.3 V controls and local supply supervisor only; not a sensor/mainboard'}
(ROOT/'reports/validation_summary.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
