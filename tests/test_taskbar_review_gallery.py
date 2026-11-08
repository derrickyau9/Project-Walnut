"""Raw-frame fidelity, attribution and publication checks; no Qt or camera."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from tools import create_taskbar_review_gallery as gallery


def png():
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',800,600,8,2,0,0,0))+
            chunk(b'IDAT',zlib.compress((b'\0'+b'\x10\x20\x30'*800)*600))+chunk(b'IEND',b''))


class TaskbarReviewGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.image=png()
        self.report=self.fixture()
        self.write()

    def fixture(self):
        def observation(elapsed,progress):
            return {'elapsed_ms':elapsed,'progress':progress,
                    'viewfinder_width':800-72*progress,'viewfinder_height':534*(800-72*progress)/800}
        def entry(name,progress=1,elapsed=10):
            return {'file':name,'width':800,'height':600,'sha256':hashlib.sha256(self.image).hexdigest(),
                    'requested_observation':observation(elapsed,progress),
                    'ready_observation':observation(elapsed,progress),'route':'capture'}
        static=[entry(str(index).zfill(2)+'-static.png') for index in range(5)]
        frames=[]
        for direction,values,base in [('collapse',(1,.5,0),100),('expand',(0,.5,1),500)]:
            for index,(progress,request,ready,scheduled) in enumerate(zip(values,(3,42,153),(11,58,171),(0,40,80))):
                value=entry(direction+'-'+str(index)+'.png',progress,base+request)
                value.update({'direction':direction,'request_after_release_ms':request,
                              'ready_after_release_ms':ready,'scheduled_offset_ms':scheduled,
                              'intermediate_observation_interval':index==1,
                              'ready_observation':observation(base+ready,progress)})
                frames.append(value)
        sources={r'C:\Users\person\private-source.py':'a'*64}
        camera={'request_id':'','settings':{'sensor_mode':'sim-small'}}
        return {'format':gallery.FORMAT,'passed':True,'synthetic':True,
                'fixture_closed_cleanly':True,'source_unchanged':True,
                'physical_camera_validated':False,'physical_display_validated':False,
                'appliance_image_boot_validated':False,'backend':gallery.BACKEND,
                'preview_source':gallery.PREVIEW,'qml_warnings':[],
                'journal_rows':0,'planned_shutter_actions':0,'observed_shutter_actions':0,
                'source_sha256_before':sources,'source_sha256_after':copy.deepcopy(sources),
                'camera_before':camera,'camera_after':copy.deepcopy(camera),
                'runtime':{'system':'Windows','machine':'AMD64','qpa':'offscreen',
                           'qt':'5.15.2','pyqt':'5.15.10','python':'3.12.7'},
                'screenshots':static,'animation_frames':frames,'animation_frame_count':6,
                'observed_intermediate_intervals':{'collapse':1,'expand':1},
                'timing_note':'</script><img src="https://example.invalid/private-path">',
                'private_path':r'C:\Users\person\private-runtime'}

    def write(self):
        for record in self.report['screenshots']+self.report['animation_frames']:
            name=record['file']
            if isinstance(name,str) and gallery.NAME.fullmatch(name):
                (self.directory/name).write_bytes(self.image)
        (self.directory/'review.json').write_text(json.dumps(self.report),encoding='utf-8')

    def create(self):
        return gallery.create_gallery(self.directory,self.directory/'review.html')

    def refused(self,pattern=None):
        with self.assertRaisesRegex(ValueError,pattern or '.'):
            self.create()
        self.assertFalse((self.directory/'review.html').exists())

    def test_raw_pngs_runtime_and_recorded_ready_clock_are_preserved_without_private_report(self):
        original=(self.directory/'review.json').read_bytes()
        result=self.create()
        text=(self.directory/'review.html').read_text(encoding='utf-8')
        encoded=re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)',text)
        self.assertEqual(11,len(encoded))
        self.assertTrue(all(base64.b64decode(value)==self.image for value in encoded))
        players=json.loads(re.search(r'<script id="taskbar-data" type="application/json">(.*?)</script>',text,re.S).group(1))
        self.assertEqual([11,58,171],[frame['ready_ms'] for frame in players['collapse']])
        self.assertEqual([0,40,80],[frame['scheduled_ms'] for frame in players['collapse']])
        self.assertIn('next.ready_ms-current.ready_ms',text)
        self.assertNotIn('setInterval',text)
        self.assertIn('Windows / AMD64 / 5.15.2 / offscreen',text)
        self.assertNotIn('private-source',text)
        self.assertNotIn('private-runtime',text)
        self.assertNotIn('example.invalid',text)
        self.assertNotIn('<img src="https:',text)
        self.assertIn('connect-src \'none\'',text)
        self.assertEqual(original,(self.directory/'review.json').read_bytes())
        self.assertEqual(hashlib.sha256((self.directory/'review.html').read_bytes()).hexdigest(),result['sha256'])
        self.assertEqual(5,result['static_count'])
        self.assertEqual(6,result['animation_frame_count'])

    def test_failed_unclosed_changed_or_no_intermediate_reports_are_refused(self):
        base=copy.deepcopy(self.report)
        for key,value in [('passed',False),('fixture_closed_cleanly',False),('source_unchanged',False),
                          ('observed_shutter_actions',1),('journal_rows',1),('physical_display_validated',True)]:
            with self.subTest(key=key):
                self.report=copy.deepcopy(base);self.report[key]=value;self.write();self.refused()
        self.report=copy.deepcopy(base)
        self.report['animation_frames'][1]['requested_observation']['progress']=1
        self.report['animation_frames'][1]['ready_observation']['progress']=1
        self.report['animation_frames'][1]['intermediate_observation_interval']=False
        self.report['observed_intermediate_intervals']['collapse']=0
        self.write();self.refused('intermediate')

    def test_corrupt_hash_crc_dimensions_and_traversal_are_rejected(self):
        base=copy.deepcopy(self.report)
        self.report['screenshots'][0]['sha256']='0'*64
        self.write();self.refused('SHA256 mismatch')
        self.report=copy.deepcopy(base)
        corrupted=self.image[:-1]+bytes([self.image[-1]^1])
        self.report['screenshots'][0]['sha256']=hashlib.sha256(corrupted).hexdigest()
        self.write();(self.directory/self.report['screenshots'][0]['file']).write_bytes(corrupted)
        self.refused('CRC mismatch')
        self.report=copy.deepcopy(base);self.report['screenshots'][0]['width']=3
        self.write();self.refused('dimensions')
        for name in ('../outside.png','/tmp/outside.png',r'C:\outside.png','child/frame.png','Same.png'):
            with self.subTest(name=name):
                self.report=copy.deepcopy(base);self.report['screenshots'][0]['file']=name
                self.write();self.refused('contained PNG')

    def test_hardlinked_inputs_and_existing_output_are_preserved_and_refused(self):
        linked=self.directory/'another-link'
        os.link(self.directory/'00-static.png',linked)
        self.refused('without links')
        linked.unlink()
        owner=self.directory/'review.html'
        owner.write_bytes(b'owner content')
        with self.assertRaisesRegex(ValueError,'existing output'):
            self.create()
        self.assertEqual(b'owner content',owner.read_bytes())

    def test_bounds_runtime_and_forged_or_nonfinite_timing_are_rejected(self):
        with patch.object(gallery,'MAX_REPORT_BYTES',1):
            self.refused('bounded regular')
        with patch.object(gallery,'MAX_TOTAL_IMAGE_BYTES',len(self.image)):
            self.refused('total image')
        base=copy.deepcopy(self.report)
        self.report['runtime']['machine']='AMD64<script>'
        self.write();self.refused('runtime token')
        for field,value in [('ready_after_release_ms',float('nan')),('request_after_release_ms',True),
                            ('ready_after_release_ms',1)]:
            with self.subTest(field=field,value=value):
                self.report=copy.deepcopy(base);self.report['animation_frames'][1][field]=value
                self.write();self.refused()
        self.report=copy.deepcopy(base)
        self.report['animation_frames'][1]['ready_after_release_ms']=99
        self.write();self.refused('timing observations disagree')
        self.report=copy.deepcopy(base)
        self.report['animation_frames'][1]['request_after_release_ms']+=10
        self.report['animation_frames'][1]['ready_after_release_ms']+=10
        self.write();self.refused('common release origin')
        self.report=copy.deepcopy(base)
        self.report['camera_before']={'request_id':''}
        self.report['camera_after']={'request_id':''}
        self.write();self.refused('identity/settings')

    def test_v2_sampling_clock_variable_counts_and_release_dispatch_remain_distinct(self):
        self.report['format']=gallery.FORMAT_V2
        # V2 permits actual sample counts to differ by direction. The first
        # collapse grab straddles the later mouse-release dispatch interval.
        self.report['animation_frames']=[frame for frame in self.report['animation_frames']
                                         if frame['file']!='expand-0.png']
        self.report['animation_frame_count']=5
        self.report['transition_sequences']=[]
        for direction,origin in [('collapse',100),('expand',500)]:
            group=[frame for frame in self.report['animation_frames'] if frame['direction']==direction]
            for frame in group:
                frame['request_after_sampling_start_ms']=frame.pop('request_after_release_ms')
                frame['ready_after_sampling_start_ms']=frame.pop('ready_after_release_ms')
                frame.pop('scheduled_offset_ms')
            finished=origin+181
            self.report['transition_sequences'].append({
                'direction':direction,'sampling_start_elapsed_ms':origin,
                'mouse_release_dispatch':{'before_elapsed_ms':origin+5,'after_elapsed_ms':origin+10},
                'sampling_interval_ms':32,'finished_elapsed_ms':finished,'frame_count':len(group),
                'png_encoding_started_elapsed_ms':finished+1,'png_encoding_finished_elapsed_ms':finished+16})
        self.write()
        result=self.create()
        text=(self.directory/'review.html').read_text(encoding='utf-8')
        players=json.loads(re.search(r'<script id="taskbar-data" type="application/json">(.*?)</script>',text,re.S).group(1))
        self.assertEqual(3,len(players['collapse']))
        self.assertEqual(2,len(players['expand']))
        self.assertEqual('sampling_start',players['collapse'][0]['clock'])
        self.assertEqual(100,players['collapse'][0]['origin_ms'])
        self.assertEqual({'before_elapsed_ms':105,'after_elapsed_ms':110},players['collapse'][0]['dispatch'])
        self.assertNotIn('scheduled_ms',players['collapse'][0])
        self.assertIn('V2 sampling-origin clock',text)
        self.assertEqual(gallery.FORMAT_V2,result['review_format'])
        self.assertEqual('recorded ready_after_sampling_start_ms differences',result['playback_clock'])
        self.assertTrue(all(base64.b64decode(value)==self.image for value in
                            re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)',text)))
        (self.directory/'review.html').unlink()
        base=copy.deepcopy(self.report)
        self.report['transition_sequences'][0]['sampling_start_elapsed_ms']+=1
        self.write();self.refused('declared sampling origin')
        self.report=copy.deepcopy(base)
        self.report['transition_sequences'][0]['png_encoding_started_elapsed_ms']=100
        self.write();self.refused('deferred-encoding')
        self.report=copy.deepcopy(base)
        self.report['animation_frames'][1]['request_after_sampling_start_ms']+=10
        self.report['animation_frames'][1]['ready_after_sampling_start_ms']+=10
        self.write();self.refused('common sampling origin')


if __name__=='__main__':
    unittest.main()
