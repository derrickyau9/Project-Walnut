"""Build root-independent Pi4 rescue boot files in a fresh staging directory.

No disks, EEPROM or OTP are changed. The optional top-level GPIO selector is
returned as a separate file for a deliberately chosen installation step.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

COMMIT = '95719d8a895bf8170851e9844d9b4092b1d2b6fe'
LOADER_SOURCE_SHA = '1b791381bb1cfc50d02fbfc36427d7ac2f1ba128ae0df4659c0854bf0b7ecf8e'
MARKER = b'/* DaOS rescue: use this EFI image\'s own FAT device, never a sibling UFS. */'
PATCH_ANCHOR = b'\t/*\n\t * First choice: if rootdev is already set, use that, even if\n'
PATCH_INSERT = b'''\t/* DaOS rescue: use this EFI image's own FAT device, never a sibling UFS. */
\tif (getenv("daos_recovery") != NULL &&
\t    strcmp(getenv("daos_recovery"), "YES") == 0) {
\t\tdp = efiblk_get_pdinfo_by_handle(boot_img->DeviceHandle);
\t\tif (dp == NULL) {
\t\t\tprintf("DaOS rescue: EFI boot device is unavailable\\n");
\t\t\treturn (ENOENT);
\t\t}
\t\tset_currdev_pdinfo(dp);
\t\tprintf("DaOS rescue: pinned to EFI boot device\\n");
\t\treturn (0);
\t}
'''


def patch_loader_source(data: bytes) -> bytes:
    if hashlib.sha256(data).hexdigest() != LOADER_SOURCE_SHA:
        raise ValueError('Expected exact pinned FreeBSD EFI loader source')
    if data.count(PATCH_ANCHOR) != 1 or MARKER in data:
        raise ValueError('Unexpected EFI loader patch site')
    return data.replace(PATCH_ANCHOR, PATCH_INSERT+PATCH_ANCHOR, 1)


def loader_patch(data: bytes) -> str:
    changed = patch_loader_source(data)
    return ''.join(difflib.unified_diff(data.decode().splitlines(True),
        changed.decode().splitlines(True), fromfile='a/stand/efi/loader/main.c',
        tofile='b/stand/efi/loader/main.c'))


def recovery_lua() -> str:
    # Only builtin Lua loader methods are used: no main root lua/loader.conf,
    # no default kernel search, and no reads of main root/state partitions.
    return '''-- DaOS independent RAM-root rescue; paired with patched loader.efi.
local function checked(command)
    if loader.perform(command) ~= 0 then
        loader.setenv("autoboot_delay", "NO")
        error("DaOS rescue failed: " .. command)
    end
end
if loader.getenv("daos_recovery") ~= "YES" then
    error("DaOS rescue must be selected explicitly")
end
loader.setenv("autoboot_delay", "NO")
loader.setenv("vfs.root.mountfrom", "ufs:/dev/md0")
loader.setenv("vfs.root.mountfrom.options", "rw")
loader.setenv("init_script", "/usr/local/libexec/DaOS-recovery/early-init.sh")
loader.setenv("module_path", "/boot/kernel")
loader.setenv("hw.usb.template", "-1")
loader.setenv("kern.geom.label.disk_ident.enable", "0")
checked("load /DaOS-recovery/kernel/kernel")
checked("load /DaOS-recovery/kernel/usb_template.ko")
checked("load /DaOS-recovery/kernel/daos_adb_template.ko")
-- Omit .gz deliberately: stand gzipfs appends it and decompresses. An explicit
-- .gz request bypasses gzipfs and would create md0 from compressed bytes.
checked("load -t md_image /DaOS-recovery/root.ufs")
checked("boot")
error("DaOS rescue kernel returned")
'''


def _device(interface: str, device: str) -> None:
    if interface not in ('mmc', 'usb') or not re.fullmatch(r'[0-9]{1,2}:[1-9][0-9]?', device):
        raise ValueError('Expected explicit mmc/usb device and FAT partition')


def manual_script(interface: str = 'mmc', device: str = '0:1') -> str:
    _device(interface, device)
    return f'''# DaOS rescue only: never fall through to a possibly corrupt OS.
setenv bootargs 'daos_recovery=YES loader_lua=/DaOS-recovery/loader.lua'
if load {interface} {device} ${{kernel_addr_r}} /DaOS-recovery/loader.efi; then
    bootefi ${{kernel_addr_r}}
fi
echo DAOS_RESCUE_BOOT_FAILED
# A nonzero return would let bootflow scan try the normal EFI method.
while true; do sleep 60; done
'''


def gpio_selector(interface: str = 'mmc', device: str = '0:1', gpio: int = 21) -> str:
    _device(interface, device)
    if type(gpio) is not int or gpio != 21:
        raise ValueError('Only separately reviewed optional GPIO21 is supported')
    return f'''# Optional: requires GPIO21 input pull-up in Pi firmware config.
# Hold GPIO21 low at power-on/reset to choose rescue; no OTP changes.
if gpio input 21; then
    setenv bootargs 'daos_recovery=YES loader_lua=/DaOS-recovery/loader.lua'
    if load {interface} {device} ${{kernel_addr_r}} /DaOS-recovery/loader.efi; then
        bootefi ${{kernel_addr_r}}
    fi
    echo DAOS_RESCUE_BOOT_FAILED
    while true; do sleep 60; done
else
    # Resume the original unmodified EFI loader, without rescue load options.
    setenv bootargs
    if load {interface} {device} ${{kernel_addr_r}} /EFI/BOOT/BOOTAA64.EFI; then
        bootefi ${{kernel_addr_r}}
    fi
    echo DAOS_NORMAL_BOOT_FAILED
    exit 1
fi
'''


def armed_failover(interface: str = 'mmc', device: str = '0:1', *, gpio_override: bool = True) -> str:
    """Crash marker published before normal boot; zero cleared at clean shutdown."""
    _device(interface,device)
    if type(gpio_override) is not bool:raise ValueError('Explicit GPIO override boolean required')
    gpio_line='if gpio input 21; then setenv daos_rescue YES; fi' if gpio_override else '# GPIO override disabled: no pin/pull configuration changes.'
    return f'''# Optional automatic DaOS failover. All state is on the FAT boot partition.
# Uses 0x05200000, below scriptaddr=0x05400000 in pinned Pi4 U-Boot.
setenv daos_marker_addr 0x05200000
setenv daos_rescue NO
{gpio_line}
mw.b ${{daos_marker_addr}} 0 1
if fatload {interface} {device} ${{daos_marker_addr}} /DaOS-recovery/normal.running 2; then
    if test "${{filesize}}" = "1"; then
        if itest.b *${{daos_marker_addr}} != 0; then setenv daos_rescue YES; fi
    else
        setenv daos_rescue YES
    fi
fi
if test "${{daos_rescue}}" = "NO"; then
    mw.b ${{daos_marker_addr}} 1 1
    if fatwrite {interface} {device} ${{daos_marker_addr}} /DaOS-recovery/normal.running 1; then
        mw.b ${{daos_marker_addr}} 0 1
        if fatload {interface} {device} ${{daos_marker_addr}} /DaOS-recovery/normal.running 2; then
            if test "${{filesize}}" = "1"; then
                if itest.b *${{daos_marker_addr}} == 1; then
                    setenv bootargs
                    if load {interface} {device} ${{kernel_addr_r}} /EFI/BOOT/BOOTAA64.EFI; then
                        bootefi ${{kernel_addr_r}}
                    fi
                fi
            fi
        fi
    fi
fi
# Do not clear normal.running here: recovery remains sticky until repaired.
setenv bootargs 'daos_recovery=YES loader_lua=/DaOS-recovery/loader.lua'
if load {interface} {device} ${{kernel_addr_r}} /DaOS-recovery/loader.efi; then
    bootefi ${{kernel_addr_r}}
fi
echo DAOS_RESCUE_BOOT_FAILED
while true; do sleep 60; done
'''


CLEAN_SHUTDOWN = '''#!/bin/sh
# PROVIDE: DaOS_rescue_clean
# REQUIRE: FILESYSTEMS
# BEFORE: DAEMON
# KEYWORD: shutdown
. /etc/rc.subr
name="DaOS_rescue_clean"
start_cmd=":"
stop_cmd="DaOS_rescue_clear"
DaOS_rescue_clear() {
    # This late shutdown hook is installed only with the armed boot selector.
    # No marker clear during a failed /etc/rc -> SIGTERM -> death_single path.
    [ "$(/bin/kenv -q daos_recovery)" = YES ] && return 0
    mountpoint=/DaOS-recovery-efi
    marker="$mountpoint/DaOS-recovery/normal.running"
    # A different path or GEOM alias can already expose this FAT filesystem.
    # Resolve labels before mounting, and bind the ESP to the normal SD root.
    daos_mounts=$(/sbin/mount -p) || return 1
    daos_labels=$(/sbin/geom label status -s) || return 1
    printf '%s\\n' "$daos_mounts" | DAOS_CLEAN_LABELS="$daos_labels" /usr/bin/awk -v target="$mountpoint" '
        function resolve(path, count) {
            count = 0
            while (path in alias) {
                if (++count > 128) { bad = 1; return "" }
                path = alias[path]
            }
            return path
        }
        BEGIN {
            count = split(ENVIRON["DAOS_CLEAN_LABELS"], lines, "\\n")
            for (i = 1; i <= count; i++) {
                line = lines[i]
                sub(/^[ \\t]+/, "", line); sub(/[ \\t]+$/, "", line)
                if (line == "") continue
                if (split(line, fields, /[ \\t]+/) != 3) { bad = 1; continue }
                name = "/dev/" fields[1]
                if (name in alias) bad = 1
                alias[name] = "/dev/" fields[3]
            }
            for (name in alias) resolve(name)
            esp = resolve("/dev/gpt/DaOS-efi")
            if (esp !~ /^\\/dev\\/(mmcsd|sdda)[0-9]+p1$/) bad = 1
            disk = esp; sub(/p1$/, "", disk)
        }
        {
            if (NF != 6) { bad = 1; next }
            source = resolve($1)
            if (source == esp || source == disk || $2 == target) bad = 1
            if ($2 == "/") {
                roots++
                if (source != disk "p2" || $3 != "ufs") bad = 1
            }
        }
        END { exit (bad || roots != 1) }
    ' || return 1
    # mount_msdosfs accepts each -o argument as one option, not a comma list.
    /sbin/mount -t msdosfs -o rw /dev/gpt/DaOS-efi "$mountpoint" || return 1
    if [ -f "$marker" ] && [ ! -L "$marker" ] && [ "$(/usr/bin/wc -c < "$marker" | /usr/bin/tr -d ' ')" = 1 ]; then
        if printf '\\000' | /bin/dd of="$marker" bs=1 count=1 conv=notrunc status=none; then
            if /bin/sync && /sbin/umount "$mountpoint"; then return 0; fi
        fi
    fi
    # A failed clean-shutdown transaction must remain a recovery boot.
    if [ -f "$marker" ] && [ ! -L "$marker" ]; then
        printf '\\001' | /bin/dd of="$marker" bs=1 count=1 conv=notrunc status=none
        /bin/sync
    fi
    /sbin/umount "$mountpoint"
    return 1
}
load_rc_config "$name"
run_rc_command "$1"
'''


def script_image(script: str) -> bytes:
    """U-Boot legacy script image, equivalent to mkimage -A arm64 -T script."""
    body = script.encode('ascii')
    payload = struct.pack('>II', len(body), 0)+body
    # Linux OS=5 is mkimage's conventional script OS; code is a U-Boot script.
    header = struct.pack('>7I4B32s', 0x27051956, 0, 0, len(payload), 0, 0,
        zlib.crc32(payload), 5, 22, 6, 0, b'DaOS rescue boot selector 001')
    hcrc = zlib.crc32(header)
    return header[:4]+struct.pack('>I', hcrc)+header[8:]+payload


def read_script_image(data: bytes) -> str:
    if len(data) < 72:
        raise ValueError('Truncated U-Boot image')
    values = struct.unpack('>7I4B32s', data[:64])
    magic, hcrc, _, size, _, _, dcrc, os_, arch, type_, comp, _ = values
    header = data[:4]+b'\0'*4+data[8:64]
    if (magic != 0x27051956 or size != len(data)-64 or zlib.crc32(header) != hcrc
            or zlib.crc32(data[64:]) != dcrc or (os_, arch, type_, comp) != (5,22,6,0)):
        raise ValueError('Invalid U-Boot script header or checksum')
    length, end = struct.unpack('>II', data[64:72])
    if end != 0 or length != len(data)-72:
        raise ValueError('Invalid U-Boot script data table')
    return data[72:].decode('ascii')


def generate(output: Path, source: Path, *, interface: str = 'mmc', device: str = '0:1') -> dict:
    _device(interface, device)
    if output.exists() or output.is_symlink():
        raise ValueError('Choose a fresh boot-file staging directory')
    original = source.read_bytes()
    patch = loader_patch(original)  # Validate exact source before creating output.
    files = {'DaOS-recovery/loader.lua': recovery_lua().encode(),
        'DaOS-recovery/boot.cmd': manual_script(interface,device).encode(),
        'DaOS-recovery/boot.scr': script_image(manual_script(interface,device)),
        'optional/GPIO21-boot.cmd': gpio_selector(interface,device).encode(),
        'optional/GPIO21-boot.scr': script_image(gpio_selector(interface,device)),
        'optional/armed-failover-boot.cmd': armed_failover(interface,device).encode(),
        'optional/armed-failover-boot.scr': script_image(armed_failover(interface,device)),
        'optional/root/etc/rc.d/DaOS-rescue-clean': CLEAN_SHUTDOWN.encode(),
        'optional/config.txt.append': b'\n[all]\ngpio=21=ip,pu\n',
        'source/efi-recovery-device.patch': patch.encode(),
        'source/main.c.patched': patch_loader_source(original)}
    output.mkdir(parents=True)
    manifest = {}
    for relative, data in files.items():
        destination = output/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data)
        mode = 0o755 if relative.startswith('optional/root/etc/rc.d/') else 0o644
        destination.chmod(mode)
        manifest[relative] = {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':mode}
    receipt = {'format':'DaOS-independent-rescue-boot-config-v1','source_commit':COMMIT,
        'loader_source_sha256':LOADER_SOURCE_SHA,'files':manifest,
        'patched_efi_loader_required':True,'main_root_required':False,
        'otp_modified':False,'physical_boot_verified':False,
        'unexpected_power_loss_enters_rescue':True,
        'armed_failover_requires':['FAT normal.running marker','clean shutdown hook','hardware watchdog or manual power/reset for a hard hang','precreated root /DaOS-recovery-efi mountpoint'],
        'required_payload':['DaOS-recovery/loader.efi','DaOS-recovery/kernel/kernel',
            'DaOS-recovery/kernel/usb_template.ko','DaOS-recovery/kernel/daos_adb_template.ko',
            'DaOS-recovery/root.ufs.gz'],
        'manual_uboot_entry':f'load {interface} {device} ${{scriptaddr}} /DaOS-recovery/boot.scr; source ${{scriptaddr}}',
        'optional_selector_install':'Copy optional/GPIO21-boot.scr to FAT /boot.scr only after boot-script behavior and GPIO21 conflicts are verified'}
    (output/'boot-config.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--loader-source',type=Path,required=True)
    parser.add_argument('--interface',choices=('mmc','usb'),default='mmc')
    parser.add_argument('--device',default='0:1')
    args=parser.parse_args()
    print(json.dumps(generate(args.output,args.loader_source,interface=args.interface,device=args.device),indent=2))


if __name__=='__main__':main()
