"""Prepare only a new private fixture tree from a pinned ordinary UFS image."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import tarfile

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parents[2]
SOURCE = WORKSPACE / "build/DaOS_USB_Recovery_001/root-reconstruction-v3/DaOS-root-recovery-v3-requires-FATv3.ufs"
RAW_SHA = "fbb089e165d1d16379733e60c33bb5ddf3da074dc7dd6b342e6fd85cec830c95"
OUT = BASE / "efi-chain-fixture-001"

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def main():
    assert SOURCE.is_file() and SOURCE.stat().st_size == 2684354560
    assert digest(SOURCE) == RAW_SHA
    OUT.mkdir()
    exported = OUT / "source-extract"
    root = OUT / "root"
    fat = OUT / "fat"
    spec = importlib.util.spec_from_file_location("image_formats", WORKSPACE / "system/bsd-buildroot/backends/image_formats.py")
    formats = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(formats)
    entries = {}
    with formats.FFS2(SOURCE) as fs:
        def export(name):
            inode = fs.lookup(name, follow=False)
            mode = inode["mode"] & 0xf000
            dst = exported / name
            if mode == stat.S_IFDIR:
                dst.mkdir(parents=True, exist_ok=True)
                for child in sorted(fs.entries(inode["number"])):
                    if child not in (".", ".."):
                        export(name + "/" + child)
            elif mode == stat.S_IFREG:
                data = fs.contents(inode)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
                entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "inode": inode["number"]}
            else:
                raise ValueError("Unexpected nonordinary boot input " + name)
        for name in ("boot/lua", "boot/defaults", "boot/fonts", "boot/loader.rc", "boot/loader.conf"):
            export(name)
    shutil.copytree(exported, root)
    (root / "boot/loader.conf").write_text('console="efi"\nloader_gfx="NO"\nloader_logo="none"\nloader_color="NO"\n', newline="\n")
    (root / "boot/DaOS-nextbsd").mkdir()
    shutil.copyfile(BASE / "once-review.lua", root / "boot/DaOS-nextbsd/once-review.lua")
    nonce = "review-6d8ddf2dd5774488983431c9a16b2a55"
    selector = ('nextboot_enable="YES"\nautoboot_delay="NO"\nbeastie_disable="YES"\n'
                f'daos_nextbsd_trial="{nonce}"\nexec="include /boot/DaOS-nextbsd/once-review.lua"\n')
    (root / "boot/nextboot.conf").write_text(selector, newline="\n")
    (fat / "EFI/BOOT").mkdir(parents=True)
    (fat / "DaOS-nextbsd").mkdir()
    (fat / "DaOS-recovery").mkdir()
    (fat / "etc").mkdir()
    primary = WORKSPACE / "build/DaOS_USB_Recovery_001/bootchain-diagnosis/EFI_BOOT_BOOTAA64.EFI"
    child = BASE.parents[0] / "official/BOOTAA64.EFI"
    rd = BASE.parents[0] / "official/bsd.rd"
    assert digest(primary) == "7374ad7c46452c29e24f090e9ee2c9cc4ee5013a82c28f836b71113691fe0472"
    assert digest(child) == "620f98bfb82f5c4916ddfde0abfa48dc95b5cf108243e22659bcc1a91ca38d0c"
    assert digest(rd) == "c22318dfd4ea2aa93227c00aff939486dcec9e230ff0bdb02adfcf97a8566efc"
    shutil.copyfile(primary, fat / "EFI/BOOT/BOOTAA64.EFI")
    shutil.copyfile(child, fat / "DaOS-nextbsd/BOOTAA64.EFI")
    shutil.copyfile(rd, fat / "DaOS-nextbsd/bsd.rd")
    (fat / "DaOS-recovery/normal.running").write_bytes(b"\1")
    # Serial first: prove the actual EFI chain independently of framebuffer.
    (fat / "etc/boot.conf").write_text('set tty com0\nboot esp0a:/DaOS-nextbsd/bsd.rd\n', newline="\n")
    with tarfile.open(OUT / "trees.tar.gz", "w:gz") as archive:
        archive.add(root, arcname="root")
        archive.add(fat, arcname="fat")
    report = {"source_raw": str(SOURCE), "source_raw_sha256": RAW_SHA, "source_unchanged": digest(SOURCE) == RAW_SHA,
              "exported_files": entries, "primary_sha256": digest(primary), "child_sha256": digest(child),
              "bsd_rd_sha256": digest(rd), "trees_sha256": digest(OUT / "trees.tar.gz"),
              "candidate_sha256": digest(BASE / "once-review.lua"), "physical_device_access": False,
              "fixture_only_configuration_overrides": ["boot/loader.conf serial console", "new nextboot and Lua", "new FAT boot.conf com0"]}
    (OUT / "inputs.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"directory": str(OUT), "exported_files": len(entries), "trees_bytes": (OUT / "trees.tar.gz").stat().st_size}))

if __name__ == "__main__":
    main()
