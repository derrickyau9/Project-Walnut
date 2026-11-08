# Round-five clean-source verification

Date: 2026-11-05. This report covers the independently reviewed software camera,
decoded-pixel RTL and integrated prototype sources. It establishes no real sensor
capture, manufacturable camera, measured image quality or physical recovery.

## New verified fixes

- Reference/native capture publication preserves late conflicting files; cleanup
  removes only owned staging entries. Durable request IDs and unknown outcomes
  retain their no-blind-retry semantics.
- HTTP rejects ambiguous framing, short bodies, invalid targets, duplicate JSON
  keys, nonfinite numbers and malformed Unicode before dispatch. Request handling
  has a total deadline. Extra route segments cannot read/cancel a valid record,
  and an unavailable native authority produces structured GET errors.
- Windows single-configuration CMake builds respect the selected build type.
  Linux packaging binds current native source/recipe hashes to tested programs,
  refuses stale sources and validates package IDs before creating output.
- RTL provenance follows the actually selected executable locations and hashes;
  unrelated cached tool metadata cannot be attached as their provenance.
- Source handoff excludes runtime/private files and non-import screenshots,
  redacts personal prefixes in evidence copies (including JSON keys), refuses
  redaction collisions and verifies source policy as well as checksums.

See the independent [security/crash audit](security-crash-audit-round5.md),
[build/boundary audit](build-boundary-audit-round5.md) and
[handoff/privacy audit](handoff-audit-round5.md) for reproductions, targeted
regressions and remaining scope. These targeted runs overlap the full suite.

## Fresh extraction and test results

A candidate source ZIP was verified and extracted into a new temporary directory.
Native programs were built there from an empty build directory with Visual Studio
2022 C++17 x64 Release. CTest passed **2/2**, covering **171 native assertions**.
MSBuild emitted its standard temporary-directory incremental-build warning;
this was a new full build, not reuse of an existing workspace build.

The complete Python integration suite ran from that extraction using its newly
built native executables, full-frame DNG testing, offscreen/software Qt and actual
Icarus tools: **261 tests in 65.709 seconds; 259 passed, 2 POSIX-only skips**.
The Linux tests below exercised those skipped paths. The full run includes all
nineteen actual Qt/QML tests, strict HTTP regressions, native process crashes,
same-ID reconciliation, workflows, DNG decoders, provenance and source-bundle
privacy tests. [Captured log](artifacts/round5-clean-suite.txt).

Eight separate arithmetic/decoded-frame reference-model tests also passed.
All five original hardware ZIPs and 313 extracted children passed the clean-copy
hash/static check. Vendored dependency checks, `qmllint`, Node syntax and Python
compilation passed. [Supplemental log](artifacts/round5-supplemental.txt) and
[fresh native build log](artifacts/round5-clean-native-build.txt).

The actual decoder environment was Python 3.12.7, NumPy 1.26.4, tifffile
2023.4.12, rawpy 0.28.0 and LibRaw 0.22.2. A combined test/UI requirements resolver
check also succeeded on Python 3.12. Direct dependency versions are pinned;
transitive wheel hashes/platform runtimes are not a complete offline lockfile.

The final source snapshot's implementation/configuration inputs were compared
byte-for-byte with that tested extraction. The machine-readable
[verification record](artifacts/round5-verification.json) lists those input
hashes, the clean-build candidate hash, fresh native hashes and exact test counts.
Final documentation/evidence additions do not change those tested inputs.

## Actual Linux and RTL work

A separate Debian x86_64/WSL GCC 14.2 build compiled the frozen native sources and
passed **171 native assertions**. Linux helper groups passed **76 Python tests**
(18 daemon, 16 client, 19 HTTP client, 20 host, 3 build helper); a separate strict
HTTP security run passed **9**, for **85 executions, zero skips**. These overlap
Windows cases and must not be added as unique cases. The source-bound
[Linux build evidence](../../system/linux/evidence/round5-build-evidence.json)
records the actual commands, input hashes and executable hashes.

The nonroot [packaged smoke](../../system/linux/evidence/round5-deployment-evidence.json)
passed capture, clean shutdown/restart, same-ID query, read-only diagnostics,
dedicated trial health confirmation and finite-budget fallback. Systemd user-unit
syntax verification passed; no service was installed/enabled. This is a host A/B
state model, not a bootloader or image updater.

Six real Icarus configurations passed after provenance repair. The logical RTL
preserves accepted pixels, IDs, counters, backpressure, error/abort state and
completion-gated checksums. Bayer-to-RTL-to-DNG tests and independent tifffile
reads passed in the clean full suite. No PHY, SLVS-EC, packing, CDC, DMA,
synthesis, timing closure or bitstream is validated.

## Handoff and remaining gates

[HANDOFF.md](../../HANDOFF.md) is the entry point for running/rebuilding the
delivered project. `SOURCE-MANIFEST.json` inside the ZIP is the inventory of
actual delivered bytes, provenance and rights status. Redacted evidence copies
retain their original source hash and a transformation note. Browser/native UI
screenshots and runtime data are excluded; original hardware CAD/PCB renders and
all supplied prototype archives remain unchanged. Historical workspace evidence
can reference excluded images or original hashes without claiming a new run.

No high-confidence credentials/private personal files were found by the scoped
source/archive review. The inventory is not an authenticity signature or a proof
against a hostile account-level filesystem writer. This local development API
does not supply production authentication or Internet exposure protection.

Project license choices remain proposals pending rightsholder confirmation;
third-party MIT/public-domain/KiCad notices retain their own scope. No existing
DaOS source was copied and no repository was published. Remote CI has not run.

Real sensor documentation/initialization and publication rights, receiver and
board interfaces, drivers, ARM BSP, target MCU, mount/thermal manufacture,
calibration, physical power-cut/endurance testing and independent fabrication
remain open in the [release matrix](release-matrix.md) and
[hardware overlay](../../hardware/interface-review.md).
