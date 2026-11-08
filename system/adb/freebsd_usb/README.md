# FreeBSD native USB ADB transport

For the computer connection steps, Pi4 USB-C cable and independent power,
see the [USB access guide](../README.md).

This source targets the project's pinned FreeBSD 15.1-RELEASE-p6 source commit
`95719d8a895bf8170851e9844d9b4092b1d2b6fe`. It adds a device-side USB template
and a `libusb20` bulk-to-stdio helper. The separate authenticated daemon handles
ADB AUTH, shell and sync. This transport does not open a network listener.

The kernel module registers template index `55813` (`0xDA05`). It wraps the
existing `usb_temp_setup_by_index_p` dispatch, delegates other indices, and
depends on `usb_template`. It deliberately returns `EBUSY` for unload because
the USB stack retains pointers to its descriptors. Reboot to remove it.

The template has one interface `ff/42/01`, bulk IN `0x81`, bulk OUT `0x01`,
full-speed 64-byte and high-speed 512-byte maximum packets. VID `ffff` / PID
`da05` are **unallocated development values**, not a USB-IF assignment. Set
`hw.usb.daos_adb.vendor_id`, `product_id` and a per-device `serial` as loader
tunables before loading the module. A shipped product requires its own assigned
identity. The upstream template engine determines advertised power from its
`hw.usb.template_power` setting, overriding the template's power fields.

MS OS 1.0 descriptors supply string `0xEE`, vendor request `0xDA`, `WINUSB`
compatible ID and REG_MULTI_SZ `DeviceInterfaceGUIDs` with Android's ADB GUID
`{F72FE0D4-CBCB-407D-8814-9ED673D0DD6B}`. Windows binding and ADB discovery still
require physical testing; generated descriptors alone do not prove enumeration.

## Build

Use the same FreeBSD source, world build environment, src.conf, and kernel
objects as the image. `build-in-env.sh` copies source into private output
directories. Set these environment variables to absolute paths:

- `DAOS_USB_SOURCE`: this directory
- `DAOS_USB_OUTPUT`: private output directory
- `DAOS_FREEBSD_SOURCE`: pinned `usr/src`
- `DAOS_KERNEL_OBJECT`: matching `sys/DaOS` kernel object directory, containing
  `opt_global.h`

From the FreeBSD source tree, retaining the original `MAKEOBJDIRPREFIX` and
source configuration:

```sh
make TARGET=arm64 TARGET_ARCH=aarch64 \
    SRCCONF=/absolute/config/src.conf SRC_ENV_CONF=/dev/null __MAKE_CONF=/dev/null \
    buildenv BUILDENV_SHELL='/bin/sh /absolute/freebsd_usb/build-in-env.sh'
```

The results are `helper/daos-usb-adb` and `kmod/daos_adb_template.ko`. Inspect
ELF machine, interpreter, dependencies and hashes before staging them. The
recipe describes a build; successful target compilation needs a separate
execution receipt.

## Image integration

Pi 4's USB-C port uses its SoC DWC OTG controller. The four Type-A ports use
the host controller. Enable the SoC USB node with `dr_mode = "peripheral"` in
the actual boot device tree; FreeBSD's `dwc_otg_fdt.c` reads that value and
selects `DWC_MODE_DEVICE`. The ARM64 GENERIC Broadcom include has `device
dwcotg`. Inspect the final DTB, not just a firmware overlay filename. Existing
firmware/U-Boot behavior and physical enumeration must be verified on the board.

After `usb_template` and this module have loaded, set `hw.usb.template=55813`.
Doing it at runtime reprobes the USB devices; loading the custom module before
selection ensures the index is known. The helper waits for the computer to
configure the peripheral, checks `LIBUSB20_MODE_DEVICE`, VID/PID and exactly
one ADB interface, and then launches the daemon. It will never attach to an
Android phone connected to a Pi host port. Run it from a background supervisor:

```sh
/usr/local/libexec/daos-usb-adb --vid 0xffff --pid 0xda05 --wait -- \
    /usr/local/bin/python3.12 /usr/local/libexec/daos-adb/daos_adbd.py \
    --stdio --authorized-keys /etc/daos/adb_keys --shell /bin/sh --cwd / --sync-root /
```

Install all daemon sibling Python files together. The key file must contain
the development computer's explicitly authorized public ADB key. The helper
does not authorize keys or bypass daemon authentication. `--list` shows ready
device-mode interfaces; `--bus N --address N` disambiguate multiple matches.
USB disconnect returns status 1 so the supervisor can restart a new daemon
and require AUTH again. A missing cable with `--wait` does not exit. SIGTERM
closes the pipes and terminates the child. The service must be asynchronous
so waiting for a USB host does not block boot.

The daemon's stdin/stdout contain only ADB bytes. The helper reassembles its
stdout into 24-byte headers and bounded payloads, sending the header and payload
in distinct USB IN transfers. OUT bytes are forwarded with bounded buffers and
pipe backpressure. The logical maximum payload is 1 MiB, while physical endpoint
buffers are at most 64 KiB and respect the controller's actual reported limits.
Payload windows before the final one are maximum-packet multiples and do not
force a short packet or ZLP. No claimed fallback
to TCP is hidden in this path.

## Validation and sources

`python3 -B tests/test_wire.py` checks descriptor lengths/offsets, the actual C
vendor callback and packet guard, and a deterministic libusb20 mock with real
forked daemon pipes. The mock checks fragmentation, separate IN header/payload
writes, and rejection of host mode, wrong interface and unconfigured devices.
These tests do not validate a FreeBSD kernel module, controller or Windows.

Separately, the source-built ARM64 helper/module compiled successfully against
the pinned source SDK. The module loaded on the matching FreeBSD ARM64
reference guest and selected template 55813; the official adb protocol suite
then passed through a private TCP/SSH test path. The
[actual target receipt](../daemon/evidence/freebsd-arm64-001/summary.json)
distinguishes this execution proof from the still-pending physical Pi4 USB-C
enumeration and Windows WinUSB-binding tests.

Primary references:

- [FreeBSD USB template API](https://github.com/freebsd/freebsd-src/blob/95719d8a895bf8170851e9844d9b4092b1d2b6fe/sys/dev/usb/template/usb_template.h)
- [FreeBSD template dispatcher and vendor requests](https://github.com/freebsd/freebsd-src/blob/95719d8a895bf8170851e9844d9b4092b1d2b6fe/sys/dev/usb/template/usb_template.c)
- [FreeBSD ugen device-mode endpoint access](https://github.com/freebsd/freebsd-src/blob/95719d8a895bf8170851e9844d9b4092b1d2b6fe/sys/dev/usb/usb_generic.c)
- [FreeBSD libusb20 API](https://github.com/freebsd/freebsd-src/blob/95719d8a895bf8170851e9844d9b4092b1d2b6fe/lib/libusb/libusb20.h)
- [FreeBSD DWC device-mode selection](https://github.com/freebsd/freebsd-src/blob/95719d8a895bf8170851e9844d9b4092b1d2b6fe/sys/dev/usb/controller/dwc_otg_fdt.c)
- [Microsoft WinUSB automatic installation](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/automatic-installation-of-winusb)
- [AOSP ADB Windows interface GUID](https://android.googlesource.com/platform/development/+/refs/heads/tools_r20/host/windows/usb/android_winusb.inf)

Files in `upstream/` are unmodified official-source references with their
original notices. They are not the source tree used to build the module.
