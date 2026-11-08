"""Bounded pre-demosaic RGGB code-value statistics from an immutable DNG copy."""
from array import array
from pathlib import Path
import sys

def raw_histogram(path,*,cancel=None,progress=None):
    from camera.dng.inspect import inspect_dng
    hist=[[0]*256 for _ in range(4)];combined=[0]*256;clipped=0;black=0;row=0
    with Path(path).open('rb') as stream:
        info=inspect_dng(stream);width=info['width'];height=info['height']
        stream.seek(0);little=stream.read(2)==b'II';white=info['white_level'];floor=info['black_level']
        scale=255/white
        for offset,length in zip(info['strip_offsets'],info['strip_byte_counts']):
            stream.seek(offset);remaining=length
            while remaining:
                if cancel is not None and cancel.is_set():raise InterruptedError('RAW analysis cancelled')
                size=min(remaining,width*2*16);data=stream.read(size)
                if len(data)!=size or size%(width*2):raise ValueError('Incomplete RAW strip')
                values=array('H');values.frombytes(data)
                if little!=(sys.byteorder=='little'):values.byteswap()
                for start in range(0,len(values),width):
                    row_base=(row&1)*2
                    for x in range(width):
                        value=values[start+x];bucket=min(255,int(value*scale));hist[row_base+(x&1)][bucket]+=1;combined[bucket]+=1
                        clipped+=value>=white;black+=value<=floor
                    row+=1
                remaining-=size
                if progress:progress({'stage':'raw-histogram','progress':row/height})
        if row!=height:raise ValueError('RAW rows do not match metadata')
    return {'scope':'RAW code values before black subtraction, WB, demosaic, edits and display curve',
            'channels':['R','G1','G2','B'],'histogram':hist,'combined':combined,
            'pixel_count':width*height,'black_level':floor,'white_level':white,
            'code_clipped_fraction':clipped/(width*height),'at_or_below_black_fraction':black/(width*height)}
