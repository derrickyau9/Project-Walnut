# Generic ARM64 virtual Linux test target

This target boots an actual ARM64 Linux kernel under QEMU's generic `virt`
machine and executes the static native HF-Sim programs. It supplies no Zynq BSP,
sensor model, FPGA/DMA device, display or physical camera support. QEMU explicitly
describes [virt](https://www.qemu.org/docs/master/system/arm/virt.html) as a
virtual platform that does not correspond to real hardware.

| Verification level | What executes | What it cannot establish |
| --- | --- | --- |
| ARM64 user mode | AArch64 program instructions; syscalls mapped to the host Linux kernel | Target kernel, boot or board behavior |
| This full-system target | Virtual Cortex-A53 CPUs, a Debian ARM64 kernel, RAM-only initramfs and native programs | Zynq/SoM boot, actual peripherals, storage power-loss or timing |
| Real board | Not tested here | All physical integration acceptance remains open |

## Build and run on a Linux host

Use a dedicated source/build workspace. The verified host was Debian x86_64 in
WSL, with QEMU 10.0.13, GCC 14.2.0 and Clang 19. The helper uses installed tools
and an isolated official C++ development package, not system package installs.
Follow the [ARM64 build guide](../arm64/README.md) first:

```sh
python3 -m tools.arm64_build --build-dir build/arm64-native --tools-dir build/arm64-tools
python3 -m tools.qemu_target fetch-kernel --dest build/qemu-kernel --lock system/qemu-virt/kernel-lock.json
python3 -m tools.qemu_target build --native-dir build/arm64-native --build-dir build/qemu-run-001
python3 -m tools.qemu_target boot --build-dir build/qemu-run-001 --kernel build/qemu-kernel/extracted/boot/vmlinuz-6.12.107+deb13-arm64
```

The ARM64 build must have successfully executed its tests before guest packaging.
The guest builder checks the complete current source map, tested executable
hashes, static ARM64 ELF headers and guest-init sources. It refuses arbitrary,
stale, dynamically linked or wrong-architecture inputs. All four executables
are included in the initramfs; they are not host executables.

The kernel fetcher downloads/extracts an official Debian package in the chosen
new build directory; it never installs a kernel. The first kernel selection was
verified using Debian's signed InRelease, the matching SHA-256 package index and
package checksum. `kernel-lock.json` preserves that selection. Lock-based
reproduction verifies the recorded package hash; it does not claim a new release
signature verification. The package is about 93 MB and repository retention may
affect future availability. For a deliberately newer investigation, omit `--lock`,
retain the new manifest separately and review the resulting version; do not
silently substitute it for recorded evidence.

QEMU uses TCG, two virtual Cortex-A53 CPUs and 512 MB RAM. Networking, disks,
monitors, graphical display, KVM and host-filesystem sharing are absent. Guest
device nodes are written into the CPIO archive as metadata; no host `mknod`,
mount, root privilege, reboot or service installation occurs. Its `/init` runs
as PID 1 inside the virtual machine only.
The init executable also requires that PID and the explicit virtual-kernel
marker; an ordinary host/user-mode invocation exits before mount or reboot.

## Guest checks and outputs

The minimal guest:

1. Confirms `uname` reports `aarch64`, mounts its own `/proc`, and runs both
   native test executables.
2. Runs the native image worker and verifies that a second write cannot replace
   the first DNG.
3. Starts the real `hf-camerad` process with a fresh RAM-only journal, retains a
   UUID before one capture send, and queries that same ID until completion.
4. Closes and restarts the authority, verifies the same request/frame identity,
   and exercises retained listing and asset metadata without another capture.
5. Streams the committed synthetic DNG over serial, then powers off the guest.

The C RPC fixture expects only this program's fixed requests and trusted daemon
responses; it is not a general JSON SDK or a production controller. It does not
print control tokens. A failed or unconfirmed request fails the guest test and
never triggers a replacement capture.

The host bounds runtime (90 seconds by default, at most 300) and serial output
(4 MiB), stops its owned QEMU process on timeout, validates unique success and
transfer markers, and checks DNG identity/dimensions. Independent Bayer/decoder
validation is recorded with the test evidence. Outputs are `serial.log`,
`guest.dng` and `boot-evidence.json` in the guest build directory. A previous
run's outputs are never overwritten: use a fresh guest build directory for
each run, including after a failure. Kernel and toolchain caches can be reused.

Initramfs ordering, ownership, modes, timestamps and gzip time are fixed. Source,
kernel, native artifacts and tool versions are recorded. This is a repeatable
procedure with integrity/provenance checks, not a hermetic or bit-identical
cross-host toolchain claim. Kernel/tool binaries remain build-cache inputs and
are not included in the source snapshot.

## Deliberately untested boundaries

The guest filesystem is RAM-only. SQLite/file synchronization and daemon restart
are exercised, but there is no persistent card, whole-VM reboot recovery,
arbitrary power cut, A/B bootloader or actual firmware update. No Qt/ARM display,
touch/buttons, sensor programming, SLVS-EC, V4L2/libcamera, DMA, thermal behavior,
mount alignment or hardware throughput is established. An ARM64 deployment
package must still be tested on its selected board and operating system.
