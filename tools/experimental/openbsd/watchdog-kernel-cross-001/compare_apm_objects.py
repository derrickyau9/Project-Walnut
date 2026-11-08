"""Compare all five separately native-built objects under completed config."""
import hashlib
import tarfile

import build_full_kernel as build
from compare_objects import compare

archive=build.REF/'native-apm-selected-objects.tgz'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='474e3e8667565b8f1a29e71e8cf87c5265e106dc61825a49789b5910469d0589'
names=('bcm2835_dog','kern_watchdog','apm','acpiapm','qcpas')
dest=build.WORK/'native-apm-selected'
dest.mkdir(exist_ok=True)
with tarfile.open(archive) as tar:
    assert sorted(tar.getnames())==sorted(name+'.o' for name in names)
    for entry in tar:
        assert entry.isfile()
        (dest/entry.name).write_bytes(tar.extractfile(entry).read())
compare(names,dest,build.OBJ,build.OUT/'apm-five-object-code-comparison.json')
