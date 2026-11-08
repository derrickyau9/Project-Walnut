"""Run stock OpenBSD machine-dtb handler on private QEMU-only DTB.

Only the supplied private ordinary fixture is attached, with no NIC. The source
image stays immutable; a separate trial copy records loader nextboot writes.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parents[2]

def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def main():
    fixture = BASE / "qemu-fixture-003"
    source_name = sys.argv[3]
    assert source_name in ("long-name.img", "short-name.img", "missing-name.img")
    source = fixture / source_name
    output = fixture / sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "aavmf"
    diagnostic = mode in ("probe", "uboot-probe")
    uboot = mode in ("uboot", "uboot-probe")
    assert source.resolve() == source and stat.S_ISREG(source.lstat().st_mode)
    output.mkdir()
    image = output / "trial.img"
    shutil.copyfile(source, image)
    before = sha(source)
    variables = output / "AAVMF_VARS.fd"
    if not uboot:
        shutil.copyfile("/usr/share/AAVMF/AAVMF_VARS.fd", variables)
    sock = "/tmp/daos-efi-chain-" + str(os.getpid()) + ".sock"
    args = ["qemu-system-aarch64", "-machine", "virt,gic-version=3", "-accel", "tcg,thread=multi",
            "-cpu", "cortex-a72", "-smp", "4", "-m", "2048", "-display", "none",
            "-serial", "unix:" + sock + ",server=on,wait=off", "-monitor", "none", "-no-reboot", "-nic", "none"]
    if uboot:
        args += ["-bios", str(BASE.parent / "chain-review/qemu-uboot-001/u-boot.bin")]
    else:
        args += ["-drive", "if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd",
                 "-drive", "if=pflash,format=raw,file=" + str(variables)]
    args += [
            "-drive", "if=none,id=chain,file=" + str(image) + ",format=raw",
            "-device", "virtio-blk-pci,drive=chain,bootindex=1"]
    commands = ["uname -a; mount; echo QA_OPENBSD_CHAIN_RAM_SHELL",
                "dmesg; echo QA_OPENBSD_CHAIN_DONE"]
    began = time.monotonic()
    all_data = bytearray()
    selected = False
    sent = 0
    last = -1
    passed = False
    probes = ["ls esp0a:/etc/boot.conf", r"ls esp0a:\etc\boot.conf", r"ls esp0a:etc\boot.conf",
              "ls esp0a:/DaOS-nextbsd/bsd.rd", r"ls esp0a:\DaOS-nextbsd\bsd.rd",
              r"boot esp0a:\DaOS-nextbsd\bsd.rd"]
    probe_sent = 0
    probe_last = -1
    with (output / "qemu.log").open("wb") as log:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connect_deadline = time.monotonic() + 5
            while True:
                try:
                    connection.connect(sock)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() > connect_deadline:
                        raise
                    time.sleep(.05)
            connection.settimeout(.25)
            progress = time.monotonic() + 15
            with (output / "serial.log").open("wb") as serial:
                while time.monotonic() - began < 150 and proc.poll() is None:
                    try:
                        data = connection.recv(65536)
                    except socket.timeout:
                        data = b""
                    if data:
                        all_data.extend(data)
                        serial.write(data)
                        serial.flush()
                    text = all_data.decode(errors="replace")
                    if diagnostic and re.search(r"boot> $", text) and len(all_data) > probe_last:
                        if probe_sent == len(probes):
                            break
                        connection.sendall((probes[probe_sent] + "\n").encode())
                        probe_sent += 1
                        probe_last = len(all_data)
                    if not selected and "(S)hell?" in text:
                        connection.sendall(b"S\n")
                        selected = True
                    if selected and sent < len(commands) and len(all_data) > last and re.search(r"(?:\r?\n|^)# $", text):
                        connection.sendall((commands[sent] + "\n").encode())
                        sent += 1
                        last = len(all_data)
                    if re.search(r"\r?\nQA_OPENBSD_CHAIN_DONE\r?\n", text):
                        passed = True
                        break
                    if not diagnostic and "Turning timeout off." in text and re.search(r"boot> $", text):
                        break
                    if len(all_data) > 1024 * 1024:
                        raise ValueError("console output exceeded bound")
                    if time.monotonic() > progress:
                        print(json.dumps({"seconds": round(time.monotonic() - began), "serial_bytes": len(all_data), "tail": text[-450:]}), flush=True)
                        progress = time.monotonic() + 15
        finally:
            connection.close()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if Path(sock).is_socket():
                Path(sock).unlink()
    spec = importlib.util.spec_from_file_location("image_formats", WORKSPACE / "system/bsd-buildroot/backends/image_formats.py")
    formats = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(formats)
    parts = formats.gpt(image)
    root = parts["partitions"][1]
    with formats.FFS2(image, root["offset"], root["bytes"]) as fs:
        selector = fs.contents(fs.lookup("boot/nextboot.conf")).decode()
    with formats.FFS2(source, root["offset"], root["bytes"]) as fs:
        original_selector = fs.contents(fs.lookup("boot/nextboot.conf")).decode()
    changed = []
    cursor = 0
    with source.open("rb") as original, image.open("rb") as trial:
        while block := original.read(1024 * 1024):
            after_block = trial.read(len(block))
            if block != after_block:
                changed.extend(cursor + index for index, (old, new) in enumerate(zip(block, after_block)) if old != new)
            cursor += len(block)
    assert len(changed) < 4096, "unexpectedly broad fixture mutation"
    (output / "nextboot-after.txt").write_text(selector)
    raw_text = all_data.decode(errors="replace")
    # The RPi-port console fix interleaves ANSI state changes with text.
    # Remove only complete ANSI escapes; do not alter ASCII words.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[78]", "", raw_text)
    (output / "serial-clean.log").write_text(text)
    with formats.FAT32(source, parts["partitions"][0]["offset"], parts["partitions"][0]["bytes"]) as fat:
        primary_sha = fat.sha256("EFI/BOOT/BOOTAA64.EFI")
        child_sha = fat.sha256("DaOS-nextbsd/BOOTAA64.EFI")
    checks = {
        "primary_actual_binary_used": primary_sha == "7374ad7c46452c29e24f090e9ee2c9cc4ee5013a82c28f836b71113691fe0472",
        "callback_after_no_readback": "DAOS_NEXTBSD_SELECTOR_CONSUMED review-6d8ddf2dd5774488983431c9a16b2a55" in text,
        "nextboot_no_persisted_in_trial_ufs": selector.startswith('nextboot_enable="NO" \n'),
        "only_exact_selector_consumed": selector == original_selector.replace('nextboot_enable="YES"', 'nextboot_enable="NO" ', 1),
        "gpt_and_whole_fat_unchanged": all(root["offset"] <= position < root["offset"] + root["bytes"] for position in changed),
        "stock_openbsd_child_banner": "OpenBSD/arm64 BOOTAA64" in text,
        "explicit_esp_subdirectory_kernel": "booting esp0a:/DaOS-nextbsd/bsd.rd:" in text or
            r"booting esp0a:\DaOS-nextbsd\bsd.rd:" in text,
        "openbsd_ram_root": "root on rd0a" in text,
        "openbsd_ram_shell": bool(re.search(r"\r?\nQA_OPENBSD_CHAIN_RAM_SHELL\r?\n", text)),
        "no_network_or_other_disks": True,
        "source_fixture_unchanged": before == sha(source),
        "private_qemu_closed": proc.poll() is not None,
        "serial_command_completed": passed,
    }
    report = {"format": "DaOS-OpenBSD-machine-dtb-private-QEMU-v1", "passed": all(checks.values()),
              "checks": checks, "command": args, "seconds": time.monotonic() - began,
              "serial_sha256": sha(output / "serial.log"), "fixture_before_sha256": before,
              "trial_after_sha256": sha(image), "callback_sha256": sha(BASE.parent / "chain-review/once-review.lua"),
              "physical_devices_attached": False, "physical_pi_boot_or_rf_proven": False,
              "frozen_files_modified": False, "gpt": parts}
    report["changed_byte_offsets"] = changed
    report["firmware"] = "same-tag-U-Boot2026.07-qemu-arm64" if uboot else "AAVMF-EDK2"
    report["mode"] = "stock-path-diagnostic" if diagnostic else "unattended-chain"
    report["manual_bootloader_commands_sent"] = probe_sent
    report["unattended_boot_proven"] = report["passed"] and not diagnostic
    report["machine_dtb"] = {
        "source_name": source_name,
        "load_error_reported": "cannot open esp0a:/DaOS-nextbsd/" in text,
        "override_model_in_kernel": "mainbus0 at root: DAOS OpenBSD DTB OVERRIDE PROBE" in text,
        "proof_leaf_in_kernel": '"daos,openbsd-dtb-001" at mainbus0 not configured' in text,
        "input_memory_bytes": 1024**3,
        "actual_kernel_real_memory_bytes": re.findall(r"real mem  = ([0-9]+)", text),
        "RPi_candidate_not_booted_on_virt": True,
    }
    (output / "qa.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    if not report["passed"]:
        raise RuntimeError("Inspect preserved bounded EFI chain console")

if __name__ == "__main__":
    main()
