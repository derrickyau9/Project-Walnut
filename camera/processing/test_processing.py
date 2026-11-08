"""Independent decoded-pixel/algorithm checks for the native, tiled processor.

Fixtures are synthetic ground truth, never claims of sensor/lens performance.
Run with the existing desktop numpy/Pillow/tifffile QA interpreter.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid

import numpy as np
from PIL import Image,ImageDraw,ImageFilter
import tifffile

ROOT=Path(__file__).resolve().parents[2]
QA=ROOT/'build/DaOS_Camera_004/processing/qa'
sys.path.insert(0,str(ROOT/'ui/on-device'))
from image_processing import run_job,ProcessingError,ProcessingCancelled,worker_path


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(worker_path(),'build hf-imager before native processing QA')
class NativeProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):QA.mkdir(parents=True,exist_ok=True)

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='suite-',dir=QA)
        self.root=Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(QA.resolve()))
        self.sources=[];self.number=0

    def tearDown(self):
        for path,digest in self.sources:self.assertEqual(sha(path),digest,'processing modified an original')
        self.temporary.cleanup()

    def source(self,pixels,name='source',linear=True):
        path=self.root/(name+'.tiff')
        tifffile.imwrite(path,np.round(np.clip(pixels,0,1)*65535).astype(np.uint16),photometric='rgb',metadata=None)
        digest=sha(path);self.sources.append((path,digest))
        return {'path':str(path),'sha256':digest,'linear':linear}

    def execute(self,kind,inputs,params=None):
        self.number+=1;identity='test'+str(self.number);scratch=self.root/identity;scratch.mkdir()
        destination=self.root/(identity+'.tiff')
        options=dict(params or {});options.setdefault('output_format','tiff16')
        request={'format':'daos.image-job.v1','job_id':identity,'kind':kind,'inputs':inputs,
                 'scratch':str(scratch),'destination':str(destination),'params':options}
        result=subprocess.run([str(worker_path())],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=120)
        messages=[json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        errors=[item for item in messages if item.get('event')=='error']
        self.assertEqual(result.returncode,0,(errors,result.stderr[-2000:]))
        receipt=next(item['result'] for item in messages if item.get('event')=='result')
        self.assertLessEqual(receipt.get('peak_rss_bytes',0),192*1024*1024)
        self.assertEqual(receipt['opencv'],'4.12.0')
        return receipt

    def pixels(self,receipt):return tifffile.imread(receipt['outputs'][0]['path']).astype(np.float32)/65535

    def test_16bit_wb_exposure_and_monotonic_curve(self):
        a=np.zeros((64,96,3),np.float32)+[.1,.2,.3]
        source=self.source(a)
        result=self.execute('develop_tiff16',[source],{'wb':[2,1,.5],'exposure_ev':1})
        np.testing.assert_allclose(self.pixels(result)[20,20],[.4,.4,.3],atol=4/65535)
        result=self.execute('develop_tiff16',[source],{'curve':[[0,0],[.2,.4],[1,1]]})
        self.assertGreater(self.pixels(result)[20,20,1],.39)
        self.assertFalse(result['color_calibrated'])

    def test_exact_crop_rotation_and_original_pixel_region(self):
        rng=np.random.default_rng(5);a=rng.random((96,128,3),dtype=np.float32)*.8
        source=self.source(a)
        result=self.execute('develop_tiff16',[source],{'crop':[.25,.25,.5,.5],'rotation':90})
        expected=np.rot90(a[24:72,32:96],-1)
        self.assertEqual(self.pixels(result).shape,expected.shape)
        np.testing.assert_allclose(self.pixels(result),expected,atol=2/65535)
        result=self.execute('develop_tiff16',[source],{'region':[30,20,17,13]})
        np.testing.assert_allclose(self.pixels(result),a[20:33,30:47],atol=2/65535)

    def test_single_frame_noise_sharpness_and_tiff_roundtrip(self):
        rng=np.random.default_rng(21);truth=np.zeros((96,128,3),np.float32)+.3
        noisy=truth+rng.normal(0,.025,truth.shape)
        source=self.source(noisy)
        clean=self.pixels(self.execute('develop_tiff16',[source],{'noise':1}))
        self.assertLess(float(np.mean((clean-truth)**2)),float(np.mean((noisy-truth)**2))*.7)
        first=self.execute('develop_tiff16',[self.source(truth,'flat')])
        path=Path(first['outputs'][0]['path'])
        second=self.execute('develop_tiff16',[{'path':str(path),'sha256':sha(path)}])
        np.testing.assert_allclose(self.pixels(second),truth,atol=2/65535)

    def test_jpeg_exif_orientation_is_normalized(self):
        a=np.zeros((64,96,3),np.uint8);a[:,:48]=[220,20,20];a[:,48:]=[20,20,220]
        image=Image.fromarray(a);exif=Image.Exif();exif[274]=6
        path=self.root/'rotated.jpg';image.save(path,quality=98,exif=exif)
        self.sources.append((path,sha(path)))
        result=self.execute('develop_tiff16',[{'path':str(path),'sha256':sha(path)}])
        output=self.pixels(result)
        self.assertEqual(output.shape,(96,64,3))
        self.assertGreater(float(output[:30,:,0].mean()),.5)
        self.assertGreater(float(output[65:,:,2].mean()),.5)

    def test_denoise_stack_reduces_independent_noise(self):
        rng=np.random.default_rng(9);truth=np.zeros((128,192,3),np.float32)+.3
        frames=[truth+rng.normal(0,.035,truth.shape) for _ in range(6)]
        result=self.execute('denoise_stack',[self.source(a,str(n)) for n,a in enumerate(frames)],{'align':False})
        before=np.mean((frames[0]-truth)**2);after=np.mean((self.pixels(result)-truth)**2)
        self.assertLess(after,before*.3)

    def test_hdr_recovers_gradient_where_reference_clips(self):
        x=np.linspace(.01,4,192,dtype=np.float32);scene=np.broadcast_to(x[None,:,None],(96,192,3)).copy()
        exposures=[10000,2500,625]
        frames=[self.source(np.clip(scene*(exposure/10000),0,1),str(n)) for n,exposure in enumerate(exposures)]
        result=self.execute('hdr',frames,{'exposures_us':exposures,'align':False})
        output=self.pixels(result)
        self.assertGreater(float(np.std(output[:,140:180,0])),.002)
        self.assertTrue(np.isfinite(output).all())

    def test_exposure_fusion_uses_multiple_frames(self):
        x=np.linspace(.04,.8,192,dtype=np.float32);scene=np.broadcast_to(x[None,:,None],(96,192,3)).copy()
        a=self.source(scene,'a');b=self.source(np.minimum(scene*2,1),'b')
        fused=self.pixels(self.execute('exposure_fusion',[a,b],{'align':False}))
        self.assertGreater(float(np.mean(np.abs(fused-scene))),.005)
        self.assertGreater(float(np.mean(np.abs(fused-np.minimum(scene*2,1)))),.005)

    def test_focus_stack_selects_sharp_region_from_each_frame(self):
        yy,xx=np.mgrid[:128,:192];truth=np.repeat((((xx//3+yy//3)%2)*.6+.2)[:,:,None],3,axis=2).astype('float32')
        blurred=np.asarray(Image.fromarray((truth*255).astype('uint8')).filter(ImageFilter.GaussianBlur(2))).astype('float32')/255
        a=truth.copy();a[:,96:]=blurred[:,96:];b=truth.copy();b[:,:96]=blurred[:,:96]
        result=self.pixels(self.execute('focus_stack',[self.source(a,'a'),self.source(b,'b')],{'align':False}))
        error=float(np.mean((result[:,12:180]-truth[:,12:180])**2))
        self.assertLess(error,float(np.mean((a-truth)**2))*.35)

    def test_dark_flat_correction_and_film_base_inversion(self):
        illum=np.linspace(.3,.8,192,dtype=np.float32)[None,:,None];illum=np.broadcast_to(illum,(96,192,3)).copy()
        dark=np.zeros_like(illum)+.02;photo=.35*illum+dark
        result=self.pixels(self.execute('darkflat',[self.source(photo,'photo'),self.source(dark,'dark'),self.source(illum+dark,'flat')]))
        self.assertLess(float(np.std(result)),.0001)
        transmission=np.broadcast_to(np.linspace(.05,.95,192,dtype=np.float32)[None,:,None],(96,192,3));base=np.array([1,.65,.35],np.float32)
        negative=self.pixels(self.execute('negative',[self.source(transmission*base,'film')],{'film_base':base.tolist()}))
        np.testing.assert_allclose(negative,1-transmission,atol=5/65535)

    def test_astro_phase_alignment_restores_translated_stars(self):
        rng=np.random.default_rng(3);a=np.zeros((192,256,3),np.float32)+.01
        for x,y in rng.integers([20,20],[230,165],size=(50,2)):
            a[y-1:y+2,x-1:x+2]=.8
        frames=[a,np.roll(a,(2,-3),(0,1)),np.roll(a,(-1,4),(0,1))]
        result=self.execute('astro_stack',[self.source(x,str(n)) for n,x in enumerate(frames)])
        output=self.pixels(result)
        self.assertLess(float(np.mean((output[12:-12,12:-12]-a[12:-12,12:-12])**2)),.001)
        self.assertEqual(len(result['processing_provenance']['alignment']),2)

    def test_panorama_registers_overlapping_views(self):
        rng=np.random.default_rng(8);a=(rng.random((240,800,3))*.5+.2).astype('float32')
        drawing=Image.fromarray(np.uint8(a*255));d=ImageDraw.Draw(drawing)
        for x in range(30,780,60):d.ellipse((x,45,x+20,65),fill=(255,240,120));d.text((x,160),str(x),fill=(10,10,10))
        a=np.asarray(drawing).astype('float32')/255
        result=self.execute('panorama',[self.source(a[:,:500],'left'),self.source(a[:,300:],'right')])
        self.assertGreater(result['outputs'][0]['width'],760)
        self.assertLess(result['outputs'][0]['width'],850)
        self.assertGreater(result['processing_provenance']['alignment'][0]['inliers'],8)

    def test_exact_histogram_and_analysis_overlays(self):
        source=self.source(np.zeros((96,128,3),np.float32)+.18)
        result=self.execute('analyze',[source],{'overlays':['zebra','falsecolor','peaking']})
        analysis=result['analysis'];self.assertEqual(analysis['pixel_count'],96*128)
        for hist in analysis['histogram']:self.assertEqual(sum(hist),96*128)
        self.assertAlmostEqual(analysis['suggested_exposure_ev'],0,places=3)
        for out in result['outputs']:
            image=Image.open(out['path']);image.load();self.assertEqual(image.size,(128,96))

    def test_cancelled_adapter_keeps_original_and_no_published_output(self):
        source=QA/'truth-24mp.dng'
        if not source.exists():self.skipTest('24 MP fixture not present')
        digest=sha(source);cancel=threading.Event();destination=self.root/'cancelled.jpg'
        class Client:
            def open_capture(self,identity):return source.open('rb')
        def progress(event):
            if event.get('stage')=='process':cancel.set()
        with self.assertRaises(ProcessingCancelled):run_job('develop_jpeg',Client(),'test',{},str(destination),progress,cancel,work_dir=self.root/'jobs')
        self.assertFalse(destination.exists());self.assertEqual(sha(source),digest)

    def test_adapter_rejects_changed_catalog_fingerprint(self):
        source=self.source(np.zeros((32,48,3),np.float32)+.25)
        class Client:
            def open_capture(self,identity):return Path(source['path']).open('rb')
        destination=self.root/'must-not-exist.jpg'
        for field in ('sha256','fingerprint'):
            with self.assertRaisesRegex(ProcessingError,'fingerprint changed'):
                run_job('develop_jpeg',Client(),'original',
                        {'sources':[{'original_request_uuid':'original',field:'0'*64}]},
                        destination,work_dir=self.root/'jobs')
            self.assertFalse(destination.exists())

    def test_24mp_independent_decoders_and_original_sha(self):
        source=QA/'truth-24mp.dng'
        if not source.exists():self.skipTest('generate 24 MP synthetic native fixture first')
        digest=sha(source);reference={'path':str(source),'sha256':digest,'black_level':64,'white_level':16383}
        for kind in ('develop_jpeg','develop_tiff16'):
            result=self.execute(kind,[reference],{'output_format':'jpeg' if kind=='develop_jpeg' else 'tiff16'})
            output=Path(result['outputs'][0]['path'])
            if kind=='develop_jpeg':
                image=Image.open(output);image.load();self.assertEqual(image.size,(6064,4040))
            else:
                with tifffile.TiffFile(output) as tiff:
                    self.assertEqual(tiff.pages[0].shape,(4040,6064,3));self.assertEqual(tiff.pages[0].dtype,np.dtype('uint16'))
                raw=tifffile.memmap(source);developed=tifffile.memmap(output)
                pattern=((0,1),(1,2))
                for y,x in ((100,100),(100,101),(101,100),(101,101)):
                    for channel in range(3):
                        if pattern[y%2][x%2]==channel:value=float(raw[y,x])
                        else:value=float(np.mean([raw[y+dy,x+dx] for dy in (-1,0,1) for dx in (-1,0,1) if pattern[(y+dy)%2][(x+dx)%2]==channel]))
                        expected=round(np.clip((value-64)/16319,0,1)*65535)
                        self.assertLessEqual(abs(int(developed[y,x,channel])-expected),2)
                del raw,developed
            self.assertGreater(result.get('peak_rss_bytes',0),0)
        self.assertEqual(sha(source),digest)


if __name__=='__main__':unittest.main()
