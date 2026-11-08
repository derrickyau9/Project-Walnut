import array,os,struct,sys,tempfile,threading,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ui/on-device'))
from raw_analysis import raw_histogram
from camera.dng.inspect import inspect_dng

class RawHistogramTests(unittest.TestCase):
    def test_raw_counts_match_unmodified_cfa_samples_and_cancel(self):
        # Reuse the independent raw-writer fixture, not the image worker's RGB.
        from camera.camerad.service import CameraService
        import time,uuid,hashlib
        with tempfile.TemporaryDirectory() as directory:
            service=CameraService(Path(directory)/'camera');lease=service.acquire('raw-analysis');identity=str(uuid.uuid4())
            service.capture(dict(service.get_state()['settings'],request_id=identity,token=lease['token'],control_epoch=lease['epoch']))
            deadline=time.monotonic()+5
            while service.query_capture(identity)['status']!='complete':
                if time.monotonic()>deadline:self.fail('fixture timed out')
                time.sleep(.01)
            path=service.root/'photos'/(identity+'.dng');before=hashlib.sha256(path.read_bytes()).hexdigest();result=raw_histogram(path)
            with path.open('rb') as stream:
                info=inspect_dng(stream);stream.seek(info['strip_offsets'][0]);values=struct.unpack('<'+str(info['width']*info['height'])+'H',stream.read(info['width']*info['height']*2))
            expected=[0]*256
            for value in values:expected[min(255,int(value*255/info['white_level']))]+=1
            self.assertEqual(expected,result['combined']);self.assertEqual(len(values),sum(result['combined']))
            self.assertEqual([len(values)//4]*4,[sum(channel) for channel in result['histogram']]);self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
            cancel=threading.Event();cancel.set()
            with self.assertRaises(InterruptedError):raw_histogram(path,cancel=cancel)
            service.close()
if __name__=='__main__':unittest.main()
