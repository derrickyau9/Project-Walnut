"""Independent FreeBSD native 24 MP runtime check; all data stays on work disk."""
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import time

ROOT=Path('/var/DaOS004-work/processing')
QA=ROOT/'qa-native-final'
QA.mkdir(exist_ok=True)
SOURCE=ROOT/'truth-24mp.dng'
WORKER=ROOT/'amd64/worker-build/hf-imager'

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def tiff(path):
    stream=path.open('rb');header=stream.read(8);endian='<' if header[:2]==b'II' else '>'
    assert struct.unpack(endian+'H',header[2:4])[0]==42
    stream.seek(struct.unpack(endian+'I',header[4:8])[0]);count=struct.unpack(endian+'H',stream.read(2))[0]
    tags={};sizes={1:1,2:1,3:2,4:4,5:8,11:4,12:8};formats={1:'B',3:'H',4:'I',11:'f',12:'d'}
    entries=[stream.read(12) for _ in range(count)]
    for entry in entries:
        tag,kind,n=struct.unpack(endian+'HHI',entry[:8]);size=sizes.get(kind,1)*n
        if size<=4:data=entry[8:8+size]
        else:stream.seek(struct.unpack(endian+'I',entry[8:])[0]);data=stream.read(size)
        tags[tag]=struct.unpack(endian+str(n)+formats[kind],data) if kind in formats else data
    def sample(x,y,channel=0):
        channels=tags.get(277,(1,))[0];rows=tags[278][0];offset=tags[273][y//rows]
        stream.seek(offset+((y%rows)*tags[256][0]*channels+x*channels+channel)*2)
        return struct.unpack(endian+'H',stream.read(2))[0]
    return stream,tags,sample

digest=sha(SOURCE)
assert digest=='da4a086eea221990c0b3a78267d8ba6e77286822513dc1e90163b3bf2e71e5a6'
report={'format':'daos.processing-native-validation.v1','platform':platform.platform(),
        'worker_sha256':sha(WORKER),'source_sha256':digest,'source_bytes':SOURCE.stat().st_size,
        'sensor_hardware_tested':False,'checks':[]}
for kind,suffix in [('develop_jpeg','.jpg'),('develop_tiff16','.tiff')]:
    scratch=QA/kind;scratch.mkdir(exist_ok=False);destination=QA/('truth-24mp-final'+suffix)
    request={'format':'daos.image-job.v1','job_id':kind,'kind':kind,'scratch':str(scratch),
      'inputs':[{'path':str(SOURCE),'sha256':digest,'black_level':64,'white_level':16383}],
      'destination':str(destination),'params':{}}
    start=time.monotonic();run=subprocess.run([str(WORKER)],input=json.dumps(request)+'\n',text=True,capture_output=True,timeout=180)
    (QA/(kind+'.jsonl')).write_text(run.stdout)
    messages=[json.loads(line) for line in run.stdout.splitlines() if line.strip()]
    assert run.returncode==0,(run.returncode,messages,run.stderr)
    receipt=next(event['result'] for event in messages if event['event']=='result')
    assert 0<receipt['peak_rss_bytes']<=192*1024*1024
    assert receipt['outputs'][0]['width']==6064 and receipt['outputs'][0]['height']==4040
    if kind=='develop_jpeg':
        ppm=QA/'independent-djpeg.ppm'
        independent=subprocess.run(['/usr/local/bin/djpeg','-outfile',str(ppm),str(destination)],capture_output=True,timeout=60)
        assert independent.returncode==0,independent.stderr
        with ppm.open('rb') as stream:
            tokens=[]
            while len(tokens)<4:
                line=stream.readline()
                if not line.startswith(b'#'):tokens.extend(line.split())
            assert tokens==[b'P6',b'6064',b'4040',b'255'],tokens
            assert ppm.stat().st_size-stream.tell()==6064*4040*3
        decoder='independent libjpeg-turbo djpeg -> exact PPM dimensions and byte count'
    else:
        original,raw,read_raw=tiff(SOURCE);decoded,tags,read=tiff(destination)
        assert tags[256]==(6064,) and tags[257]==(4040,) and tags[258]==(16,16,16)
        assert tags[259]==(1,) and tags[277]==(3,)
        pattern=((0,1),(1,2))
        for y,x in ((100,100),(100,101),(101,100),(101,101)):
            for channel in range(3):
                values=[read_raw(x+dx,y+dy) for dy in (-1,0,1) for dx in (-1,0,1) if pattern[(y+dy)%2][(x+dx)%2]==channel]
                value=read_raw(x,y) if pattern[y%2][x%2]==channel else sum(values)/len(values)
                expected=round(max(0,min(1,(value-64)/16319))*65535)
                assert abs(read(x,y,channel)-expected)<=2,(x,y,channel,read(x,y,channel),expected)
        original.close();decoded.close()
        decoder='independent Python struct TIFF tags/strip decoder + 12 CFA mathematical samples'
    assert sha(SOURCE)==digest
    report['checks'].append({'kind':kind,'elapsed_seconds':time.monotonic()-start,
       'independent_decoder':decoder,'output_sha256':sha(destination),'output_bytes':destination.stat().st_size,
       'width':6064,'height':4040,'receipt':receipt,'source_unchanged':True})
(QA/'native-validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
