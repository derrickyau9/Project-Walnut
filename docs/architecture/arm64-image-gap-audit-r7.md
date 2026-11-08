# ARM64 image and Qt deployment gap audit — round seven

This audit precedes the round-seven implementation. It preserves the round-six
source snapshot and distinguishes target instructions, operating-system boot,
GUI deployment and a physical camera board.

| Area | What R6 actually established | Missing at the start of R7 |
| --- | --- | --- |
| Native C++ | Four static AArch64 programs; ELF/source/tool binding; user-mode and generic guest execution | No new native acquisition feature is needed to prove the GUI deployment path |
| Linux boot | Verified Debian ARM64 kernel and a small static, RAM-only initramfs | Dynamic ARM64 userland, filesystem image, modules/bootstrap and a persistent data device |
| Static package | Correct target manifest and native-host guard; packaged host-Python/external-QEMU smoke | ARM64 Python interpreter and its native standard-library modules |
| Qt/QML | Working Windows PyQt5 desktop UI and real retained-image review/export | ARM64 Qt/PyQt extensions, QML modules/plugins/fonts, and native ARM GUI execution |
| Persistence | Same-process-directory daemon restart in RAM; host A/B model | Separate virtual-kernel boots recovering the same data/image state |
| Reproduction | Native and initramfs source/artifact hashes; fixed kernel/tool package locks | Complete locked userland dependency closure, rootfs manifest and repeatable image recipe |
| Hardware | Explicit interface/CAD/supplier gates | Board BSP, sensor/receiver/FPGA/DMA, physical display, power, mechanics and calibration remain unavailable |

The selected execution environment has a Debian x86_64 Linux process under WSL,
the ARM64 GNU C sysroot/compiler, Clang, explicit QEMU user/system tools,
`qemu-img`, `mke2fs`, `debugfs`, `e2fsck`, Debian keyrings and package tools.
Rootless filesystem-image creation is available. No host package installation,
binfmt registration, root mount, service change or credential is assumed.

The implementation route under investigation is a separate generic-virtual
profile: authenticated official Debian ARM64 package inputs, isolated extraction
without maintainer scripts, an immutable root filesystem and a new private data
image, and an actual ARM64 Python/Qt/native-camera first/resume smoke. It is not
the production board's Buildroot/BSP decision or a claim that all upstream
components have been rebuilt from source. Exact package versions/closure,
toolchain inputs, filesystem normalization and successful boot results must be
recorded before this route is marked verified.

The UI source is not being replaced with a mock. Its existing QML imports are
QtQuick, Controls and Layouts 2.15; the target profile must provide compatible
ARM64 libraries and modules. Offscreen/software rendering can establish native
Qt execution and rendered output, while physical display/GPU/touch behavior
remains a separate acceptance test.

The guarded ARM runner must verify ELF machine 183 for Python, native Qt
extensions and the camera authority before declaring target execution. Its
first phase must make one real simulator request, review/export retained pixels
and record the UUID. Its resume phase must reopen that same UUID without a
shutter invocation, with unchanged DNG identity and exactly one journal row.
Virtual disk persistence is not physical-media power-cut or bootloader rollback
validation.

R6 remains available independently as Library item
`libfile_8b9acaf9fe048191ba7ed30e29cd1cd0`, SHA-256
`49ae3ed5eb6426505f0ef978522dee8f3615b2eab7458b0a95c84e353a76c010`.
All R7 results must be reported as later work; no missing component is counted
as complete merely because a manifest or script was written.
