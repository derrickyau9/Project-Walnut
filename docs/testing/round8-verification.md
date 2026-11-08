# Round-eight requirements audit and camera correctness

Date: 2026-11-05. Scope: actual software/synthetic camera behavior on Windows,
Linux and a generic ARM64 virtual system. R7/R6 ZIPs remain byte-identical.

The [requirements matrix](requirements-status-r8.md) covers all fourteen original
chapters, G0-G7 and release tests, linked to actual code/test evidence. It
distinguishes implemented software, narrower substitutes, feasible missing
software, vendor/publication dependencies and unperformed physical work. The
[original user text](../product/original-user-specification.md) is preserved as
requirements, not proof of implementation. No directory, document, CAD render
or unexecuted test is counted as a working camera feature.

## Resulting behavior

- Standalone basic CLI capabilities/status/list/asset/export works with reference
  and native authorities. Actual subprocess tests block all DaOS imports while
  capturing and exporting; retained bytes are verified and never overwritten.
  Incompatible backend arguments fail before creating camera data. See
  [basic usage](offline-basic-round8.md).
- Reserved workflow request IDs are reconciled before cancellation settles.
  An absent/mismatched/unqueryable result stays unknown. Local OS journal
  ownership prevents another manager from marking a live job interrupted.
  Saved-plan resume/pause/cancel/list and legacy bad-terminal repair are tested;
  clean pauses preserve intervening controller settings. See
  [workflow evidence](../../daos/capability_runtime/evidence/round8-workflow.json).
- Retained-DNG interpretation now resolves CFA plane indices to their color
  meanings, accepts equivalent RGB permutations and rejects unsupported CFA
  geometry/linearization transforms. Overflowing JSON floats are rejected.
  Actual LibRaw mutations independently establish the color mapping behavior.
- Native and reference capture publication now bind the produced file and
  storage identities. Observed directory/staging/file replacement cannot be
  treated as a normal completed photograph or used to clean foreign files.
  Post-publication ambiguity stays unknown. See the
  [receipt decision](../adr/0005-capture-file-receipts.md) and
  [native report](../../camera/native/evidence/round8/REPORT.md).

## Exact verification

| Execution | Result and boundary |
| --- | --- |
| Windows full regression | 428 tests / 102.048 s: 405 pass, 23 explicit platform/tool skips. Native programs, Qt, full 6064x4040 synthetic I/O and both optional independent decoders were available. |
| Final Windows delivery checks | 16 pass, zero skips. One delivery-only regression was added after full-suite discovery, so this separate current run covers it; these overlapping counts are not summed. |
| Linux critical + delivery subset | 120 pass, zero skips / 23.805 s. Covers actual symlinks, staged-file substitution, both offline authorities, DNG, workflow recovery/continuation and packaging. |
| Targeted native checks | Windows CTest 2/2; Linux 89 core + 90 authority assertions. The native report preserves 33 focused Python checks, all passing with zero skips. |
| Fresh ARM64 build | Actual current C++ sources cross-compiled and executed under explicit QEMU user mode, then included in the new image. Source, ELF and artifact checks passed. |
| Full ARM64 Linux first/resume | Both passed, 28.909 s / 25.890 s; normal ARM Python 3.13.5 and Qt5/PyQt execute as UID1000. |
| Independent fresh guest DNG read | tifffile 2023.4.12 and rawpy0.28.0/LibRaw0.22.2 each match all 12,288 Bayer samples to the separate Python oracle; RGB96x128x3 renders. Synthetic128x96, not a sensor test. |

Windows skips cover POSIX/Linux process/symlink/FIFO/preload checks and unavailable
Debian extraction/signature/cross-tool facilities. The relevant capture-path
cases were run on Linux; actual Windows junction checks are separate in the
native report. Full source/head results are preserved, including initial fixture
failures. One existing fake-clock test released a real-clock lease after moving
the clock to100s; this depends on Linux boot uptime. Release now occurs before
the clock substitution. A new replacement fixture originally renamed an open
file, which Windows properly refuses; substitution now occurs after writer
close. Neither correction weakens the production guards or skips a decoder.

## Actual ARM offline continuation

The new image runs the existing real QML controls, followed by separate native
CLI and workflow sessions. First phase creates one basic DNG with all DaOS
imports blocked, lists/queries/verifies/exports it, and completes a two-capture
workflow. Resume uses the saved UUID/job and checks original bytes; it issues
no new capture in those sessions. Qt independently recovers its own one-photo
session. The three journals contain one, one and two completed captures.

- Qt UUID: `b86572e6-c679-4079-8f3d-2d2c5c1239bb`.
- Basic UUID: `abd646a3-77e6-4063-97cf-a6e557eee799`, SHA256
  `2bed91cab1738f3be5406b3990924c888accfd68803e4d1d5b72fb62f4f568ae`.
- Workflow: `offline-5f2535c7efde4ef49b51eb0312f17236`; both DNG hashes and the
  job identity remain unchanged across the second boot.
- Read-only system image SHA256, unchanged through both boots:
  `675c2212ef7a893ef7525c3137848c752bf23837ccabe5e1823b438159ab5882`.

The completed recipe binds82 source inputs. Original commands, reports, hashes,
logs and decoder output are under [R8 artifacts](artifacts/round8/). Evidence
copies in the source ZIP redact private task paths while preserving original
hashes in its manifest; original local evidence and source/import bytes remain
unchanged. Generated disks, binaries, runtime photos and non-import screenshots
are excluded. This is one fresh R8 image; R7's four-build repeatability result
belongs to its own inputs and is not silently applied to R8.

## Remaining work

The matrix explicitly identifies feasible software gaps: production calibration
activation/processing/schema, persistent device settings, deterministic input
integration, stage-aware capture progress/recovery, virtual-target A/B install/
boot/health/rollback and reproducible target CI. These are not all blocked on
sensor procurement. The generic Debian binary runtime is not a production
Buildroot/BSP or an upstream source rebuild.

Physical work additionally needs authorized sensor electrical/register/RAW14
data and publication rights, supply/evaluation hardware, selected SoM/MCU/display/
power interfaces, open SLVS-EC/FPGA/DMA/V4L2/libcamera integration, real sensor
package/optical stack and mount geometry, thermal closure, measured calibration,
media/power-cut/endurance tests and an independent manufactured replica. No
physical acceptance gate is promoted by these software results.
