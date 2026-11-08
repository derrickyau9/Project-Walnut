#!/usr/bin/env python3
"""Arithmetic lower bounds from Sony public active-pixel mode dimensions.
Not a transport baud-rate prediction, memory benchmark or compression model.
"""
from pathlib import Path
import json
MODES=[('mode0_full14',6064,4040,14,19.20),('mode2_full12',6064,4040,12,40.58),('mode5_crop12',4160,2160,12,97.57)]
def budget(name,w,h,bits,fps):
 pixels=w*h;packed=pixels*bits/8;container=pixels*2
 return dict(mode=name,active_width=w,active_height=h,bits=bits,fps=fps,
             packed_frame_bytes=int(packed),container16_frame_bytes=container,
             active_payload_bits_per_second=pixels*bits*fps,
             packed_active_MB_per_second=packed*fps/1e6,
             container16_MB_per_second=container*fps/1e6,
             four_frame_buffer16_bytes=4*container,
             one_write_one_read_container16_GB_per_second=2*container*fps/1e9,
             caveat='Active pixels only; excludes blanking, embedded data, protocol/encoding overhead, memory inefficiency and additional ISP passes.')
if __name__=='__main__':
 out={'assumptions':{'MB':1000000,'GB':1000000000,'buffer_frames':4,'storage_compression':'none assumed','source':'https://www.sony-semicon.com/files/62/pdf/p-13_IMX410CQK_Flyer.pdf'},'modes':[budget(*m) for m in MODES]}
 p=Path(__file__).with_name('throughput_budget.json');p.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
