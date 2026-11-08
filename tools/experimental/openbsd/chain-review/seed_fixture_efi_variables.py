"""Retain QEMU-only firmware initialization; rearm only our fixture selector."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

base = Path(__file__).resolve().parent
fixture = base / "efi-chain-fixture-001"
spec = importlib.util.spec_from_file_location("formats", base.parents[2] / "system/bsd-buildroot/backends/image_formats.py")
formats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(formats)
source = fixture / "qemu-chain-003-uboot/trial.img"
baseline = fixture / "chain.img"
dest = fixture / "chain-seeded.img"
assert not dest.exists() and source.is_file() and baseline.is_file()
assert source.stat().st_size == baseline.stat().st_size == 100697600
gpt = formats.gpt(source)
root = gpt["partitions"][1]
with formats.FFS2(baseline, root["offset"], root["bytes"]) as fs:
    enabled = fs.contents(fs.lookup("boot/nextboot.conf"))
with formats.FFS2(source, root["offset"], root["bytes"]) as fs:
    inode = fs.lookup("boot/nextboot.conf")
    assert fs.contents(inode) == enabled.replace(b'nextboot_enable="YES"', b'nextboot_enable="NO" ', 1)
    assert len(enabled) == inode["size"] < fs.fsize and inode["direct"][0] > 0
    address = root["offset"] + inode["direct"][0] * fs.fsize
shutil.copyfile(source, dest)
with dest.open("r+b") as stream:
    stream.seek(address)
    assert stream.write(enabled) == len(enabled)
with formats.FFS2(dest, root["offset"], root["bytes"]) as fs:
    assert fs.contents(fs.lookup("boot/nextboot.conf")) == enabled
fatpart = gpt["partitions"][0]
with formats.FAT32(dest, fatpart["offset"], fatpart["bytes"]) as fs:
    varsha = fs.sha256("ubootefi.var")
report = {"purpose": "QEMU-only firmware EFI-variable first-initialization baseline",
          "source": str(source), "output": str(dest), "only_private_ufs_nextboot_rearmed": True,
          "seeded_ubootefi_var_sha256": varsha, "physical_devices_accessed": False,
          "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
(fixture / "efi-variable-seed.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
