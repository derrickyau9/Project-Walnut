"""Read-only GPT, FAT32 and NetBSD FFSv2 image inspection.

On-disk FFS layouts follow the locked NetBSD sys/ufs/{ffs/fs.h,ufs/dinode.h,
ufs/dir.h}.  These readers do not mount a filesystem or change an image.
They verify bounds and file contents; they do not replace a target fsck.
"""
from __future__ import annotations
import hashlib
from pathlib import Path, PurePosixPath
import struct
import uuid
import zlib


class FormatError(ValueError):pass


def _u32(data,offset):return struct.unpack_from('<I',data,offset)[0]
def _u64(data,offset):return struct.unpack_from('<Q',data,offset)[0]


def gpt(path):
    """Validate both GPT headers/tables and return bounded partition records."""
    path=Path(path);size=path.stat().st_size
    if size%512 or size<1024*1024:raise FormatError('Sector-aligned GPT image required')
    sectors=size//512
    with path.open('rb') as stream:
        def read(offset,count):
            if offset<0 or count<0 or offset+count>size:raise FormatError('GPT read outside image')
            stream.seek(offset);data=stream.read(count)
            if len(data)!=count:raise FormatError('Short GPT read')
            return data
        if read(510,2)!=b'\x55\xaa':raise FormatError('Missing MBR signature')
        def header(lba):
            data=read(lba*512,512)
            if data[:8]!=b'EFI PART':raise FormatError('Missing GPT header')
            length=_u32(data,12)
            if not 92<=length<=512:raise FormatError('Invalid GPT header length')
            check=bytearray(data[:length]);expected=_u32(check,16);check[16:20]=b'\0'*4
            if zlib.crc32(check)&0xffffffff!=expected:raise FormatError('GPT header CRC mismatch')
            current,backup,first,last=struct.unpack_from('<QQQQ',data,24)
            table_lba=_u64(data,72);count,entry_size,crc=struct.unpack_from('<III',data,80)
            if current!=lba or backup>=sectors or not 1<count<=4096 or not 128<=entry_size<=4096:
                raise FormatError('Invalid GPT geometry')
            if not 2<=first<=last<sectors-1:raise FormatError('Invalid GPT usable range')
            table=read(table_lba*512,count*entry_size)
            if zlib.crc32(table)&0xffffffff!=crc:raise FormatError('GPT entry CRC mismatch')
            return {'current':current,'backup':backup,'first':first,'last':last,'disk_guid':data[56:72],
                    'count':count,'entry_size':entry_size,'table':table}
        primary=header(1);secondary=header(sectors-1)
        if (primary['backup']!=sectors-1 or secondary['backup']!=1
                or primary['disk_guid']!=secondary['disk_guid'] or primary['table']!=secondary['table']):
            raise FormatError('Primary and secondary GPT disagree')
        result=[]
        for index in range(primary['count']):
            entry=primary['table'][index*primary['entry_size']:(index+1)*primary['entry_size']]
            if entry[:16]==b'\0'*16:continue
            begin,end,attributes=struct.unpack_from('<QQQ',entry,32)
            if not primary['first']<=begin<=end<=primary['last']:raise FormatError('GPT partition outside usable range')
            label=entry[56:128].decode('utf-16-le').split('\0',1)[0]
            if not label or any(p['label']==label for p in result):raise FormatError('Empty or duplicate GPT label')
            result.append({'index':index+1,'label':label,'type':str(uuid.UUID(bytes_le=entry[:16])),
                           'guid':str(uuid.UUID(bytes_le=entry[16:32])),'start_sector':begin,
                           'sectors':end-begin+1,'offset':begin*512,'bytes':(end-begin+1)*512,
                           'attributes':attributes})
        ordered=sorted(result,key=lambda p:p['offset'])
        for left,right in zip(ordered,ordered[1:]):
            if left['offset']+left['bytes']>right['offset']:raise FormatError('Overlapping GPT partitions')
        return {'bytes':size,'sector_size':512,'both_gpt_crcs_valid':True,'partitions':result}


class Region:
    def __init__(self,path,offset=0,size=None):
        self.path=Path(path);self.offset=offset
        self.size=self.path.stat().st_size-offset if size is None else size
        if offset<0 or self.size<=0 or offset+self.size>self.path.stat().st_size:raise FormatError('Invalid image region')
        self.stream=self.path.open('rb')
    def read(self,offset,count):
        if offset<0 or count<0 or offset+count>self.size:raise FormatError('Filesystem read outside partition')
        self.stream.seek(self.offset+offset);data=self.stream.read(count)
        if len(data)!=count:raise FormatError('Short filesystem read')
        return data
    def close(self):self.stream.close()
    def __del__(self):
        stream=getattr(self,'stream',None)
        if stream is not None:stream.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class FFS2(Region):
    """Bounded NetBSD FFSv2 reader, including all three indirect block levels."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        # NetBSD makefs/ffs/mkfs.c intentionally writes small FFSv2 images
        # at SBLOCK_UFS1 (8192); newfs may use SBLOCK_UFS2 (65536).
        sb=None
        for offset in (65536,8192,0,262144):
            if offset+1536>self.size:continue
            candidate=self.read(offset,1536)
            if _u32(candidate,1372) in (0x19540119,0x19012038):
                sb=candidate;self.superblock_offset=offset;self.magic=_u32(candidate,1372);break
        if sb is None:raise FormatError('Expected little-endian NetBSD FFSv2 superblock')
        self.iblkno=_u32(sb,16);self.ncg=_u32(sb,44)
        self.bsize=_u32(sb,48);self.fsize=_u32(sb,52);self.frag=_u32(sb,56)
        self.ipg=_u32(sb,184);self.fpg=_u32(sb,188)
        if (self.bsize not in (4096,8192,16384,32768,65536) or self.fsize<512
                or self.fsize&(self.fsize-1) or self.bsize//self.fsize!=self.frag
                or not self.ncg or not self.ipg or not self.fpg):raise FormatError('Invalid FFSv2 geometry')
        self.inopb=self.bsize//256;self.nindir=self.bsize//8
        if _u32(sb,120)!=self.inopb or _u32(sb,116)!=self.nindir:raise FormatError('FFSv2 inode/pointer geometry mismatch')
        self._inode_cache={};self._directory_cache={}
    def inode(self,number):
        if number in self._inode_cache:return self._inode_cache[number]
        if not 2<=number<self.ncg*self.ipg:raise FormatError('Invalid inode number')
        cg,local=divmod(number,self.ipg)
        address=(cg*self.fpg+self.iblkno+(local//self.inopb)*self.frag)*self.fsize+(number%self.inopb)*256
        data=self.read(address,256)
        inode={'number':number,'mode':struct.unpack_from('<H',data)[0],'uid':_u32(data,4),'gid':_u32(data,8),'size':_u64(data,16),
               'blocks':_u64(data,24),'direct':struct.unpack_from('<12q',data,112),
               'indirect':struct.unpack_from('<3q',data,208),'inline_link':data[112:232]}
        if inode['size']>self.size:raise FormatError('Inode content exceeds filesystem size')
        self._inode_cache[number]=inode;return inode
    def _address(self,inode,lbn):
        if lbn<12:return inode['direct'][lbn]
        lbn-=12
        for level in (1,2,3):
            capacity=self.nindir**level
            if lbn>=capacity:lbn-=capacity;continue
            block=inode['indirect'][level-1]
            for depth in range(level-1,-1,-1):
                if block==0:return 0
                index,lbn=divmod(lbn,self.nindir**depth)
                block=struct.unpack('<q',self.read(block*self.fsize+index*8,8))[0]
                if block<0:raise FormatError('Negative FFS block pointer')
            return block
        raise FormatError('Unrepresentable FFS file offset')
    def chunks(self,inode):
        if inode['mode']&0xf000==0xa000 and inode['blocks']==0 and inode['size']<120:
            yield inode['inline_link'][:inode['size']];return
        remaining=inode['size'];lbn=0
        while remaining:
            count=min(self.bsize,remaining);block=self._address(inode,lbn)
            yield self.read(block*self.fsize,count) if block else b'\0'*count
            remaining-=count;lbn+=1
    def contents(self,inode,maximum=64*1024*1024):
        if inode['size']>maximum:raise FormatError('Refusing oversized directory/metadata read')
        return b''.join(self.chunks(inode))
    def entries(self,number):
        if number in self._directory_cache:return self._directory_cache[number]
        inode=self.inode(number)
        if inode['mode']&0xf000!=0x4000:raise FormatError('Path parent is not a directory')
        data=self.contents(inode);result={};cursor=0
        while cursor<len(data):
            if cursor+8>len(data):raise FormatError('Truncated FFS directory record')
            ino,length,kind,namelen=struct.unpack_from('<IHBB',data,cursor)
            if length<8 or length%4 or cursor+length>len(data) or 8+namelen>length:
                raise FormatError('Invalid FFS directory record')
            if ino:
                name=data[cursor+8:cursor+8+namelen].decode('utf-8')
                if name in result:raise FormatError('Duplicate FFS directory name')
                result[name]=ino
            cursor+=length
        self._directory_cache[number]=result;return result
    def lookup(self,name,follow=True,_depth=0):
        if _depth>32:raise FormatError('FFS symlink cycle')
        parts=PurePosixPath(name).parts
        if not parts or '..' in parts or name.startswith('/'):raise FormatError('Confined FFS path required')
        number=2
        for index,part in enumerate(parts):
            try:number=self.entries(number)[part]
            except KeyError:raise FormatError('Missing image path: '+name) from None
            inode=self.inode(number)
            if inode['mode']&0xf000==0xa000 and (follow or index<len(parts)-1):
                target=self.contents(inode).decode('utf-8')
                prefix=[] if target.startswith('/') else list(parts[:index])
                for item in PurePosixPath(target).parts:
                    if item=='/':continue
                    if item=='..':
                        if not prefix:raise FormatError('Image symlink escapes root')
                        prefix.pop()
                    elif item!='.':prefix.append(item)
                return self.lookup('/'.join(prefix+list(parts[index+1:])),follow,_depth+1)
        return self.inode(number)
    def sha256(self,name,follow=True):
        value=hashlib.sha256()
        for chunk in self.chunks(self.lookup(name,follow)):value.update(chunk)
        return value.hexdigest()


class FAT32(Region):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        bpb=self.read(0,512)
        self.bps=struct.unpack_from('<H',bpb,11)[0];self.spc=bpb[13]
        self.reserved=struct.unpack_from('<H',bpb,14)[0];self.fats=bpb[16]
        self.fat_sectors=_u32(bpb,36);self.root=_u32(bpb,44)
        self.total=_u32(bpb,32)
        if (bpb[510:512]!=b'\x55\xaa' or bpb[82:90]!=b'FAT32   '
                or self.bps!=512 or not self.spc or self.spc&(self.spc-1)
                or self.fats not in (1,2) or self.total*self.bps>self.size):raise FormatError('Invalid FAT32 BPB')
        self.cluster_size=self.bps*self.spc
        self.data_offset=(self.reserved+self.fats*self.fat_sectors)*self.bps
        self.cluster_count=(self.total*self.bps-self.data_offset)//self.cluster_size
        if not 2<=self.root<self.cluster_count+2:raise FormatError('FAT root cluster outside data')
    def chain(self,first):
        seen=set();cluster=first
        while cluster<0x0ffffff8:
            if not 2<=cluster<self.cluster_count+2 or cluster in seen:raise FormatError('Invalid or cyclic FAT chain')
            seen.add(cluster);yield cluster
            cluster=_u32(self.read(self.reserved*self.bps+cluster*4,4),0)&0x0fffffff
    def clusters(self,first):
        for cluster in self.chain(first):yield self.read(self.data_offset+(cluster-2)*self.cluster_size,self.cluster_size)
    def entries(self,first):
        names={};long=[]
        for block in self.clusters(first):
            for offset in range(0,len(block),32):
                row=block[offset:offset+32]
                if row[0]==0:return names
                if row[0]==0xe5:long=[];continue
                if row[11]==0x0f:
                    long.append((row[0]&0x1f,row[1:11]+row[14:26]+row[28:32]));continue
                short=row[:8].decode('ascii').rstrip();extension=row[8:11].decode('ascii').rstrip()
                name=short+('.'+extension if extension else '')
                if long:
                    name=b''.join(x[1] for x in sorted(long)).decode('utf-16-le').split('\0',1)[0].rstrip('\uffff')
                long=[]
                if row[11]&8:continue
                cluster=(struct.unpack_from('<H',row,20)[0]<<16)|struct.unpack_from('<H',row,26)[0]
                names[name.casefold()]={'name':name,'cluster':cluster,'size':_u32(row,28),'directory':bool(row[11]&16)}
        return names
    def sha256(self,name):
        parts=PurePosixPath(name).parts;cluster=self.root
        if not parts or '..' in parts or name.startswith('/'):raise FormatError('Confined FAT path required')
        for index,part in enumerate(parts):
            try:entry=self.entries(cluster)[part.casefold()]
            except KeyError:raise FormatError('Missing FAT path: '+name) from None
            if index<len(parts)-1 and not entry['directory']:raise FormatError('FAT parent is not a directory')
            cluster=entry['cluster']
        value=hashlib.sha256();remaining=entry['size']
        if remaining:
            for chunk in self.clusters(cluster):
                value.update(chunk[:remaining]);remaining-=min(remaining,len(chunk))
                if not remaining:break
        if remaining:raise FormatError('FAT file shorter than recorded size')
        return value.hexdigest()
