# R10 DaOS BSD Unix: ARM64 verification

Date: 2026-11-05. This report records a completed FreeBSD ARM64 source
world/kernel, target-native camera/workflow/Qt checks, image003 assembly and
filesystem admission, followed by actual800x600 native Qt display, one
synthetic capture, full kernel restart/same-request recovery and GUI export
on that exact image. Earlier reference-guest Qt suites used the offscreen
platform and remain separate. Failed FAT/display/kiosk attempts are retained.
No physical FF-1, sensor, board display or manufacturing release is validated.

## Actual results

| Requirement | Evidence and exact result | Remaining boundary |
| --- | --- | --- |
| ARM64 operating-system source build | [Base-build receipt](../../system/freebsd/base/evidence/arm64-p6/build-state.json): completed `buildworld`, `buildkernel`, `installworld`, `distribution`, `installkernel`, all exit0. Target `arm64`/`aarch64`; kernel configuration identifier `DaOS`. The same kernel/world now enter assembled image003. | The build host was FreeBSD amd64, UID1000. Source-build and filesystem admission are separate from the later actual-image UI acceptance below. |
| Native ARM64 application build | [Cross-build receipt](../../system/freebsd/arm-vm/evidence/camera/cross-build.json): six actual ELF64 little-endian AArch64 artifacts, machine183, FreeBSD OSABI9; Clang/LLD19.1.7 with target `aarch64-unknown-freebsd15.1` and the source-built ARM64 sysroot. | Cross-compilation alone does not execute target tests. No target recompilation is claimed. |
| Actual ARM64 native tests | [Target-validation receipt](../../system/freebsd/arm-vm/evidence/camera/target-validation.json): FreeBSD15.1-RELEASE-p6, machine `arm64`, UID1000; four CTest targets passed in 3.060s. Separate direct execution reports 89 core +126 authority +21 SHA256 +325 calibration checks: 561 checks. | Direct execution and CTest overlap; their counts must not be added into a larger unique-test total. |
| Camera/API/settings/calibration Python checks | Same target receipt: 98 tests, 90 passed, 8 explicit skips, zero failures/errors; bounded Python command took 96.911s. | Six Linux `/proc`/compiler/`LD_PRELOAD` interposition checks and two optional `jsonschema` checks were skipped. None counts as a pass. Synthetic calibration binding is not measured calibration or applied RAW correction. |
| Separate ARM64 workflow regression | [Workflow-validation receipt](../../system/freebsd/arm-vm/evidence/workflow-r10-002/workflow-validation.json): 45/45 passed, zero skips/failures/errors, 45.174s; actual FreeBSD15.1-RELEASE-p6 ARM64, UID1000. Four modules contain17 workflow,12 recipe,8 recovery and8 continuation/CLI tests. | Separate execution from the98-test camera suite. One recipe case executes the verified native daemon; remaining cases exercise the Python reference camera, controlled fake cameras and workflow/CLI behavior. No target compilation, physical camera or image-boot claim. |
| Body UI and retained settings | [First UI report](../../system/freebsd/arm-vm/evidence/camera/ui-first.json) and [resume report](../../system/freebsd/arm-vm/evidence/camera/ui-resume.json): native ARM64 authority; synthetic live view, capture, retained gallery and export; zero QML warnings. First run19.148s, resume10.115s. | QtQuick/PyQt5 application in the reference guest, `qpa=offscreen`; not a physical display, visible compositor session or final-image kiosk boot. |
| Separate full ARM64 Qt interaction regression | [UI interaction receipt](../../system/freebsd/arm-vm/evidence/ui-test-r10-001/ui-test-validation.json): unmodified `tests.test_device_ui`, 23/23 passed, zero skips/failures/errors, 42.750s; actual ARM64 FreeBSD UID1000, Qt5.15.19/PyQt5.15.11, `qpa=offscreen`. | Eight cases exercise the native authority or its HTTP attachment; the other15 exercise reference/fake-camera and UI validation behavior. This is a separate suite, with no target compilation, physical-display or visible custom-image boot claim. |
| DNG interoperability | [Independent decode report](../../system/freebsd/arm-vm/evidence/camera/independent-dng.json): exported ARM64-generated DNG read with tifffile2023.4.12 and rawpy0.28.0/LibRaw0.22.2; all12,288 Bayer samples equal the separate oracle, RGB shape96x128x3. | Independent checks concern a copied synthetic128x96 DNG, not a real sensor image, full-frame throughput, color calibration or decoder execution inside FreeBSD. |
| Image003 DNG and durable request | [Independent Windows check](../../system/freebsd/image-boot/evidence/image003-capture/independent-Windows-parent003.json): actual saved image003 DNG read by tifffile2023.4.12 and rawpy0.28.0/LibRaw0.22.2. Both decoders returned all12,288 Bayer samples exactly equal to a separate Python oracle at10100us/gain1.0; RGB shape96x128x3. Immutable SQLite integrity is good with exactly one complete capture row, matching original request/UI/settings and frame_sequence1. | Separate from the earlier reference guest's12500us DNG. The image003 photo is25,662bytes, SHA256 `34526aaace0136e38935822d974fa8d548060d0d5ea8ab8b726894b31f9529e0`. Synthetic128x96 data does not validate a physical sensor or full-frame throughput. |
| Original DaOS framework integration | [Architecture assessment](../architecture/daos-relationship.md) and [rights inventory](../../licenses/INVENTORY.json): zero original DaOS source files imported; `daos_source_copied=false`. | Existing React App Shell and TypeScript schema/engine/control were not extracted. Camera contracts, workflow execution, ownership, browser console and Qt UI are independent implementations of related patterns. Source/rights boundaries and an actual upstream adapter remain unresolved. |
| DaOS BSD Unix image003 assembly | [Image-build receipt](../../system/freebsd/image/evidence/assembled-003/image-evidence.json): assembly completed in105.717s; ARM64 UEFI/GPT image,3,187,712,000 raw bytes,362,424,748 compressed bytes. Same pinned kernel, source world and tested native binary. | Receipt correctly retains `boot_validated=false` and `gui_validated=false`. Those fields describe assembly time. Later separate first/resume observations and human review establish the actual-image GUI/capture/export/restart result below. |
| Image003 filesystem admission | [Native filesystem report](../../system/freebsd/image/evidence/assembled-003/filesystem-validation.json): EFI `fsck_msdosfs -M -n` plus four UFS `fsck_ffs -n` commands all exit0; all before/after hashes unchanged. [Independent Linux EFI report](../../system/freebsd/image/evidence/assembled-003/linux-efi-validation.json): `fsck.fat -n -v` exit0 on the exact64MiB ESP, also unchanged. | Filesystem checks do not execute ARM code or establish a successful boot. Linux reports an uninitialized free-cluster summary hint; its exact output is preserved, not described as warning-free. |
| Earlier custom-image display checks | Valid image002 booted the ARM64 DaOS kernel, RNG and all four UFS volumes. Its virtio-GPU profile failed SCFB framebuffer mmap; a subsequent RAMFB/efifb800x600 session rendered Xorg/Qt with the application/native authority as UID1000, but the initial1280x800 window was clipped. | These outcomes are retained as failure/partial display evidence. Image003 includes the kiosk QScreen geometry fix and later passed full800x600 visible-body-UI acceptance below. |
| FreeBSD update/recovery integration | Persistent state, calibration and photos have separate image volumes; no FreeBSD A/B slot/update acceptance is recorded. | The earlier Linux A/B model does not implement or validate FreeBSD bootloader/slot recovery. This remains missing work, separate from controlled application/OS restart checks. |
| Physical camera and board integration | No new physical-hardware pass is recorded in R10. | Sensor supply/public initialization data, SLVS-EC receiver, board-specific drivers, power/MCU signals, optics/HUMI tolerances, measured thermal/calibration and independent manufacture remain open. The original Linux V4L2/libcamera/BSP design is not an implemented FreeBSD sensor pipeline. |

The pinned FreeBSD source commit is
`95719d8a895bf8170851e9844d9b4092b1d2b6fe`, release15.1-RELEASE-p6; archive
SHA256 `62616e1381734098f3351a0dc97dd153fe78d5cdd74cea41c771c1c6e2e3c9b6`.
The source receipt records official git transport/archive checks, not a PGP
signature claim. Base steps took154.456s,101.537s,19.980s,9.150s and4.173s,
respectively. Installation used `NO_ROOT=yes` into a private stage;
`live_root_installed=false`. Actual shell/libc anchors are machine183/OSABI9;
the source-built kernel is machine183/OSABI0, which is not a userland branding
failure. Its SHA256 is
`a4e6121c99aae567340b478bfdc2d4ff309bec7ff29541785e7602887968e82d`.

The cross receipt deliberately retains `target_tests.status=not_run`: those
were not run on the amd64 compiler host. The separate actual-target receipt
sets `target_runtime_validated=true` only after the ARM64 tests pass. It binds
the original cross-receipt SHA256, all six binaries and the same75 source
inputs; those75 hashes also matched the current local source at this review.
No host sysroot is required or silently substituted by target validation.

The separate workflow [test details](../../system/freebsd/arm-vm/evidence/workflow-r10-002/python-tests.json)
and [execution log](../../system/freebsd/arm-vm/evidence/workflow-r10-002/workflow-tests.log)
retain all45 successes, including the copied-native-authority recipe case.
Its41 staged source hashes and original native binary identity were checked
before and after the isolated run; test data stayed in a new scratch directory.
These results are not folded into the98-test receipt or a combined total.
The complete Qt interaction suite now has separate actual ARM64 execution
evidence below; its result is not folded into either Python suite. Later visible
fullscreen operation and retained capture in image003 are recorded separately below.

## Assembled image003 and its software boundary

The [assembled image evidence](../../system/freebsd/image/evidence/assembled-003/image-evidence.json)
binds the following artifacts. Paths in the receipt identify private build
outputs; the distributable names below do not depend on those temporary paths.

| Artifact | Bytes | SHA256 |
| --- | --- | --- |
| `DaOS-arm64.img` | 3,187,712,000 | `69c824a3aa1f992fcb38e285145e4549c76107cab95a2a26e2e391e401b4942a` |
| `DaOS-arm64.img.xz` | 362,424,748 | `e06c24c415894e9e396b7b9af147fb25953c585e01e1ec44fd71994f51dd05a4` |
| Image-build receipt | 219,897 | `28d117a3d73ff30cf71143a84284106387fe385ef24ca2346931e3ddde4281a2` |

The build emits a64MiB EFI FAT32 volume,2560MiB read-only UFS root,128MiB
persistent state,32MiB calibration and256MiB photo volumes. Before mkimg, the
builder validates FAT32 bounds/capacity and runs all five native read-only
filesystem checks. The source world's hardlinked recovery commands remain
hardlinked within the independent staged copy; source inputs are not linked
into the writable stage or modified by overlays. All six image-building
commands and their log hashes are retained alongside the receipt.

The ESP uses512-byte sectors, one sector per cluster,32 reserved sectors, two
FATs and1009 sectors per FAT. The native and independent Linux checks bind the
same ESP SHA256
`6365214a707a5288e8e81c0baf3406d8d8725b341212be1540defb5b846fbc2e`.
The Linux checker preserved the image while reporting the free-cluster summary
as uninitialized; this is a retained diagnostic, not an applied repair.
The initially rejected image001 used invalid FAT geometry and is not promoted
to a successful image or boot result.

The FreeBSD kernel/world were source-built. The native C++ camera was
cross-built against that ARM64 sysroot and then tested on actual ARM64 FreeBSD.
Python, PyQt/Qt, Xorg and their runtime dependencies came from the declared
installed ARM64 FreeBSD package closure; **this is not an all-Qt/Python
source-built distribution**. The camera runtime profile separates explicitly
recorded SDK omissions from retained libraries, QML/plugins, Python resources
and license notices. It does not claim to deliver a full development SDK.

Source package metadata/checks were collected on FreeBSD ARM64. The transported
retained bytes were separately checked against their declared pkg checksums,
SHA256 and static ARM ELF dependencies on the FreeBSD amd64 build host. The
receipt preserves `source_host=arm64`, `verification_host=amd64` and
`collection_kind=offline-verified-transport`; that static validation is not
target execution. It binds source provisional SHA256
`bc5320512b771ea23b4eed8638b94036ebc533a9ed9bbe66f574cb75feb1330e`
and runtime manifest SHA256
`c4af37223ca9a1bcc4b2ad3506f95711b06b92e3934ddd4eeb4330c21b0f289b`.
The source/verifier module hashes and package license/omission inventory remain
in the corresponding manifests. Package/repository signature verification is
not newly attested by this collection process.

## Display diagnosis and accepted image003

The earlier valid image002's first display profile attached `virtio_gpu` to
VT. Its [actual Xorg log](../../build/DaOS_BSD_Unix_ARM64_R10/firstboot-failure/DaOS-Xorg.log)
shows successful SCFB probe/preinit at1280x800, followed by framebuffer mmap
failure and `AddScreen/ScreenInit failed`. The
[kernel context](../../build/DaOS_BSD_Unix_ARM64_R10/firstboot-failure/kernel-context.json)
and [camera session log](../../build/DaOS_BSD_Unix_ARM64_R10/firstboot-failure/DaOS-camera.log)
retain the actual backend and failed session. The pinned virtio-GPU VT driver
has no framebuffer mmap implementation, while SCFB requires it; a loaded
driver and a process-start marker alone therefore did not prove a usable GUI.

The next display-profile boot used RAMFB and actual `efifb` at800x600. Xorg and
the UID1000 native Qt application rendered, but the
[whole-screen image](../../build/DaOS_BSD_Unix_ARM64_R10/intermediate-002/boot002-first.png)
exposed a viewport crop: the default1280x800 QML window had not resized on the
800x600 X server without a window manager. Right-side controls/footer were
outside the visible screen, so this was not accepted as a usable kiosk.

Image003 includes launcher SHA256
`1cc455fc1f995f3f0ac99843792a9bf379a0fcc8eae9e63add635c95cd40c474`.
Only kiosk startup now sets the actual QScreen geometry before requesting
fullscreen; Main.qml, the controller and native protocol were unchanged.
Separate Windows-host checks rendered the complete800x600 kiosk and retained
the1280x800 desktop behavior. Those host checks support the narrow sizing fix
but do not stand in for FreeBSD display evidence.

The [first actual boot observation](../../system/freebsd/image-boot/evidence/image003-first/observation.json)
and [controlled restart observation](../../system/freebsd/image-boot/evidence/image003-resume/observation.json)
both completed with exit0, the kernel's `All buffers synced.` marker and
unchanged source image, UEFI code and immutable backing inputs. The first run
lasted797.211s; the resumed run242.354s. The source-built raw hash remained
`69c824a3aa1f992fcb38e285145e4549c76107cab95a2a26e2e391e401b4942a`.
The child used a new private qcow2 layer backing the stopped parent, not a
fresh pristine camera disk. Both used Cortex-A72 ARM64 emulation,4GiB RAM,
RAMFB/efifb and Qt/xcb. Neither had a NIC, SSH, NoCloud, injected key or host
share. These are whole-kernel virtual boots, not physical-board tests.

Human review of the full800x600 framebuffer confirmed all controls/footer
visible, including `DaOS BSD Unix`, FreeBSD15.1-RELEASE-p6 and `arm64`.
Synthetic live preview started. One explicit150ms shutter press saved request
`afb981a3-b924-4d55-88be-6402189c2242` at requested10100us/gain1.0 in
`sim-small`128x96 mode. The UI showed COMPLETE, its exact ID/path and retained
DNG display. After the full kernel restart, the same setting and original
completed request appeared without another shutter action. GUI export then
reported25662bytes to a new `/var/tmp/ff1-review.dng` file.

The [human-acceptance receipt](../../system/freebsd/image-boot/evidence/image003-ui-acceptance.json)
binds eight actual screenshots and both observation identities. Review
[the local gallery](../../build/DaOS_BSD_Unix_ARM64_R10/UI-review.html),
[live preview](../../build/DaOS_BSD_Unix_ARM64_R10/screens/03-live-held.png),
[resumed original details](../../build/DaOS_BSD_Unix_ARM64_R10/screens/12-restored-details.png)
and [export result](../../build/DaOS_BSD_Unix_ARM64_R10/screens/15-export-complete.png).
PNG conversion retained every RGB pixel; no framebuffer image was redrawn or
cropped. Automated observations correctly retain `UI_readiness_claimed:false`;
human acceptance is an additional record, not a rewrite of old receipts.

The initial immediate mouse down/up helper could coalesce within USB polling;
explicit bounded150ms held input worked. The initial unchanged-frame attempts
are retained and not labeled successful live/shutter evidence. There was one
shutter action in the first boot and zero in resume, with no blind capture retry.
Source-only follow-ups to operator click timing and the legacy HTML console's
`DaOS` spelling are distinguished from the frozen tested image in the
[delivery guide](../../system/freebsd/DELIVERY.md).

Independent read-only forensic validation of the stopped parent and child
disks found exactly one complete capture in both. The entire original capture
row, UI request ID and persistent settings remained unchanged. The
[restart/export receipt](../../system/freebsd/image-boot/evidence/image003-capture/independent-Windows-resume004.json)
confirms the retained DNG and actual exported `/var/tmp/ff1-review.dng` are
byte-identical25,662-byte files, SHA256
`34526aaace0136e38935822d974fa8d548060d0d5ea8ab8b726894b31f9529e0`.
Both actual Xorg logs show successful SCFB mmap/ScreenInit at800x600 and normal
exit0 on shutdown. The source raw image and stopped parent/child COW hashes
were unchanged by forensic extraction. Full scripts, tool provenance and
source/copy identities are preserved in the canonical evidence directory;
no journal database, DNG binary, key or runtime X authority enters the source ZIP.

The final source-only click helper uses a150ms hold and one finally release,
without retrying presses, including uncertain failure cases. Its separate unit
suite passed34/34 on Linux and23 on Windows with11 explicit platform skips.
The executed image003/004 manual input used an explicit held press with the
earlier archived helper; new helper tests are not retroactively described as
those VM actions. Source packaging passed20 targeted tests after narrowly
admitting the two exact canonical28.4MB runtime inventories under a32MiB cap;
other JSON remains capped at8MiB and all secret/privacy checks remain active.

Read-only `/etc` hostid/machine-id and entropy-save warnings remain in the boot
console. The virtual RNG supplied fresh host CSPRNG bytes; no shared seed was
baked into the source image. These tests do not establish physical entropy,
power-loss safety, board-specific recovery or update trust. Physical sensor,
driver, optical, thermal and measured calibration acceptance remain open.

## UI review and retained capture

The reference ARM64 guest used Python3.12.15, Qt5.15.19 and PyQt5.15.11 as
UID1000. Its runtime-derived label was
`DaOS BSD Unix · FreeBSD 15.1-RELEASE-p6 · arm64`. Case-sensitive module paths
and historical machine hostnames are evidence identifiers, not UI branding.

The full Qt interaction [test results](../../system/freebsd/arm-vm/evidence/ui-test-r10-001/python-tests.json),
[execution log](../../system/freebsd/arm-vm/evidence/ui-test-r10-001/ui-tests.log)
and [runtime identity](../../system/freebsd/arm-vm/evidence/ui-test-r10-001/qt-runtime.json)
are separate from first/resume review. All42 staged source hashes and the
original native binary were checked before and after this isolated QTest run.
Seven test-specific screenshots are retained in the build companion, outside
the source archive; they do not replace the five review-gallery images below.
All these UI results remain offscreen reference-guest evidence.

Review [the screenshot index](../../build/freebsd-arm64-ui-r10-001/index.html):

| Screenshot | Dimensions | Actual content |
| --- | --- | --- |
| [01-live.png](../../build/freebsd-arm64-ui-r10-001/01-live.png) | 1280x800 | Synthetic live preview and manual exposure controls. |
| [02-saved.png](../../build/freebsd-arm64-ui-r10-001/02-saved.png) | 1280x800 | Confirmed capture with immutable request/storage details. |
| [03-gallery.png](../../build/freebsd-arm64-ui-r10-001/03-gallery.png) | 1280x800 | Display conversion read from the original retained DNG. |
| [04-compact.png](../../build/freebsd-arm64-ui-r10-001/04-compact.png) | 800x600 | Compact camera layout. |
| [05-restored.png](../../build/freebsd-arm64-ui-r10-001/05-restored.png) | 1280x800 | Same original capture and persisted settings recovered. |

The first run made one shutter action; resume made zero. Both report exactly
one journal row, request/capture/frame identity
`e80344a4-718e-436e-b146-6efee5443d23`, requested and actual exposure12500us,
gain1.0 and persistent `sim-small` settings. Original and exported DNG bytes
have identical SHA256. Both reports confirm the native process closed and
explicitly set physical-camera/display validation false. These results prove
retained synthetic capture behavior in that guest, not yet the new image's
boot or recovery behavior.

## Evidence identities and remaining release gate

These hashes identify the actual files inspected; they are content
commitments, not signatures or a claim of bit-for-bit rebuild reproducibility.

| File/artifact | SHA256 |
| --- | --- |
| Base-build receipt | `49eccaf40d4754857d2b07b4e3f31d524516462befff0ac3dbbe6651ffefa71b` |
| Collected cross-build receipt | `49df9748c8dea15005e9721b3e33426b932ef343603837336c63b4f2560eb69c` |
| Collected actual-target receipt | `b612d6fa0e822b608abd1098030a6596267f4dd87f99ada28983e72283e79ad1` |
| ARM64 `hf-camerad`, identical in test and both UI reports | `be752fd42c631e763682fa96a34b929a2bd80b5ae9ab3eb1b6f17653bc31bc28` |
| Original/exported synthetic DNG | `e4149164bccb5d271b75c7f8507b592ea20392ad0c77fabfd98fe47a169b8831` |
| First UI report | `1c3bbe3d88aad695ec2ad972e0e1e45e66bc04c9dd479a3bcb6cacaf61164e9f` |
| Resume UI report | `c8ee70bfd7d4726db97759d58c69aa0080cb9f9dec34ddb6b0f99b191a5a0ee8` |
| Restored screenshot | `256ec92daabc6ba661d7e10ba53cccf8a6c2722c2e061a5b758e1b9d194f81e6` |
| Independent DNG decode report | `ca97557d723682775a7cdbc4fbbed200c6d9582c12540710ccb1ad7d41f1c55f` |

[Copy receipts](../../build/freebsd-r10-evidence/copies.json) retain transferred
file sizes/hashes. The native quartet and remaining binary identities are in
the cross and target receipts; the screenshots' five identities are in the UI
reports. Earlier failed preflights remain in `build/`: they exposed an
incorrect `/usr/lib/libc++.so.1` assumption and an overstrict calibration-path
string comparison. The final builder validates actual `/lib` C++ runtimes and
the target-contained linker script; target validation accepts CMake's genuine
`native/../calibration` spelling while rejecting links, escapes and extra args.
Failed runs were not promoted into passing receipts.

Canonical JSON evidence is retained under `system/freebsd/` for the source
handoff. The separate UI-review folder supplies the actual PNGs and DNG; those
are intentionally outside the source-only ZIP. Source-bundle privacy normalization
records original and delivered hashes for task-specific workspace-prefix redactions.

Image003's exact source/manifest identities, filesystem admission, visible UI,
retained native synthetic capture/export and controlled full-kernel restart
are now recorded separately above. Prior R9 Linux A/B model tests do not
establish FreeBSD bootloader/update recovery: BSD A/B integration remains
unimplemented. The delivery is a **source-built ARM64 FreeBSD-derived camera
appliance with tested synthetic capture and restart**, not a physical camera,
all-dependencies source build, complete original DaOS framework integration or
manufacturing release. Follow the [delivery guide](../../system/freebsd/DELIVERY.md)
for exact cache/tool prerequisites and known limitations.
