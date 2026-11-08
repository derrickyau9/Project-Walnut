# DaOS ARM64 appliance image boot

`tools/freebsd_image_boot.py` boots the assembled, sanitized raw GPT/EFI/UFS image
as an actual ARM64 guest. The source file is opened read only and hash checked;
QEMU writes only a NEW private qcow2 overlay and private UEFI variables. The large
source image is not copied. This runner injects no NoCloud seed, SSH key or NIC,
and creates no host mount, filesystem share, VNC listener or external port.

The input `image-evidence.json` must use `DaOS-freebsd-image-build-v1` and bind:

- The exact raw image byte size and SHA256.
- ARM64 target, ELF machine183 and EFI machine43620.
- Kernel identity `DaOS`, its hash and the completed source-build state hash.
- Actual collected ARM64 runtime, `FreeBSD:15:aarch64`, and tested ARM64 native
  executable/evidence hashes.

These consistency checks preserve the caller's build provenance. They do not
authenticate a publisher or independently parse the UFS kernel payload. The raw
parser separately checks the protective MBR, primary and backup GPT/header/array
CRCs and contained nonoverlapping partitions. Appliance boot requires exactly
five unique UTF-16 GPT labels: `DaOS-efi` of EFI type, plus `DaOS-root`,
`DaOS-state`, `DaOS-calibration` and `DaOS-photos` of FreeBSD UFS type. The generic
parser test mode also supports a simpler single-UFS fixture; it cannot satisfy
the appliance boot contract. Actual boot and visual review remain necessary.

```sh
python3 -m tools.freebsd_image_boot serve \
  --image /home/user/task/DaOS-arm64.img --sha256 VERIFIED_RAW_SHA256 \
  --provenance /home/user/task/image-evidence.json \
  --work-dir /home/user/task/image-boot-001 --timeout 600
```

The work directory must be NEW beneath an existing canonical parent. The default
guest has four Cortex-A72 CPUs, 4 GiB RAM, multithreaded TCG, AAVMF UEFI,
`ramfb` providing a linear UEFI GOP framebuffer, USB keyboard and tablet. The
firmware and FreeBSD loader select the resolution; the runner records each
capture's actual width and height and does not promise1280x800. At least800x600
must be observed before judging the full camera layout usable. The earlier
virtio-GPU profile was rejected by actual SCFB startup: its FreeBSD VT driver
does not support the framebuffer mmap operation SCFB requires. `ramfb` replaces
that device rather than running alongside it. An ordinary virtual
`virtio-rng-pci` device receives fresh host CSPRNG bytes from `/dev/urandom`; no
shared or baked entropy seed is included in the source image. The matching guest
image loads FreeBSD's `virtio_random` module. This device does not enable a NIC,
SSH or a host directory share. The host display remains
disabled; GOP and the guest framebuffer still exist. A private Unix QMP socket
provides controls. QEMU owns a separate process group and receives a parent-death
signal; the runner bounds its lifetime/serial and terminates its owned children.
Up to120 seconds of the time budget is reserved for guest poweroff. Serial,
source copies/hashes, launch and final observation metadata stay in the work tree.

Pinned FreeBSD p6 loader defaults `efi_max_resolution` to `1x1`, preserving the
firmware's current mode. An image override such as `1024x768` lets its normal GOP
auto-resize choose the largest supported mode within that limit; it does not add
unsupported firmware modes. The actual framebuffer still requires measurement.

`serve` automatically takes three framebuffer observations, normally60,120 and
180 seconds after launch, then keeps running for inspection until stopped or its
budget expires. `boot` takes the same three observations and then powers down.
Shorter budgets scale observation times. Neither mode sends a shutter action.

From a second terminal:

For mouse input, replace the example coordinates and dimensions with those from
the actual framebuffer capture.

```sh
python3 -m tools.freebsd_image_boot status --work-dir /home/user/task/image-boot-001
python3 -m tools.freebsd_image_boot screenshot --work-dir /home/user/task/image-boot-001 --name reviewed
python3 -m tools.freebsd_image_boot key --work-dir /home/user/task/image-boot-001 --keys tab
python3 -m tools.freebsd_image_boot click --work-dir /home/user/task/image-boot-001 \
  --x 640 --y 400 --width 1280 --height 800
python3 -m tools.freebsd_image_boot stop --work-dir /home/user/task/image-boot-001
```

After a controlled shutdown, explicitly boot the saved state in a NEW child
work directory:

```sh
python3 -m tools.freebsd_image_boot resume \
  --parent-work-dir /home/user/task/image-boot-001 \
  --work-dir /home/user/task/image-boot-002 --timeout 600
```

`resume` creates a small new qcow2 layer backing the stopped parent's qcow2. It
copies the parent's UEFI variables into a new private file and preserves the
original raw image, parent disk state, launch/serial logs, screenshots and final
receipt. It does not reset to the pristine image or submit a capture. The guest
can recover its original saved request ID through its normal UI startup; that
reconciliation must be checked separately.

The parent must have an exit0 completed observation, an actually observed kernel
`All buffers synced` marker, unchanged immutable inputs, and identity/hash seals
for its stopped qcow2 and UEFI variables. Missing markers, forced exits, failed
or unsealed older runs, active owners/PIDs/QMP sockets, changed bytes or replaced
files are refused. Source-build/runtime provenance and saved runner sources are
verified again. Read-only `qemu-img check` and the exact bounded backing chain are
checked before constructing the child. Up to eight immutable backing inputs,
including the original raw image, are supported. Ancestor lifecycle locks stay
held while the child runs; ancestor bytes and evidence are verified afterwards.
There is no automatic repair, retry, rebase or replay after an uncertain stop.

Input commands act only when explicitly requested. Screenshot files are create
only and confined to `screens/`; up to32 captures are supported. QMP writes to a
pinned owned descriptor. The PNG conversion preserves every RGB byte from the P6
framebuffer. Independent image decoding is covered by focused tests.

The explicit click command holds its press150ms to span USB polling and sends
one release in a finally path. It never retries a press after an uncertain
QMP result. Its tests inject press/hold/release failures. This operator-tool
follow-up was made after the accepted image003/004 manual held-input runs;
their archived source snapshots and original observations remain unchanged.

A nonblack image can be a firmware logo, loader or console. Every capture and
observation therefore sets `UI_readiness_claimed:false`. A human must inspect
the actual whole-screen images and guest boot/Xorg evidence to establish that
the DaOS kiosk started. QML offscreen captures are a separate runtime check.
The firmware-selected console may route kernel or session output to GOP instead
of the UART; serial identity and GUI markers are recorded only when actually
observed and are not assumed from their source configuration.
`All buffers synced` is recorded when seen; it is not an independent UFS fsck or
a power-loss-safety claim. Original source-image and UEFI-code hashes must remain
unchanged after the owned VM stops. Failed partial overlays and logs are retained
without replaying captures or modifying the source image.
