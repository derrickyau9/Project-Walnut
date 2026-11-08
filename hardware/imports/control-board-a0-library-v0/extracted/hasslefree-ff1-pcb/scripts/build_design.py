#!/usr/bin/env python3
"""Reproduce an FF-1 low-speed control test board in KiCad 9. Not a sensor board."""
import os
os.environ.update(XDG_CONFIG_HOME='/tmp/ff1-config',XDG_CACHE_HOME='/tmp/ff1-cache',XDG_DATA_HOME='/tmp/ff1-data')
import math, json, uuid, re, shutil
from pathlib import Path
import pcbnew as p
ROOT=Path(__file__).resolve().parents[1]; H=ROOT/'hardware'; H.mkdir(exist_ok=True)
NAME='FF1_Control_A0'; SHEET=str(uuid.uuid5(uuid.NAMESPACE_DNS,NAME))
def uid(s):return str(uuid.uuid5(uuid.NAMESPACE_DNS, NAME+'/'+s))
def extract(lib,name):
 s=Path('/usr/share/kicad/symbols/'+lib+'.kicad_sym').read_text(); i=s.index('(symbol "'+name+'"'); level=0; quote=False; esc=False
 for j in range(i,len(s)):
  c=s[j]
  if quote:
   if esc:esc=False
   elif c=='\\':esc=True
   elif c=='"':quote=False
  elif c=='"':quote=True
  elif c=='(':level+=1
  elif c==')':
   level-=1
   if level==0:return s[i:j+1]
libs={n:extract(l,n) for l,n in [('Device','R'),('Device','C'),('Device','LED'),('Switch','SW_Push'),('Connector_Generic','Conn_01x06'),('power','PWR_FLAG'),('Mechanical','MountingHole')]}
libs['TLV803EA29DBZR']='''(symbol "TLV803EA29DBZR" (pin_names (offset 0.508)) (in_bom yes) (on_board yes)
(property "Reference" "U" (at 0 10.16 0) (effects (font (size 1.27 1.27))))
(property "Value" "TLV803EA29DBZR" (at 0 -10.16 0) (effects (font (size 1.27 1.27))))
(property "Footprint" "FF1:SOT-23" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
(property "Datasheet" "https://www.ti.com/lit/ds/symlink/tlv803e.pdf" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
(symbol "TLV803EA29DBZR_0_1" (rectangle (start -7.62 5.08) (end 7.62 -5.08) (stroke (width 0.254) (type default)) (fill (type background))))
(symbol "TLV803EA29DBZR_1_1"
(pin power_in line (at 0 7.62 270) (length 2.54) (name "VDD" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))
(pin power_in line (at 0 -7.62 90) (length 2.54) (name "GND" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
(pin open_collector line (at 10.16 0 180) (length 2.54) (name "~{RESET}" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
))'''
(H/'FF1.kicad_sym').write_text('(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")\n'+'\n'.join(libs.values())+'\n)')
(H/'sym-lib-table').write_text('(sym_lib_table (version 7) (lib (name "FF1") (type "KiCad") (uri "${KIPRJMOD}/FF1.kicad_sym") (options "") (descr "Local symbols; supervisor pin map checked against TI Rev J")))\n')
F=H/'FF1.pretty';F.mkdir(exist_ok=True)
fp_src={
'R_0603_1608Metric':'Resistor_SMD','C_0603_1608Metric':'Capacitor_SMD','LED_0805_2012Metric':'LED_SMD','SOT-23':'Package_TO_SOT_SMD','PinHeader_1x06_P2.54mm_Vertical':'Connector_PinHeader_2.54mm','SW_TH_Tactile_Omron_B3F-10xx':'Button_Switch_THT','MountingHole_2.7mm_M2.5':'MountingHole'}
for n,l in fp_src.items():shutil.copyfile('/usr/share/kicad/footprints/'+l+'.pretty/'+n+'.kicad_mod',F/(n+'.kicad_mod'))
(H/'fp-lib-table').write_text('(fp_lib_table (version 7) (lib (name "FF1") (type "KiCad") (uri "${KIPRJMOD}/FF1.pretty") (options "") (descr "Bundled KiCad 9 footprint snapshots")))\n')
components=[]; bits=[]
def wire(a,b):
 if a==b:return
 bits.append(f'(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) (stroke (width 0) (type default)) (uuid "{uid("wire"+str(a)+str(b))}"))')
def label(net,x,y):bits.append(f'(label "{net}" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{uid("label"+net+str(x)+str(y))}"))')
def junc(x,y):bits.append(f'(junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid "{uid("junction"+str(x)+str(y))}"))')
def note(s,x,y,size=1.27):bits.append(f'(text {json.dumps(s)} (at {x} {y} 0) (effects (font (size {size} {size})) (justify left top)) (uuid "{uid("note"+s)}"))')
def symbol(ref,kind,value,x,y,angle=0,fp='',nets=None,board=None,source=''):
 guid=uid(ref); pw=ref.startswith('#')
 # Local standard symbols use KiCad's own graphical conventions.
 if kind in ['R','C'] and angle==0:rx,ry,vx,vy=x+5,y-1.5,x+5,y+1.5
 elif kind=='SW_Push':rx,ry,vx,vy=x+9,y-3,x+13,y+1
 elif kind=='Conn_01x06':rx,ry,vx,vy=x+6,y-6,x+19,y-3
 elif kind=='LED':rx,ry,vx,vy=x+8,y-1.5,x+13,y+1.5
 elif kind=='TLV803EA29DBZR':rx,ry,vx,vy=x,y-11,x,y+11
 else:rx,ry,vx,vy=x,y-6,x,y-3
 def prop(n,v,px,py,hide=False):return f'(property "{n}" {json.dumps(v)} (at {px} {py} {angle%180}) (effects (font (size 1.27 1.27))'+(' (hide yes)' if hide else '')+'))'
 s=f'(symbol (lib_id "FF1:{kind}") (at {x} {y} {angle}) (unit 1) (in_bom {"no" if pw or kind=="MountingHole" else "yes"}) (on_board {"no" if pw else "yes"}) (dnp no) (uuid "{guid}")'
 s+=prop('Reference',ref,rx,ry,pw)+prop('Value',value,vx,vy,pw)+prop('Footprint','FF1:'+fp if fp else '',x,y,True)+prop('Datasheet',source,x,y,True)
 s+=f'(instances (project "{NAME}" (path "/{SHEET}" (reference "{ref}") (unit 1)))))'
 bits.append(s)
 if not pw:components.append(dict(ref=ref,kind=kind,value=value,uuid=guid,fp=fp,nets=nets or {},board=board,source=source))
# Top row: independent half/full buttons with series-protected RC input filtering.
for idx,x in [(1,60),(2,165)]:
 raw='HALF_RAW' if idx==1 else 'FULL_RAW'; out='HALF_N' if idx==1 else 'FULL_N'
 r=idx; rs=idx+2; c=idx+1; sx=72 if idx==1 else 94
 symbol('R'+str(r),'R','10k 1%',x,55,fp='R_0603_1608Metric',nets={'1':'V3V3','2':raw},board=(sx-2,67,0))
 symbol('SW'+str(idx),'SW_Push','B3F-1002-G',x,73,270,'SW_TH_Tactile_Omron_B3F-10xx',{'1':raw,'2':'GND'},(sx,57,0),'https://components.omron.com/system/files/2023-01/datasheet_pdf/A070-E1.pdf')
 symbol('R'+str(rs),'R','1k 1%',x+25,62,90,'R_0603_1608Metric',{'1':raw,'2':out},(sx+2,69,0))
 symbol('C'+str(c),'C','100n 16V X7R',x+45,73,fp='C_0603_1608Metric',nets={'1':out,'2':'GND'},board=(sx+6,71,0))
 wire((x,45),(x,51.19));label('V3V3',x,45)
 wire((x,58.81),(x,62)); wire((x,62),(x,67.92));junc(x,62);label(raw,x,62)
 wire((x,62),(x+21.19,62));wire((x+28.81,62),(x+45,62));wire((x+45,62),(x+55,62));junc(x+45,62);label(out,x+55,62)
 wire((x+45,62),(x+45,69.19));wire((x+45,76.81),(x+45,83));label('GND',x+45,83)
 wire((x,78.08),(x,83));label('GND',x,83)
 note(('01 / HALF PRESS' if idx==1 else '02 / FULL PRESS'),x-15,33,2)
# Host header and local bulk capacitor.
symbol('J1','Conn_01x06','HOST 3V3 ONLY',65,120,fp='PinHeader_1x06_P2.54mm_Vertical',nets={'1':'V3V3','2':'GND','3':'HALF_N','4':'FULL_N','5':'RESET_N','6':'GND'},board=(57,59,0))
for i,n in enumerate(['V3V3','GND','HALF_N','FULL_N','RESET_N','GND']):
 y=114.92+i*2.54;wire((40,y),(59.92,y));label(n,40,y)
symbol('C4','C','1u 16V X7R',95,120,fp='C_0603_1608Metric',nets={'1':'V3V3','2':'GND'},board=(62,55,0))
wire((95,110),(95,116.19));label('V3V3',95,110);wire((95,123.81),(95,132));label('GND',95,132)
symbol('#FLG01','PWR_FLAG','PWR_FLAG',105,110);wire((95,110),(105,110));junc(95,110)
symbol('#FLG02','PWR_FLAG','PWR_FLAG',105,132);wire((95,132),(105,132));junc(95,132)
note('03 / HOST HEADER\nExternal regulated 3.3 V only. No 5 V.',30,96,1.5)
# Supervisor plus decoupling, output pullup. Not connected to any sensor supply.
symbol('U1','TLV803EA29DBZR','TLV803EA29DBZR',160,120,fp='SOT-23',nets={'1':'GND','2':'RESET_N','3':'V3V3'},board=(75,81,0),source='https://www.ti.com/lit/ds/symlink/tlv803e.pdf')
symbol('C1','C','100n 16V X7R',135,120,fp='C_0603_1608Metric',nets={'1':'V3V3','2':'GND'},board=(79,80,90))
symbol('R5','R','10k 1%',190,110,fp='R_0603_1608Metric',nets={'1':'V3V3','2':'RESET_N'},board=(71,78,0))
wire((160,104),(160,112.38));label('V3V3',160,104)
wire((160,127.62),(160,136));label('GND',160,136)
wire((135,110),(135,116.19));label('V3V3',135,110);wire((135,123.81),(135,132));label('GND',135,132)
wire((190,100),(190,106.19));label('V3V3',190,100)
wire((190,113.81),(190,120));wire((170.16,120),(190,120));wire((190,120),(215,120));junc(190,120);label('RESET_N',215,120)
note('04 / 3.3 V BROWNOUT FLAG\n2.93 V nominal; 200 ms nominal release',130,93,1.5)
# Supply indicator.
symbol('R6','R','2k2 1%',245,110,fp='R_0603_1608Metric',nets={'1':'V3V3','2':'LED_A'},board=(94,81,0))
symbol('D1','LED','GREEN 0805',245,125,90,'LED_0805_2012Metric',{'1':'GND','2':'LED_A'},(98,81,180),'https://www.kingbrightusa.com/images/catalog/SPEC/APT2012SGC.pdf')
wire((245,100),(245,106.19));label('V3V3',245,100);wire((245,113.81),(245,121.19));label('LED_A',245,118)
wire((245,128.81),(245,136));label('GND',245,136)
for i,(bx,by) in enumerate([(53,53),(107,53),(53,89),(107,89)],1):symbol('H'+str(i),'MountingHole','M2.5 NPTH',30+(i-1)*25,164,fp='MountingHole_2.7mm_M2.5',board=(bx,by,0))
note('A0 ENGINEERING PROTOTYPE / NOT RELEASED FOR FABRICATION',28,15,3)
note('HassleFree FF-1 | Low-speed controls + supply supervisor',28,22,2)
note('REQUIREMENTS\n- Schmitt-capable 3.3 V host inputs; software debounce >= 10 ms.\n- RESET_N is a local supply flag, not a validated Zynq POR circuit.\n- Separate bench buttons emulate half/full press, not a two-stage shutter.\n- No sensor connection, no SLVS-EC, no battery path or ESD qualification.',135,151,1.4)
sch=f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{SHEET}") (paper "A4") (title_block (title "FF1 Control and Supply Supervisor") (date "2026-11-05") (rev "A0") (company "HassleFree FF-1") (comment 1 "Prototype only; no sensor or high-speed interface")) (lib_symbols\n'
bits=[re.sub(r'\((at|xy) ([-0-9.]+) ([-0-9.]+)',lambda m:'('+m[1]+' '+str(round(round(float(m[2])/1.27)*1.27,4))+' '+str(round(round(float(m[3])/1.27)*1.27,4)),v) for v in bits]
sch+='\n'.join(v.replace('(symbol "'+n+'"','(symbol "FF1:'+n+'"',1) for n,v in libs.items())+')\n'+'\n'.join(bits)+'\n(embedded_fonts no))\n'
(H/(NAME+'.kicad_sch')).write_text(sch)
(ROOT/'scripts/components.json').write_text(json.dumps(components,indent=2))
# Board with conservative two-layer low-frequency geometry, all footprints bundled.
b=p.BOARD();ds=b.GetDesignSettings();ds.m_MinClearance=p.FromMM(.2);ds.m_TrackMinWidth=p.FromMM(.25);ds.m_CopperEdgeClearance=p.FromMM(.5);ds.m_ViasMinSize=p.FromMM(.6);ds.m_MinThroughDrill=p.FromMM(.3);ds.m_HoleToHoleMin=p.FromMM(.25)
netnames=['GND','V3V3','HALF_RAW','HALF_N','FULL_RAW','FULL_N','RESET_N','LED_A']; nets={}
for nn in netnames:
 n=p.NETINFO_ITEM(b,"/"+nn);b.Add(n);nets[nn]=n
fps={}
def vec(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
for c in components:
 f=p.FootprintLoad(str(F),c['fp']);f.SetReference(c['ref']);f.SetValue(c['value']);f.SetFPID(p.LIB_ID('FF1',c['fp']));f.SetPath(p.KIID_PATH('/'+SHEET+'/'+c['uuid']));f.SetSheetfile(NAME+'.kicad_sch');f.SetSheetname('')
 x,y,a=c['board'];f.SetPosition(vec(x,y));f.SetOrientationDegrees(a)
 f.Reference().SetTextSize(vec(.8,.8));f.Reference().SetTextThickness(p.FromMM(.12));f.Value().SetVisible(False)
 for pad in f.Pads():
  if pad.GetNumber() in c['nets']:pad.SetNet(nets[c['nets'][pad.GetNumber()]])
 b.Add(f);fps[c['ref']]=f
# Place references to keep them readable and off pads.
for ref,f in fps.items():
 x,y,a=next(c['board'] for c in components if c['ref']==ref)
 if ref.startswith('SW'):f.Reference().SetPosition(vec(x+3.25,y-1.8))
 elif ref.startswith('H'):f.Reference().SetVisible(False)
 elif ref=='J1':f.Reference().SetPosition(vec(x,y-2.7))
 elif ref=='U1':f.Reference().SetPosition(vec(x,y-2.5))
 elif ref=='C1':f.Reference().SetPosition(vec(x+2,y))
 else:f.Reference().SetPosition(vec(x,y-1.8))
for a,z in [((50,50),(110,50)),((110,50),(110,92)),((110,92),(50,92)),((50,92),(50,50))]:
 e=p.PCB_SHAPE();e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(*a));e.SetEnd(vec(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(p.FromMM(.05));b.Add(e)
def text(s,x,y,size=1,layer=p.F_SilkS):
 t=p.PCB_TEXT(b);t.SetText(s);t.SetPosition(vec(x,y));t.SetTextSize(vec(size,size));t.SetTextThickness(p.FromMM(.15));t.SetLayer(layer);b.Add(t)
text('FF-1 CONTROL A0',80,52.5,1.3);text('HALF',75.25,64.5);text('FULL',97.25,64.5);text('3V3 ONLY',61.5,75.5,.8);text('ENGINEERING / NOT SENSOR PCB',80,89.8,.9)
for i,n in enumerate(['3V3','GND','HALF','FULL','RST','GND']):text(n,61.1,59+i*2.54,.8)
text('PWR',102,81,.8);text('NO 5V / NO BATTERY',95,85,.8)
p.SaveBoard(str(H/(NAME+'.kicad_pcb')),b)
project={'board':{'design_settings':{'rules':{'min_clearance':.2,'min_track_width':.25,'min_copper_edge_clearance':.5,'min_through_hole_diameter':.3,'min_via_diameter':.6,'min_hole_to_hole':.25,'min_silk_clearance':.15,'min_text_height':.8,'min_text_thickness':.12},'rule_severities':{},'drc_exclusions':[]}},'net_settings':{'classes':[{'name':'Default','clearance':.2,'track_width':.3,'via_diameter':.6,'via_drill':.3,'diff_pair_width':.25,'diff_pair_gap':.25,'diff_pair_via_gap':.25,'pcb_color':'rgba(0, 0, 0, 0)','schematic_color':'rgba(0, 0, 0, 0)','wire_width':6,'bus_width':12,'line_style':0}]},'meta':{'filename':NAME+'.kicad_pro','version':1}}
(H/(NAME+'.kicad_pro')).write_text(json.dumps(project,indent=2))
print('Created schematic and un-routed PCB; now run route_board.py and checks.')
