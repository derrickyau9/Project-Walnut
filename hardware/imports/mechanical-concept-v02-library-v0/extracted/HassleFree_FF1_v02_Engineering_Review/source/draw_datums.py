"""Draw the optical packaging schematic from named datum parameters, not pixels."""
from pathlib import Path
import json
R=Path(__file__).resolve().parents[1]
P=json.loads((R/'source/parameters.json').read_text())
x=lambda z:625+11*z
parts=[]
def line(x1,y1,x2,y2,color='#52636a',width=2,dash=''):
    parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" stroke-dasharray="{dash}"/>')
def rect(x0,y,w,h,color,stroke='none',rx=0):
    parts.append(f'<rect x="{x0}" y="{y}" width="{w}" height="{h}" fill="{color}" stroke="{stroke}" rx="{rx}"/>')
def text(s,x0,y,size=20,color='#253940',anchor='start',weight='400'):
    parts.append(f'<text x="{x0}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{s}</text>')
def dim(a,b,y,label,color='#52636a'):
    line(x(a),y,x(b),y,color)
    for v in [a,b]: line(x(v),y-7,x(v),y+7,color)
    text(label,(x(a)+x(b))/2,y-12,19,color,'middle','600')

rect(0,0,1600,1000,'#f5f6f3')
text('HassleFree FF-1',70,77,44,weight='700')
text('OPTICAL DATUM / CONCEPT PACKAGING',72,112,18,'#6b7d82')
text('v0.2  •  millimetres  •  2026-11-05',1520,83,18,'#6b7d82','end')
rect(70,225,1120,395,'#e7ece9',rx=16)
rect(x(-45),264,x(12)-x(-45),310,'#d5dcda','#849690',14)
rect(x(-42),280,x(10)-x(-42),278,'#f5f6f3')
text('CONCEPT SHELL',x(-43),254,17,'#63776f')
rect(x(-45.8),320,33,195,'#5a6c70',rx=5)
rect(x(-37),304,17.6,220,'#417359')
rect(x(-29.5),289,17.6,250,'#66977a')
rect(x(-23),315,22,200,'#b37856')
rect(x(-5),328,33,174,'#bda17b')
rect(x(0)-4,344,8,144,'#007f79')
for y in [298,510]:
    rect(x(10),y,22,32,'#25a393')
    rect(x(12),y,66,32,'#aab6b9')
    rect(x(18),y,286,32,'#dde5e3','#8c9d98')
for z,c in [(0,'#007f79'),(12,'#25a393'),(18,'#516c76'),(44,'#8c9d98')]:
    line(x(z),230,x(z),585,c,2,'6 7')
line(90,416,1170,416,'#7f9794',2,'10 7')
text('OPTICAL AXIS  +Z  →',895,399,18,'#637f79')
dim(0,44,165,'EF nominal flange distance  44 mm')
dim(0,18,210,'E nominal  18 mm')
dim(0,12,654,'12 mm common interface','#00877f')
dim(18,44,654,'26 mm cartridge difference')
text('Rear',x(-45),598,17,'#6b7d82','middle')
text('Sensor',x(0),598,17,'#007f79','middle','600')
text('Z = 0',x(0),621,17,'#007f79','middle')
text('HUMI',x(12),598,17,'#00877f','middle')
text('E',x(18),598,17,'#516c76','middle')
text('EF',x(44),598,17,'#63776f','middle')
text('ALTERNATIVES, NOT STACKED',945,284,16,'#63776f','middle')

text('STRUCTURAL INTENT',1250,250,20,weight='700')
for i,(s,c) in enumerate([('Metal load frame','#849690'),('3-point sensor carrier','#bda17b'),('36 × 24 active area','#007f79'),('HUMI service interface','#25a393'),('Control PCB envelope','#417359'),('Heat spreader envelope','#b37856')]):
    yy=292+i*44;rect(1250,yy-16,18,18,c);text(s,1281,yy,17)
text('Independent sensor support',1250,596,16,'#63776f')
text('Removable E / EF cartridge',1250,620,16,'#63776f')
text('Optical datum stays fixed',1250,644,16,'#63776f')

rect(70,718,1460,204,'#e2e8e4',rx=14)
text('WHAT THESE DIMENSIONS MEAN',96,757,20,weight='700')
text('36 × 24 mm is the required active area. 18 / 44 mm are public nominal flange references.',96,798,22)
text('Every shell, bore, bolt circle, wall and component envelope remains a provisional design assumption.',96,834,21)
text('No bayonet profile, manufacturing tolerance, optical clearance, thermal or strength certification is provided.',96,870,21)
text('Schematic only. Transverse heights are illustrative. Inspect STEP and the editable source for three-dimensional geometry.',72,961,17,'#6b7d82')
svg='<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000"><g font-family="DejaVu Sans, sans-serif">'+''.join(parts)+'</g></svg>'
(R/'renders/06_optical_datums.svg').write_text(svg)
try:
    import cairosvg
    cairosvg.svg2png(bytestring=svg.encode(),write_to=str(R/'renders/06_optical_datums.png'))
except ImportError:
    try:
        import fitz
        document=fitz.open(stream=svg.encode(),filetype='svg')
        document[0].get_pixmap().save(str(R/'renders/06_optical_datums.png'))
    except ImportError: pass
