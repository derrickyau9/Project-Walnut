# DaOS BSD Unix on ARM64

Status: platform decision accepted at the user's direction, 2026-11-05.
ARM64 source-base and reference-target software execution are verified;
assembled-image acceptance and physical-camera compatibility remain pending.

## Decision

Use **ARM64 FreeBSD as the DaOS BSD Unix platform**, replacing Buildroot as the
active appliance route. Independently rebuild the pinned upstream FreeBSD
world and kernel using the [source-base recipe](../../system/freebsd/base/README.md).
The active [ARM64 p6 source lock](../../system/freebsd/base/source-p6-arm64.lock.json)
pins commit `95719d8a895bf8170851e9844d9b4092b1d2b6fe`.
The [`DaOS` kernel profile](../../system/freebsd/base/DaOS) contains
`include GENERIC` and `ident DaOS`. This is a rebuilt FreeBSD kernel with a
project profile, not a BSD kernel written from scratch or a new sensor driver.

Keep the C++ camera authority and shared camera contracts as the application
boundary. Use the native QtQuick/PyQt5 body application and a target-matched
FreeBSD Python/Qt runtime. The [runtime/image recipe](../../system/freebsd/image/README.md)
collects declared ARM64 package files, checksums, dependencies and notices;
it does not claim every Qt/Python dependency was rebuilt from source by this
project. No Linux compatibility layer, Linux root filesystem or Linux camera
executable is substituted for FreeBSD execution.

An amd64 FreeBSD guest may build the ARM64 world/kernel and native programs.
It cannot establish ARM64 runtime behavior. Retain the distinct
[cross-build receipt](../../tools/freebsd_cross_build.py) and
[actual-target validation](../../tools/freebsd_target_validate.py), including
source, artifact and architecture commitments. The ARM64 reference guest is a
bootstrap/test environment, not the source-built appliance image.

## Evidence and consequences

The [R10 verification report](../testing/round10-freebsd-arm64-verification.md)
records completed source `buildworld`/`buildkernel` and private staging; six
native ARM64 FreeBSD artifacts; four CTest targets passing on actual ARM64;
and98 focused Python tests with90 passes and8 explicit skips. Qt/Python
portability was exercised in the ARM64 reference guest using offscreen Qt:
synthetic preview, one capture, retained gallery/export and resume of that
same capture without another shutter action. Independent DNG decoding is
recorded separately. None of this is a visible custom-image boot result.

The platform change has these engineering consequences:

- The original Linux BSP, V4L2/videobuf2, DMA-BUF, libcamera and RAUC route does
  not become FreeBSD-supported through recompilation or shared API names.
  Capture/DMA integration, synchronization, device access and update/recovery
  require an explicitly implemented and tested FreeBSD path.
- Sensor public initialization data, a releasable SLVS-EC receiver,
  board-specific drivers and verified real MCU/power signals remain missing.
  No physical FPGA/SoM, sensor interface, interrupt/DMA ABI, register map or
  MCU protocol is invented by this decision. The user's platform choice does
  not establish that a proposed physical SoM can boot or acquire images under
  FreeBSD. QEMU ARM64 virtual devices are not substitutes for that evidence.
- The [Linux implementation](../../system/linux/README.md), prior ARM64 Linux
  images/update-model results and [deferred Buildroot work](../../system/buildroot/STATUS.md)
  remain preserved, separate records. Their pass results are not relabeled as
  FreeBSD driver, bootloader, update or power-loss validation.
- [Original DaOS framework integration](../architecture/daos-relationship.md)
  remains zero imported source files. The camera architecture independently
  implements related capability/control/workflow ideas; the original React
  shell and TypeScript runtime have not been extracted or adapted. Platform
  naming does not change that provenance or resolve upstream reuse rights.

## Acceptance boundary

The next software gate is an actual assembled ARM64 appliance image with
recorded source/runtime/native inputs, disk/manifest hashes and partition
metadata, followed by boot of those exact bytes using the
[image-boot recipe](../../system/freebsd/image-boot/README.md). Retain serial and
framebuffer evidence, verify visible DaOS UI operation as the ordinary camera
user, then demonstrate capture/export and recovery of the original capture
after controlled restart. An offscreen screenshot, nonblack firmware screen
or compile-only receipt does not satisfy this gate. These custom-image results
are pending at this decision checkpoint.

Physical FF-1 acceptance remains separate: selected-board boot and driver
validation, real full-frame RAW integrity, input/display/storage/power behavior,
optics/HUMI, measured calibration/thermal results and independent reproduction.
The [FreeBSD entry point](../../system/freebsd/README.md) links each software
stage without declaring those hardware gates complete.
