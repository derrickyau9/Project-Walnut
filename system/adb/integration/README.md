# Native USB ADB image integration

`install.py` creates a new bundle from a completed FreeBSD ARM64 source-world
record, a successful USB transport/module build receipt, the original official
Pi4 firmware, the authenticated Python daemon manifest and explicit authorized
public ADB keys. It never modifies the original world, firmware or old image.

```sh
python3 -B system/adb/integration/install.py \
  --base-state /absolute/pi-splash/build-state.json \
  --firmware-dir /absolute/original-pi4-firmware \
  --helper /absolute/compiled-006/daos-usb-adb \
  --module /absolute/compiled-006/daos_adb_template.ko \
  --usb-build-receipt /absolute/compiled-006/build-evidence.json \
  --daemon-dir /absolute/project/system/adb/daemon \
  --authorized-keys /absolute/authorized-adbkey.pub \
  --output /absolute/new-usb-bundle --dtc /usr/bin/dtc
```

The source tree around this installer must include the project's FreeBSD image
validation modules and the exact `freebsd_usb` source files bound by the compile
receipt. The installer checks source-world anchors, the native compiled ELF
identity and runtime library closure, all daemon runtime hashes and the Android
RSA2048 public-key structure. It does not install a private key. Development USB
identity defaults to unallocated `ffff:da05`, serial `DaOS-USB-ADB-DEVELOPMENT`.
VID/PID and safe ASCII serial may be selected explicitly. The shipped identity
still requires an actual USB-IF allocation.

The bundle contains `root/`, `efi/`, `manifest.json`, `receipt.json`,
`config.txt.append` and DTB derivation evidence. File records include relative
path, type, numeric mode, root ownership, byte count and SHA256. The receipt
binds the manifest hash. Root and EFI payloads contain only manifested files.

`apply_overlay.apply_to_prepared(value, summary, board, bundle)` integrates a
bundle directly after `assemble_pi4.prepare_pi()` and before any `makefs` or
`mkimg` call. It changes only the private assembly directory, refreshes
`root.mtree`, updates every affected EFI `boot_payload` checksum and rewrites
the installed payload/board JSON documents. Existing source evidence remains
unchanged. This function also binds the overlay to the selected base-state
and firmware receipt hashes and rechecks every staged payload hash.

The original assembler can place Pi4 DTBs at firmware
`efi/bcm2711-rpi-4-b.dtb`, each existing source-world candidate under
`root/boot/dtb/**/bcm2711-rpi-4-b.dtb`, and its copy under
`efi/dtb/**/bcm2711-rpi-4-b.dtb`. All existing candidates receive a derived
replacement. A source world with zero Pi4 board DTBs is valid: the separately
pinned official firmware supplies the board DTB. The receipt records the
actual `source_world_candidates` count, including zero.
The firmware-derived canonical `efi/daos-rpi4-usb-device.dtb` is selected by
the final `[all] device_tree=` directive. Every candidate's SoC USB node is
enabled, forced to `peripheral`, and includes `snps,dwc2`, which the pinned
FreeBSD DWC FDT driver recognizes. `dtc` roundtrips each candidate; a binary
FDT parser proves all other properties, node paths and memory reservations
remain unchanged. The original bytes and hashes are retained in the receipt.

The GPU then applies runtime fixups to the selected derived firmware DTB;
U-Boot provides its FDT to the EFI loader. No raw file is preloaded over the
EFI FDT table, which preserves firmware memory/MAC/chosen fixups. Coverage of
static candidates is verified; the final runtime FDT, physical USB-C controller
and Windows WinUSB binding remain hardware tests.

`loader.conf.local` sets `hw.usb.template=-1` so no default gadget template
enumerates before the custom template is registered. The RC service runs
`/usr/sbin/daemon -P ...` and returns immediately.
Its Python supervisor loads `usb_template` and the custom module, then selects
template `55813`. A disconnected cable waits inside the background helper.
After disconnect, the supervisor starts a new daemon and AUTH session. SIGTERM
propagates through the daemon supervisor to the helper and authenticated ADB
process. Logs persist in `/var/log/DaOS-usb-adb.log`, with four files capped
at 2 MiB each. PID files live in writable `/var/run`; the root filesystem can
remain read-only. The service opens no TCP listener and performs no automatic
key enrollment.

Run `python3 -B tests/test_integration.py` for DTB preservation, actual firmware
derivation, explicit-key validation, module-before-template ordering, target
guards, real child-process restart/shutdown and stdio-only command tests.
