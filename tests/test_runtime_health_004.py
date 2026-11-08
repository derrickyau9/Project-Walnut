import importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ui/on-device'))
from runtime_health import probe_imager
from image_processing import worker_path,PROCESSOR_VERSION
spec=importlib.util.spec_from_file_location('health_installer',ROOT/'system/freebsd/raspberrypi/deploy_camera_app.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class RuntimeHealthTests(unittest.TestCase):
    def test_actual_native_worker_identity_probe(self):
        if not worker_path():self.skipTest('native desktop image worker not built')
        result=probe_imager();self.assertTrue(result['ready'],result);self.assertEqual(PROCESSOR_VERSION,result['processor'])
    def test_missing_or_mismatched_image_worker_is_not_healthy(self):
        with patch('image_processing.worker_path',return_value=None):self.assertFalse(probe_imager()['ready'])
        class Result:stdout=b'{"format":"daos.image-processing-capabilities.v1","processor":"old","opencv":"4.12.0"}'
        with patch('image_processing.worker_path',return_value=Path('unused')),patch('runtime_health.subprocess.run',return_value=Result()):self.assertFalse(probe_imager()['ready'])
    def readiness(self,text):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'log';path.write_text(text,encoding='utf-8');now=[0.]
            with patch.object(installer,'Path',return_value=path),patch.object(installer.os,'kill',return_value=None),patch.object(installer.time,'monotonic',side_effect=lambda:now[0]),patch.object(installer.time,'sleep',side_effect=lambda value:now.__setitem__(0,now[0]+value)):
                return installer.wait_ready(0,'camera-experience-004')
    def test_visible_diagnostics_window_does_not_count_as_healthy_application(self):
        gui='daemon: DaOS_GUI_EVENT '+json.dumps({'event':'ui-ready','pid':os.getpid()})+'\n'
        with self.assertRaises(RuntimeError):self.readiness(gui)
        health={'format':'DaOS-camera-app-health-v1','version':'camera-experience-003','backend':'native','pid':os.getpid(),'authority_ready':True,'worker_ready':True}
        with self.assertRaises(RuntimeError):self.readiness(gui+'daemon: DaOS_APP_HEALTH '+json.dumps(health)+'\n')
        health['version']='camera-experience-004';health['worker_ready']=False
        with self.assertRaises(RuntimeError):self.readiness(gui+'DaOS_APP_HEALTH '+json.dumps(health)+'\n')
        health['worker_ready']=True
        self.assertEqual(os.getpid(),self.readiness(gui+'daemon: DaOS_APP_HEALTH '+json.dumps(health)+'\n'))
if __name__=='__main__':unittest.main()
