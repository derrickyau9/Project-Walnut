# Round-nine persistent state, calibration provenance and virtual recovery

Date: 2026-11-05. This is an independently runnable synthetic camera and an
engineering prototype collection. No physical FF-1, sensor interface, optical
mount, measured calibration or manufacturing release was validated.
R6-R8 archives remain byte-identical. Read the
[requirements delta](requirements-status-r9.md) and the historical
[full R8 matrix](requirements-status-r8.md) for every original requirement.

## Resulting behavior

Both camera authorities now persist an exact versioned device baseline and
active calibration selection in their durable journals. Configuration commits
before the service confirms it or changes memory. Explicit per-capture
overrides leave that baseline unchanged. Partial or corrupt stored metadata
fails closed, before advancing the startup epoch; it is not silently reset.
The independent CLI supports configure/bind/clear, and omitted capture flags
inherit the persisted settings. Qt coalesces edits asynchronously, waits for
confirmed readback and holds capture while settings are pending or uncertain.

[Calibration bindings](../../camera/calibration/BINDING.md) contain an exact,
detached package payload and two checked SHA256 identities. The authorities
validate the package independently and require a live idle control lease.
Accepted captures pin the full artifact as an explicit version-2 record;
changing or clearing the current selection cannot change an old UUID or cause
it to execute again. The default unbound record remains version 1. The native
implementation includes its own SHA256 vector and strict package validators.
This is synthetic metadata selection: `hardware_calibrated` and
`pixel_corrections_applied` remain false, and Bayer values are unchanged.
Hashes detect content changes; they do not authenticate a calibration author.

Review reproduced a reference journal accepting contradictory UUID, exposure
or SQL/JSON status context. All read paths now validate the immutable request
and SQL identity/status, fence corrupt state and retain original rows/photos.
Clients validate present calibration summaries and nested frame evidence. Only
actual HTTP404 plus `not_found` means absence; HTTP500 with that code is an
error, never permission to recapture. Legacy workers may omit an optional
nested capture ID; present identities must match. A valid worker result with
an unconfirmed database commit can remain **unknown**, withholding outer frame
metadata; evidence does not promote that result to completed or trigger retry.

The [A/B model](../../system/update-model/README.md) now selects and actually
boots regular ARM64 image clones, consumes trial budgets durably before launch,
requires explicit matching health confirmation and returns to the confirmed
image after failed trials. A bound-data profile checks actual saved settings,
the full selected artifact, original capture UUID/record and DNG, alongside
separately labeled preservation fixtures. It uses the same application code
with artificial release versions. Different native binaries are refused even
if they claim the same ABI. This does not implement cross-version migration,
authenticated update distribution, anti-rollback or a real bootloader/flash.

## Exact current-source verification

| Execution | Actual result and scope |
| --- | --- |
| Windows full suite | 527 tests / 120.588 s: 490 passed, 37 explicit tool/platform skips; native authority/worker, Qt, full synthetic I/O and both independent decoders available. |
| Linux full suite | 500 tests / 71.998 s: 477 passed, 23 explicit optional-tool/configuration skips; actual nonroot Debian, native C++, filesystem/process/descriptor guards and image/update tool checks. |
| Native checks | Four CTest targets: 89 core + 126 authority + 21 SHA256 + 325 calibration checks. Native process subsets and static ARM64 execution are recorded in the [native evidence](../../camera/native/evidence/round9/native-settings-binding.json). These overlap host runs and are not added into a false total. |
| Final generic ARM64 target | Current-source offline build, actual Linux/Python/Qt first and resume boots passed as UID1000; independent basic capture blocks all DaOS imports. Exact source/artifact commitments and timings are retained in [ARM evidence](artifacts/round9/arm64/run-state.json). |
| Independent final guest DNG reads | Qt10100us and bound basic11700us captures each match all 12,288 Bayer samples in tifffile2023.4.12 and rawpy0.28.0/LibRaw0.22.2 against the separate Python oracle. Both render 96x128x3 RGB. These are synthetic128x96 images, not measured color performance. |
| Same-image bound A/B integration | Three actual ARM64 boots passed: A healthy, B deliberately unhealthy with one-attempt budget, then actual A fallback. The [final report](../../system/update-model/evidence/bound-r9-final/REPORT.md) pins the same final image and six preserved data hashes. Earlier six legacy and three bound-data boots remain separate historical runs. |

Windows skips are explicit POSIX/Linux filesystem/process/preload, Debian
package/signature/image and nonroot Linux update facilities. Linux lacks the
optional Qt/RAW decoder/Schema/RTL environment used on Windows; some optional
UI classes are not discovered when Qt is absent. Actual ARM Qt runs provide a
separate executable deployment check. Counts therefore differ; a skip is never
counted as a pass. Original failed runs are preserved under `historical/`:
they exposed the overstrict optional-evidence validation, a deliberate malformed
diagnostic fixture, POSIX scandir-FD mocking and an idle-state timing fixture.
The final runs above contain no failures and retain the actual unknown path.

The final image creates three independent sessions: Qt (one capture), basic
CLI (one bound capture) and saved workflow (two captures). Resume verifies the
same identities and bytes rather than submitting new shots. The basic session
persists11700us and the exact selected artifact across native authority and
guest restarts. System root is read-only; the separate writable data image
unmounts cleanly and passes read-only fsck. No host mount, root access, network
share, physical display or camera board is used by these boots.

Original reports/logs, source hashes and decoder results are retained under
[R9 artifacts](artifacts/round9/verification.json). Source-bundle evidence copies
redact private task paths and preserve original hashes in the manifest.
Binaries, generated disks, runtime photographs and credentials are excluded.
Imported prototype hardware retains its original identity and bytes.

## Architecture and remaining gates

The [DaOS assessment](../architecture/daos-relationship.md) is explicit: zero
existing DaOS source files are imported. The current plain-JavaScript console,
Python workflow runner, new camera JSON Schemas, C++ authority and Qt/QML body
application independently implement capability/checkpoint/control ideas.
The original React App Shell and TypeScript schema/engine/control have not
been extracted or connected through an upstream adapter. Applicable source
ownership/licensing boundaries still need resolution. This is a camera-domain
prototype informed by DaOS, not completed integration of the requested framework.

Feasible software still missing includes calibrated-processing schemas and
algorithms, full console calibration management, stage-aware supervision and
recovery budgets, removable-media lifecycle, source-bound target CI and
production update/BSP integration. Hardware additionally requires documented
sensor electrical/register/RAW14/publication rights and supply; an open receiver,
real FPGA/DMA/V4L2/libcamera path; selected board/MCU/display/power signals;
verified package/filter/image-plane/mount geometry; thermal closure; real
color/black/defect/shading calibration; media/power-cut/endurance and two-mount
optical testing; and an independent manufactured replica. Current CAD/control
PCB artifacts are prototypes and do not satisfy those gates.
