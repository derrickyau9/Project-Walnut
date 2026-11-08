# Reproduce the generic ARM64 Qt camera target

This profile builds a real bootable ARM64 Linux development image and exercises
the synthetic camera. It targets QEMU `virt`, not Zynq, a sensor, a physical
display, or a production firmware installation. The earlier R6 source ZIP and
evidence are separate preserved snapshots.

Use a non-root Linux user with Python 3.13 and the installed tools listed by
preflight. Required packages/tools are not installed by this recipe. The tested
host was Debian x86_64 in WSL, with Clang 19, GNU AArch64 GCC 14.2, QEMU 10.0.13,
e2fsprogs 1.47.2, dpkg/dpkg-deb, gpgv and the Debian archive public keyring. This
is a pinned binary-runtime recipe, not a source rebuild of Debian or Qt.

From the extracted source root, use a **new persistent Linux filesystem** path
for `--work-dir`. Avoid `/tmp` and Windows-mounted directories for this POSIX
staging tree. Ordinary disk-image files are created separately under the source
tree's `build/target-<work-path-hash>` directory, which the coordinator records.
Keep both the work tree and that directory until inspecting the results.

```sh
python3 -B -m tools.target_build preflight --work-dir "$HOME/hf-target-001"
python3 -B -m tools.target_build plan --work-dir "$HOME/hf-target-001"
python3 -B -m tools.target_build build --work-dir "$HOME/hf-target-001"
```

The fresh build downloads checksum-pinned toolchain supplementation, an official
kernel package and 170 official runtime packages as data. It verifies signed
Debian release metadata and exact package sizes/hashes. Nothing is installed on
the host; no maintainer scripts, chroot, host mount, binfmt registration or host
block devices are used. Installed compiler/sysroot/tool identities are recorded,
but the build environment itself is not a hermetic container or toolchain image.

For an offline replay, retain the three input caches from a successful build
and use new output paths. The native cache must contain matching tested source
hashes and actual AArch64 ELFs; the kernel and package caches must match the
pinned locks. The source snapshot contains signed package metadata, not the
106 MB of package archives, compiler binaries or kernel package.

```sh
python3 -B -m tools.target_build build \
  --work-dir "$HOME/hf-target-offline-002" \
  --native-dir /path/to/previous/native \
  --kernel-dir /path/to/previous/kernel \
  --inputs-dir /path/to/previous/inputs --offline
```

The five cached-mode stages are input verification, POSIX staging, image build,
first boot and resume boot. The two guest phases run ordinary ARM Python and
ARM Qt/QML as UID 1000, directly invoke the native ARM authority, and use a
read-only root image plus a private writable data image. The first boot presses
the QML shutter, reviews the saved synthetic Bayer/DNG and exports identical
bytes. Resume queries the saved UUID without another shutter action. Both phases
must close the native process; exactly one completed journal row must remain.
The root image hash, request/frame identity and DNG/export hashes are checked
independently by the coordinator. There is no external guest network interface
or shared host directory. The offscreen renderer is not evidence for a display.

`run-state.json` is atomically written with a unique run ID, component commands,
timings, log hashes, source hashes, status and image directory. Per-stage logs
remain in the work tree. The image directory contains `build-evidence.json` and
`boots/{first,resume}/boot-evidence.json`, serial logs and extracted guest files.
Stage output and time are bounded; cancellation stops only the coordinator's
owned child process group. No failed capture/build stage is retried or resumed
automatically. Inspect failed evidence and choose a new work path after fixing
inputs. Reusing a completed work or image destination is rejected.

The [runtime profile](../arm64-rootfs/README.md),
[image builder](../arm64-image/README.md) and
[ARM UI report](../../docs/testing/arm64-ui-round7.md) describe component limits.
Real board bootloaders/BSPs, libcamera/V4L2/DMA, sensor initialization and SLVS-EC,
physical input/display, A/B firmware updates, arbitrary power-loss behavior and
optics still need their own implementation and physical acceptance tests.
