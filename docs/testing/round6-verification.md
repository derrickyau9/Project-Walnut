# Round-six verification and source handoff

Date: 2026-11-05. This work extends the preserved round-five source snapshot.
The project remains a software camera plus unvalidated hardware prototypes.
ARM64 instruction execution, a generic virtual Linux kernel and a physical
camera board are separate evidence levels.

## Delivered behavior

- Durable completed-capture pagination, pinned verified DNG reads, actual saved
  Bayer thumbnails and create-only export in native/reference APIs and SDKs.
  Binary media does not pass through JSON. See [retained-media evidence](retained-media-round6.md).
- Qt and console galleries, saved-image review/export, strict portable recipes,
  recent-job selection and offline recipe CLI execution. Separate bounded media
  work preserves control heartbeat during slow export. See [UI/workflow evidence](ui-workflow-round6.md).
- Four genuine static AArch64 executables with source/tool/artifact binding,
  actual QEMU user-mode tests and a separate verified ARM64 deployment package.
  Its normal runtime rejects non-ARM hosts and contains no emulator. See the
  [ARM64 guide](../../system/arm64/README.md).
- A full-system generic ARM64 Linux/initramfs target that runs native assertions,
  an actual daemon capture, shutdown/restart/same-ID and frame query, retained
  listing/asset metadata and bounded serial DNG retrieval. See the
  [virtual-target guide](../../system/qemu-virt/README.md).

## Final clean-source checks

The source-only staging directory excluded runtime data, build output and
non-import screenshots. A fresh Visual Studio x64 Release build passed CTest
**2/2**, containing **171 native assertions**. Then the complete suite ran from
that directory with its newly built programs, optional decoders, full-frame I/O,
actual RTL tools and offscreen/software Qt:

**318 tests in 86.688 seconds: 315 passed, 3 POSIX-only skips.**

The skips were the executable-symlink tool-selection case and two recovery
symlink cases; all were executed successfully on Linux. No missing native, Qt,
decoder or RTL dependency was skipped in this Windows run. All **133 final
implementation inputs** match the tested clean copy byte-for-byte. Final
documentation/evidence additions do not alter those inputs.

[Clean suite log](artifacts/round6-clean-suite.txt),
[fresh native build log](artifacts/round6-clean-native-build.txt) and
[machine-readable source/hash record](artifacts/round6-verification.json) are
preserved. Supplemental reference-model/import/dependency/syntax checks are in
[their log](artifacts/round6-supplemental.txt). The five original hardware ZIPs
and 313 extracted files remain unchanged; they do not become validated hardware.

An earlier clean run exposed an invalid test assumption: a healthy durable
acquire was still using the hostile-client **150 ms** budget. An independent
gated reproduction confirmed the server deadline could expire while a valid
operation completed exactly once. Only the fixture changed: hostile dribbles
retain their short budget/no-admission/slot-release checks, while the healthy
probe gets the normal budget and an intentional 220 ms delay. Windows EOF/reset
variants are accepted as closure. Production server code and retry semantics
were unchanged. Nine security tests and **50 consecutive stress runs** passed.
The [initial failure log](artifacts/round6-clean-suite-initial-failure.txt) is
retained. A new actual-HTTP regression also confirms an admitted capture survives
an expired reply, is queried by its original UUID and creates exactly one file
and journal row, with no replay.

## Linux, ARM64 and virtual-target evidence

| Execution | Actual result | Limit |
| --- | --- | --- |
| Debian x86_64 native | 171 assertions; 78 helper tests plus 48 additional tests, all passed, zero skips | Host Linux, not a board |
| ARM64 QEMU user mode | Four static EM_AARCH64 programs executed; 171 assertions, 20 RPC tests and four ELF/tool checks passed | Syscalls use the host kernel |
| ARM64 worker supplemental | Full 6064x4040 synthetic worker check explicitly enabled and passed | File/header/edge checks; no real sensor or frame-rate claim |
| Static ARM64 package | Nine package tests passed on Windows and Linux; staged host-Python/external-QEMU capture/list/PNG/DNG/restart smoke passed without changing release bytes | Actual ARM64 Python/board runtime not executed |
| Full generic ARM64 guest | 171 assertions; worker no-overwrite; real daemon capture/restart/same UUID and frame; list/asset queries; guest poweroff | RAM-only QEMU virt, not Zynq |
| Guest DNG independent read | All 12,288 samples match Python Bayer; tifffile and LibRaw decode; RGB 96x128x3 renders | Synthetic uncalibrated 128x96 data |
| Guest reproducibility/context | Two fresh init/initramfs builds are byte-identical; ordinary/non-PID1 invocations exit before guest-only actions | Same recorded inputs/toolchain, not hermetic cross-host attestation |

[Current Linux build](../../system/linux/evidence/round6-build-evidence.json),
[Linux additional results](../../system/linux/evidence/round6-extra-results.json),
[ARM64 evidence](../../system/arm64/evidence/), and
[full-guest evidence](../../system/qemu-virt/evidence/) preserve commands,
versions and hashes. The kernel was selected from authenticated Debian release
and package-index metadata; repeat downloads use its pinned package checksum.
No kernel was installed on the host, no network/disk/shared host filesystem was
attached to the guest, and no system service was enabled.

The earlier ARM worker CLI run had six passes and three explicit skips: the
full-frame opt-in and two optional decoders absent from Linux Python 3.13. The
full-frame case was subsequently run and passed separately. The decoder cases
were not run in that interpreter; independent decoding of the ARM guest's DNG
ran on Windows using NumPy 1.26.4, tifffile 2023.4.12, rawpy 0.28.0/LibRaw 0.22.2.
These overlapping runs are not added as unique project test totals.

## Snapshot, provenance and remaining work

The earlier Library item `libfile_ad6ff83270b081918bf50337cc5aea22` remains
unchanged, including SHA-256
`7e9acde4b075834600e4cc2ed9a2db01972ae27cf0f1891800b1e318c1aafe5b`.
Round six is a separate source revision, recorded in [the changelog](../CHANGELOG.md).
Source ZIPs omit compiled/tool/kernel binaries, runtime data and screenshots.
Original prototype sources and scoped license notices remain preserved;
personal prefixes are redacted only in delivered evidence copies, with hashes
tracked by the source manifest.

Project license grants, sensor documentation/publication rights and the physical
release remain open. No repository was published and remote CI did not run.
The [actionable hardware/release gates](round6-hardware-gates.md) identify the
required supplier information, interfaces, fabrication, calibration and tests.
ARM64 Qt/physical display, real sensor/SLVS-EC/FPGA/CDC/DMA, persistent media power
cuts, bootloader rollback, thermal/mount validation and independent manufactured
replication remain untested. Further virtual-media/platform software work is
possible; this report does not claim every remaining software task is blocked.
