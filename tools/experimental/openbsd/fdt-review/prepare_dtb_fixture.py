"""Create private same-chain images to test stock machine-dtb handler."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from inspect_runtime import parse
from build_candidate import serialize, PROOF

base = Path(__file__).resolve().parent
chain = base.parent / "chain-review"
source = chain / "efi-chain-fixture-001/chain-seeded.img"
firmware = chain / "qemu-uboot-001/u-boot.bin"
out = base / "qemu-fixture-003"
out.mkdir()
assert source.stat().st_size == 100697600
assert hashlib.sha256(source.read_bytes()).hexdigest() == "5070c4fe13c22375776115996836bb884781bcf33678d024f8ec1a5d5c711abc"
dump = out / "qemu-virt-original.dtb"
args = ["qemu-system-aarch64", "-machine", "virt,gic-version=3,dumpdtb=" + str(dump), "-cpu", "cortex-a72",
        "-smp", "4", "-m", "2048", "-display", "none", "-nic", "none", "-bios", str(firmware),
        "-drive", "if=none,id=chain,file=" + str(source) + ",format=raw,readonly=on",
        "-device", "virtio-blk-pci,drive=chain"]
p = subprocess.run(args, capture_output=True, timeout=20)
assert p.returncode == 0, p.stderr
tree = parse(dump.read_bytes())
tree["nodes"]["/"]["model"] = b"DAOS OpenBSD DTB OVERRIDE PROBE\0"
tree["nodes"]["/" + PROOF] = {"compatible": PROOF.encode() + b"\0", "status": b"okay\0"}
memory = [name for name, props in tree["nodes"].items() if props.get("device_type") == b"memory\0"]
assert len(memory) == 1
original_memory = tree["nodes"][memory[0]]["reg"]
assert original_memory.hex() == "00000000400000000000000080000000"
# Deliberately stale 1GiB input. EFI_DT_FIXUP_PROTOCOL must refresh actual2GiB.
tree["nodes"][memory[0]]["reg"] = bytes.fromhex("00000000400000000000000040000000")
dtb = serialize(tree)
(out / "probe.dtb").write_bytes(dtb)
mcopy = "/home/dy09/hf-openbsd-host-tools-001/root/usr/bin/mcopy"
fat = out / "fresh-efi.fat"
with fat.open("xb") as stream:
    stream.truncate(64 * 1024**2)
subprocess.run(["/usr/sbin/mkfs.vfat", "-F", "32", "-s", "1", "-n", "DAOSFDT", str(fat)], check=True, capture_output=True)
# Native makefs' small FAT BPB is accepted by firmware but mtools rejects its
# boundary cluster count. Use a newly formatted Linux FAT for this new fixture,
# with identical reviewed payload bytes; never skip mtools consistency checks.
fat_tree = chain / "efi-chain-fixture-001/fat"
for name in ("EFI", "DaOS-nextbsd", "DaOS-recovery", "etc"):
    subprocess.run([mcopy, "-s", "-i", str(fat), str(fat_tree / name), "::/"], check=True, capture_output=True)
subprocess.run([mcopy, "-i", str(fat), str(chain / "qemu-uboot-001/ubootefi-seeded.var"), "::/ubootefi.var"], check=True, capture_output=True)
records = []
for case, leaf in (("long-name", "probe.dtb"), ("short-name", "d")):
    image = out / (case + ".img")
    shutil.copyfile(source, image)
    with image.open("r+b") as target, fat.open("rb") as fs:
        target.seek(17408)
        shutil.copyfileobj(fs, target)
    config = out / (case + "-boot.conf")
    config.write_text("set tty com0\nmachine dtb /DaOS-nextbsd/" + leaf + "\nboot esp0a:/DaOS-nextbsd/bsd.rd\n")
    for src, target in ((out / "probe.dtb", "/DaOS-nextbsd/" + leaf), (config, "/etc/boot.conf")):
        command = [mcopy, "-o", "-i", str(image) + "@@17408", str(src), "::" + target]
        result = subprocess.run(command, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
    records.append({"case": case, "path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest(), "dtb_leaf": leaf})
report = {"dump_command": args, "dump_stderr": p.stderr.decode(errors="replace"),
          "probe_dtb_sha256": hashlib.sha256(dtb).hexdigest(), "proof_compatible": PROOF,
          "probe_input_memory_bytes": 1024**3, "actual_guest_memory_bytes": 2 * 1024**3,
          "RPi_candidate_is_NOT_used_on_virt_machine": True, "images": records, "physical_device_access": False}
(out / "inputs.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"images": records, "dtb_sha256": report["probe_dtb_sha256"]}))
