"""Build isolated same-tag U-Boot for a QEMU virt EFI-filesystem fixture."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

base = Path(__file__).resolve().parent
original = Path("/home/dy09/hf-task-freebsd-r10/uboot-selector-003")
source = original / "u-boot-2026.07"
archive = original / "inputs/u-boot-2026.07.tar.bz2"
scratch = Path("/home/dy09/daos-openbsd-chain-uboot-001")
out = base / "qemu-uboot-001"
out.mkdir()
scratch.mkdir()
digest = lambda data: hashlib.sha256(data).hexdigest()
assert digest(archive.read_bytes()) == "78e8bfc382fe388f9b55aa1daf8c563522a037779b5d4c349d1415e381f1243e"
efi_source = source / "lib/efi_loader/efi_file.c"
with tarfile.open(archive) as tar:
    exact = tar.extractfile("u-boot-2026.07/lib/efi_loader/efi_file.c").read()
assert efi_source.read_bytes() == exact
(out / "efi_file.c").write_bytes(exact)
commands = []
def run(args, limit=240):
    p = subprocess.run(args, cwd=source, capture_output=True, timeout=limit)
    commands.append({"args": list(map(str, args)), "returncode": p.returncode,
                     "stdout": p.stdout.decode(errors="replace"), "stderr": p.stderr.decode(errors="replace")})
    (out / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    if p.returncode:
        raise RuntimeError(p.stderr[-3000:].decode(errors="replace"))
make = ["make", "O=" + str(scratch), "ARCH=arm", "CROSS_COMPILE=aarch64-linux-gnu-"]
run(make + ["qemu_arm64_defconfig"])
configuration = scratch / ".config"
before = configuration.read_text()
cmd = 'virtio scan; if load virtio 0:1 ${kernel_addr_r} /EFI/BOOT/BOOTAA64.EFI; then bootefi ${kernel_addr_r} ${fdtcontroladdr}; fi; echo DAOS_QEMU_CHAIN_FAILED; while true; do sleep 60; done'
lines = [line for line in before.splitlines() if not line.startswith(("CONFIG_BOOTCOMMAND=", "CONFIG_BOOTDELAY="))]
lines.extend(['CONFIG_BOOTCOMMAND="' + cmd + '"', 'CONFIG_BOOTDELAY=-2'])
configuration.write_text("\n".join(lines) + "\n")
run(make + ["olddefconfig"])
run(make + ["CONFIG_TOOLS_MKEFICAPSULE=n", "-j4", "u-boot.bin"])
shutil.copyfile(scratch / "u-boot.bin", out / "u-boot.bin")
shutil.copyfile(configuration, out / "config")
assert efi_source.read_bytes() == exact
result = {"source_version": "2026.07", "archive_sha256": digest(archive.read_bytes()),
          "efi_file_c_sha256": digest(exact), "efi_file_c_matches_actual_rpi_source": True,
          "board_differs_from_rpi": "qemu_arm64_defconfig", "bootcommand": cmd,
          "binary_sha256": digest((out / "u-boot.bin").read_bytes()),
          "config_sha256": digest(configuration.read_bytes()),
          "original_source_efi_file_unchanged": True, "production_binaries_modified": False,
          "host_only_mkeficapsule_disabled": True}
(out / "build.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result), flush=True)
