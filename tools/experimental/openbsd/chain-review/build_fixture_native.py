"""Construct only new ordinary fixture images inside the isolated builder."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import re

base = Path(sys.argv[1]).resolve(strict=True)
assert base.name.startswith("daos-openbsd-chain-") and base.parent == Path("/tmp")
archive = base / "trees.tar.gz"
assert hashlib.sha256(archive.read_bytes()).hexdigest() == sys.argv[2]
with tarfile.open(archive) as tar:
    for member in tar:
        parts = Path(member.name).parts
        assert parts and parts[0] in ("root", "fat") and ".." not in parts
        dst = base / member.name
        if member.isdir():
            dst.mkdir(exist_ok=True, parents=True)
        else:
            assert member.isfile()
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(tar.extractfile(member).read())
            dst.chmod(0o644)
commands = []
def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    commands.append({"args": list(map(str, args)), "returncode": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode:
        (base / "build-commands.json").write_text(json.dumps(commands, indent=2))
        raise RuntimeError(result.stderr + result.stdout)
run(["/usr/sbin/makefs", "-t", "msdos", "-s", "64m", "-o", "fat_type=32,sectors_per_cluster=1,volume_label=DAOSCHAIN", str(base / "efi.fat"), str(base / "fat")])
def check_private_image(tool, image):
    # This FreeBSD-current fsck_msdosfs crashes when passed an ordinary file.
    # Use its supported own read-only vnode device, never a physical provider.
    device = subprocess.check_output(["/sbin/mdconfig", "-a", "-t", "vnode", "-o", "readonly", "-f", str(image)], text=True).strip()
    assert re.fullmatch(r"md[0-9]+", device)
    try:
        run([tool, "-n", "/dev/" + device])
    finally:
        subprocess.run(["/sbin/mdconfig", "-d", "-u", device[2:]], check=True, timeout=10)
check_private_image("/sbin/fsck_msdosfs", base / "efi.fat")
run(["/usr/sbin/makefs", "-t", "ffs", "-B", "little", "-s", "32m", "-o", "version=2,label=DAOSCHAIN", str(base / "root.ufs"), str(base / "root")])
check_private_image("/sbin/fsck_ufs", base / "root.ufs")
run(["/usr/bin/mkimg", "-s", "gpt", "-p", "efi/DAOSCHAIN-efi:=" + str(base / "efi.fat"), "-p", "freebsd-ufs/DAOSCHAIN-root:=" + str(base / "root.ufs"), "-o", str(base / "chain.img")])
(base / "build-commands.json").write_text(json.dumps(commands, indent=2) + "\n")
print(json.dumps({"bytes": (base / "chain.img").stat().st_size,
                  "sha256": hashlib.sha256((base / "chain.img").read_bytes()).hexdigest(),
                  "physical_devices_used": False, "mounts_created": False}))
