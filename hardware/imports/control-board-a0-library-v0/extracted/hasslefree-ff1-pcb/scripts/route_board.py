#!/usr/bin/env python3
"""Conservative grid router for this low-speed prototype; KiCad DRC is authoritative."""
import os
os.environ.update(XDG_CONFIG_HOME='/tmp/ff1-config',XDG_CACHE_HOME='/tmp/ff1-cache',XDG_DATA_HOME='/tmp/ff1-data')
import pcbnew as p, numpy as np, math, heapq, json
from scipy.ndimage import distance_transform_edt
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; path=ROOT/'hardware/FF1_Control_A0.kicad_pcb'; b=p.LoadBoard(str(path))
if len(list(b.GetTracks())):raise RuntimeError('Input is already routed. Rebuild only if overwriting design edits is intended.')
G=.1; X0=50.;Y0=50.;NX=601;NY=421
occ=np.zeros((2,NY,NX),dtype=np.int16)
for l in [0,1]:
 occ[l,:7,:]=-1;occ[l,-7:,:]=-1;occ[l,:,:7]=-1;occ[l,:,-7:]=-1

def grid(x,y):return round((x-X0)/G),round((y-Y0)/G)
def world(x,y):return X0+x*G,Y0+y*G
def vec(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def disk(x,y,r,net,layers):
 cx,cy=grid(x,y); rr=math.ceil(r/G)+1
 x1=max(0,cx-rr);x2=min(NX,cx+rr+1);y1=max(0,cy-rr);y2=min(NY,cy+rr+1)
 yy,xx=np.ogrid[y1:y2,x1:x2];mask=((X0+xx*G-x)**2+(Y0+yy*G-y)**2 <= (r+.072)**2)
 for l in layers:occ[l,y1:y2,x1:x2][mask]=net

def rectangle(x,y,sx,sy,net,layers):
 x1=max(0,math.floor((x-sx/2-X0)/G));x2=min(NX,math.ceil((x+sx/2-X0)/G)+1)
 y1=max(0,math.floor((y-sy/2-Y0)/G));y2=min(NY,math.ceil((y+sy/2-Y0)/G)+1)
 for l in layers:occ[l,y1:y2,x1:x2]=net
pads={};holes=[];smd_rects=[]
for f in b.GetFootprints():
 for pad in f.Pads():
  x=pad.GetPosition().x/1e6;y=pad.GetPosition().y/1e6; net=pad.GetNetCode();
  if pad.GetDrillSize().x:holes.append((x,y,pad.GetDrillSize().x/2e6))
  layers=[0,1] if pad.GetAttribute()!=p.PAD_ATTRIB_SMD else [0]
  if pad.GetAttribute()==p.PAD_ATTRIB_NPTH:disk(x,y,3.,-1,[0,1]);continue
  sx=pad.GetSize().x/1e6;sy=pad.GetSize().y/1e6
  if round(pad.GetOrientationDegrees())%180==90:sx,sy=sy,sx
  rectangle(x,y,sx,sy,net,layers)
  if layers==[0]:smd_rects.append((x,y,sx,sy))
  pads.setdefault(pad.GetNetname().lstrip("/"),[]).append({'x':x,'y':y,'layers':layers,'net':net,'ref':f.GetReference()+'.'+pad.GetNumber()})

def route(a,z):
 net=a['net']; blocked=(occ!=0)&(occ!=net)
 dist=np.array([distance_transform_edt(~blocked[l])*G for l in [0,1]])
 free=dist>.40 # 0.20 clearance + 0.15 half width + quantization margin
 viafree=(dist[0]>.55)&(dist[1]>.55)
 yygrid,xxgrid=np.ogrid[:NY,:NX]
 for hx,hy,hr in holes:viafree &= (X0+xxgrid*G-hx)**2+(Y0+yygrid*G-hy)**2 > (hr+.15+.25+.08)**2
 for px,py,pw,ph in smd_rects:viafree &= (abs(X0+xxgrid*G-px)>pw/2+.55)|(abs(Y0+yygrid*G-py)>ph/2+.55)
 sx,sy=grid(a['x'],a['y']);tx,ty=grid(z['x'],z['y']);targets={(l,ty,tx) for l in z['layers']}
 def h(x,y):
  dx=abs(tx-x);dy=abs(ty-y);return max(dx,dy)+.41421356*min(dx,dy)
 q=[];cost={};prev={}
 for l in a['layers']:
  k=(l,sy,sx);cost[k]=0;heapq.heappush(q,(h(sx,sy),0,k));prev[k]=None
 steps=[(-1,0,1),(1,0,1),(0,-1,1),(0,1,1),(-1,-1,1.41421356),(-1,1,1.41421356),(1,-1,1.41421356),(1,1,1.41421356)]
 goal=None
 while q:
  _,g,k=heapq.heappop(q)
  if g>cost[k]+1e-6:continue
  if k in targets:goal=k;break
  l,y,x=k
  for dx,dy,dc in steps:
   xx=x+dx;yy=y+dy
   if xx<0 or xx>=NX or yy<0 or yy>=NY or not free[l,yy,xx]:continue
   if dx and dy and (not free[l,y,xx] or not free[l,yy,x]):continue
   nk=(l,yy,xx);ng=g+dc
   if ng<cost.get(nk,1e30):cost[nk]=ng;prev[nk]=k;heapq.heappush(q,(ng+h(xx,yy),ng,nk))
  if viafree[y,x]:
   nk=(1-l,y,x);ng=g+18
   if ng<cost.get(nk,1e30):cost[nk]=ng;prev[nk]=k;heapq.heappush(q,(ng+h(x,y),ng,nk))
 if goal is None:raise RuntimeError('Could not route '+a['ref']+' to '+z['ref'])
 nodes=[]
 while goal is not None:nodes.append(goal);goal=prev[goal]
 nodes.reverse()
 def segment(x1,y1,x2,y2,l):
  if abs(x1-x2)+abs(y1-y2)<.000001:return
  t=p.PCB_TRACK(b);t.SetStart(vec(x1,y1));t.SetEnd(vec(x2,y2));t.SetWidth(p.FromMM(.3));t.SetLayer(p.F_Cu if l==0 else p.B_Cu);t.SetNetCode(net);b.Add(t)
  n=max(1,math.ceil(math.hypot(x2-x1,y2-y1)/(.05)))
  for i in range(n+1):disk(x1+(x2-x1)*i/n,y1+(y2-y1)*i/n,.15,net,[l])
 # Exact pad center to grid endpoints; all endpoint corrections lie inside pad copper.
 segment(a['x'],a['y'],*world(nodes[0][2],nodes[0][1]),nodes[0][0])
 start=nodes[0];prior=nodes[0];direction=None
 for k in nodes[1:]:
  if k[0]!=prior[0]:
   segment(*world(start[2],start[1]),*world(prior[2],prior[1]),prior[0])
   x,y=world(k[2],k[1]);v=p.PCB_VIA(b);v.SetPosition(vec(x,y));v.SetWidth(p.F_Cu,p.FromMM(.6));v.SetDrill(p.FromMM(.3));v.SetViaType(p.VIATYPE_THROUGH);v.SetLayerPair(p.F_Cu,p.B_Cu);v.SetNetCode(net);b.Add(v);disk(x,y,.3,net,[0,1]);holes.append((x,y,.15));start=k;direction=None
  else:
   d=(k[2]-prior[2],k[1]-prior[1])
   if direction is not None and d!=direction:segment(*world(start[2],start[1]),*world(prior[2],prior[1]),prior[0]);start=prior
   direction=d
  prior=k
 segment(*world(start[2],start[1]),*world(prior[2],prior[1]),prior[0]);segment(*world(prior[2],prior[1]),z['x'],z['y'],prior[0])
 return cost[nodes[-1]]
summary=[]
for netname in ['HALF_RAW','FULL_RAW','HALF_N','FULL_N','RESET_N','LED_A','V3V3','GND']:
 pp=sorted(pads[netname],key=lambda v:(v['ref'],v['x'],v['y']));done=[pp[0]];todo=pp[1:]
 while todo:
  a,z=min(((aa,zz) for aa in done for zz in todo),key=lambda q:math.hypot(q[0]['x']-q[1]['x'],q[0]['y']-q[1]['y']))
  length=route(a,z);print(netname,a['ref'],'->',z['ref'],'grid cost',round(length,2),flush=True);done.append(z);todo.remove(z);summary.append({'net':netname,'from':a['ref'],'to':z['ref'],'grid_cost':length})
p.SaveBoard(str(path),b)
(ROOT/'reports/router_log.json').write_text(json.dumps(summary,indent=2))
print('Routed tracks:',len(list(b.GetTracks())))
