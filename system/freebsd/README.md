# DaOS BSD Unix: ARM64 camera system

This is the entry point for the FreeBSD ARM64 route. It builds a pinned
FreeBSD world and `DaOS` kernel, adds the native camera and a target-matched
Qt/Python runtime, then assembles a separate appliance image for its own boot
validation. The amd64 FreeBSD VM is a build host; ARM64 target execution uses
a distinct QEMU ARM64 guest. Neither architecture is inferred from a label.

At the [R10 verification](../../docs/testing/round10-freebsd-arm64-verification.md),
the ARM64 source world/kernel and six native application artifacts were built.
Reference ARM64 tests passed four CTests,90/98 focused Python checks with8
skips,45 workflow cases and23 Qt interaction cases. Separately, assembled
image003 booted its full800x600 native Qt UI, saved one synthetic DNG,
recovered it and its settings after a full kernel restart, and exported it.
The [actual-image acceptance](image-boot/evidence/image003-ui-acceptance.json)
and [artifact guide](DELIVERY.md) distinguish those visible boots from earlier
offscreen reference-guest checks and static build/assembly evidence.

## Follow the pipeline

| Stage | Recipe, inputs and required evidence |
| --- | --- |
| 1. Bootstrap the build host and target | [amd64 build VM](vm/README.md), [release lock](release-lock.json), and [ARM64 reference target](arm-vm/README.md) with its [profile](arm-vm/profile.json). These are disposable development environments, not the delivered appliance base. Preserve launch, architecture, readiness and shutdown evidence; do not distribute their task keys or authorized overlays. |
| 2. Build the ARM64 base | [Base recipe](base/README.md), active [15.1-RELEASE-p6 ARM64 source lock](base/source-p6-arm64.lock.json), [source configuration](base/src.conf) and [`DaOS` kernel configuration](base/DaOS). [build_base.py](base/build_base.py) performs source checks, `buildworld`/`buildkernel` and private `NO_ROOT` staging, preserving METALOG and `build-state.json`. Host and target identities remain separate. |
| 3. Build native camera binaries | [Cross-build guide](../../docs/testing/freebsd-arm64-cross-build.md) and [freebsd_cross_build.py](../../tools/freebsd_cross_build.py): actual amd64 FreeBSD compiler host, verified ARM64 staged sysroot, Clang/LLD target triple, six checked ARM64 FreeBSD ELFs. Keep the original cross-build receipt; compilation is not a target test. |
| 4. Execute on ARM64 | [freebsd_target_validate.py](../../tools/freebsd_target_validate.py) runs as camera UID1000 on actual ARM64 FreeBSD. It binds source and artifact hashes to the original cross receipt, runs the four CTests and focused Python suite, and produces `DaOS-freebsd-arm64-target-validation-v1`. It does not configure, compile or require the build-host SDK. The [UI review runner](../../ui/on-device/review_capture.py) provides separate first/resume screenshots and retained-capture evidence. |
| 5. Collect the target runtime and assemble | [Image recipe](image/README.md), [runtime collector](image/collect_runtime.py) and [assembler](image/assemble_image.py). Collect declared ARM64/compatible noarch package files, checksums, licenses and dependency evidence in the ARM64 reference guest. Combine them with the source-built stage, tested camera and current project sources. Do not substitute the bootstrap VM's root filesystem. The output must retain its exact image/manifest hashes and source/runtime/native provenance. |
| 6. Boot that exact appliance image | [Image-boot recipe](image-boot/README.md), [profile](image-boot/profile.json) and [freebsd_image_boot.py](../../tools/freebsd_image_boot.py). Verify raw GPT/EFI/UFS image identity and boot with private writable overlay/UEFI variables. Review real framebuffer captures and boot logs before declaring the visible camera ready. A nonblack screen or successful offscreen render does not establish kiosk startup. |

Each stage has its own limits and prerequisites; use the linked recipe rather
than treating this table as an executed build script. New work/output paths
are required, existing results are retained, and tests are not blindly retried.
The current image profile separates read-only system files, persistent state,
calibration and photos. Its visible boot, single synthetic capture and controlled restart have been
checked on image003; unexpected power-loss and update recovery remain unvalidated. Generic VM tests do not validate
sensor drivers, a physical display, electrical interfaces or power-loss safety.

## Source boundaries and other platforms

The active source lock pins commit
`95719d8a895bf8170851e9844d9b4092b1d2b6fe`. The [original-release ARM64
lock](base/source-arm64.lock.json) and earlier amd64 [original](base/source.lock.json)
and [p6](base/source-p6.lock.json) locks remain separate historical/development
profiles; do not silently exchange their source or output artifacts.

The camera application is independently implemented. As the
[DaOS architecture assessment](../../docs/architecture/daos-relationship.md)
explains, no original DaOS App Shell, TypeScript engine/control or other DaOS
source was imported. Product branding does not complete that integration.
Project and third-party [rights/provenance inventory](../../licenses/INVENTORY.json)
still applies; assembling BSD, Qt and application components does not grant the
whole image one BSD license.

The earlier [Linux route](../linux/README.md), [ARM64 Linux image](../arm64-image/README.md)
and [deferred Buildroot work](../buildroot/STATUS.md) remain distinct. Their
binaries, boot tests and update-model results are not FreeBSD execution
evidence. They are preserved without being relabeled as DaOS BSD Unix.
