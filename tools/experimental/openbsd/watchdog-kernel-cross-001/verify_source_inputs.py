"""Bind every compiler dependency to signed sys archive or exported config."""
import hashlib
import json
from pathlib import Path
import shlex
import tarfile

import build_full_kernel as build

OUT=build.OUT
ARCHIVE=OUT.parent/'chain-review/openbsd-8.0-source/sys.tar.gz'
EXPECTED='964b1aed7839b86f4e010a0fdd03dcd409e6f569e69fe85f95e5fceb740c2aaf'


def main():
    assert build.sha(ARCHIVE)==EXPECTED
    paths=set()
    for dep in build.OBJ.glob('*.d'):
        first=dep.read_text().replace('\\\n',' ').splitlines()[0]
        paths.update(Path(name).resolve() for name in shlex.split(first.split(':',1)[1]))
    sysroot=build.SYS.resolve()
    source={path.relative_to(sysroot).as_posix():build.sha(path)
            for path in paths if path.is_relative_to(sysroot)}
    verified={}
    with tarfile.open(ARCHIVE,'r|gz') as tar:
        for entry in tar:
            name=entry.name.removeprefix('./').removeprefix('sys/')
            if name not in source:continue
            assert entry.isfile()
            actual=hashlib.sha256(tar.extractfile(entry).read()).hexdigest()
            assert actual==source[name],name
            verified[name]=actual
    assert set(verified)==set(source)
    generated={}
    for path in paths:
        if path.is_relative_to(sysroot):continue
        assert path.parent==build.OBJ,path
        original=build.SNAP/path.name
        if original.is_file():assert build.sha(path)==build.sha(original),path
        else:assert path.name in ('vers.c','gapdummy.c'),path
        generated[path.name]=build.sha(path)
    result={'passed':True,'signed_archive_sha256':EXPECTED,
            'signed_archive_dependency_files':len(verified),
            'exported_generated_files':len(generated),
            'source_file_hashes':verified,'generated_file_hashes':generated,
            'no_source_changes':True,'no_physical_or_private_payload_access':True}
    (OUT/'source-input-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if not k.endswith('_hashes')},indent=2))


if __name__=='__main__':main()
