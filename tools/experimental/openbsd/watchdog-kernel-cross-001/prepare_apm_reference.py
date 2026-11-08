"""Extract only the immutable, source-bound native config snapshot."""
import hashlib
import json
from pathlib import Path
import tarfile

OUT=Path(__file__).resolve().parent
REF=OUT.parent/'watchdog-kernel-002-arm64'
DEST=Path('/home/dy09/daos-openbsd-cross-001/native-apm-reference')
SYS=Path('/home/dy09/daos-openbsd-watchdog-001/sys')
archive=REF/'native-apm-cross-reference.tgz'
expected='8a440d9041255a45c519781e669f49540dfbcd41841f3c12706f372547c4149a'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
DEST.mkdir(exist_ok=True)
hashes={}
with tarfile.open(archive) as tar:
    for entry in tar:
        assert '/' not in entry.name and entry.name not in ('.','..')
        if entry.issym():
            assert entry.name in ('machine','arm64')
            continue
        assert entry.isfile()
        data=tar.extractfile(entry).read()
        (DEST/entry.name).write_bytes(data)
        hashes[entry.name]=hashlib.sha256(data).hexdigest()
for name in ('machine','arm64'):
    link=DEST/name
    if link.is_symlink():link.unlink()
    assert not link.exists()
    link.symlink_to(SYS/'arch/arm64/include')
assert (DEST/'apm.h').read_text().strip()=='#define\tNAPM\t1'
config=(DEST/'DAOS_WDOG_APM_RAMDISK').read_text()
assert config.splitlines()==['include "arch/arm64/conf/RAMDISK"','rmoption SMALL_KERNEL','apm0 at mainbus?']
result={'passed':True,'archive_sha256':expected,'source_config':config,'files':hashes,
        'symlink_rebases_only':{'machine':str(SYS/'arch/arm64/include'),'arm64':'machine'},
        'no_native_or_source_tree_writes':True}
(OUT/'apm-reference-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({key:result[key] for key in ('passed','archive_sha256','source_config')}))
