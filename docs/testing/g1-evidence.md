# HF-Sim G1 verification evidence

This page preserves the **round-four verification snapshot**. The later
[round-five clean-source verification](round5-verification.md) records the
independent security/build/handoff audit fixes and final extraction/build run.
Historical counts and executable hashes below describe that earlier snapshot.

Verified on 2026-11-05 in the authorized Windows workspace, Python 3.12.7 and Node 24.19.0, and genuine Debian x86_64 Linux in WSL, Python 3.13.5 and GCC 14.2.0. The ordinary command sandbox launcher failed while enumerating a BitLocker-locked H: volume; approved elevated command execution was used. This did not repair the sandbox and is not a hardware camera test.

## Automated results

`python -m unittest discover -s tests -v` ran **226 tests in 55.110 seconds: 224 passed, 2 POSIX-only tests skipped** in the final Windows integration run. Those two tests passed on Linux. The run selected the built worker/authority with `HF_NATIVE_WORKER` and `HF_NATIVE_DAEMON`, enabled `HF_FULL_FRAME_TEST=1`, used the isolated rawpy directory in `PYTHONPATH`, actual Icarus tools, and offscreen/software Qt. It includes the reference service, native worker and authority, bounded clients, actual Qt/QML controls, supervisor input model, DNG, preview, workflows, calibration storage, offline diagnostics, RTL integration and host recovery. The [complete captured test log](artifacts/latest-suite.txt) is preserved.

- Real second-process lock conflict and stale-epoch rejection.
- Actual subprocess termination before output, and immediately after DNG rename before completion commit. Restart reports unknown and a repeated request ID does not generate another file.
- Capture admission journal failure, thread-start failure, HTTP worker bounds and hostile Host/Origin rejection.
- Preview PNG CRC and independent Pillow decode, bounded latest-frame generation, exposure change and explicit mode-transition interruption.
- Bracket, interval, synthetic dark/flat, pause/resume/cancel, interruption reconciliation and lease renewal. The short-lease workflow unit test uses a controlled clock/service to verify scheduling calls, not a hard real-time Windows guarantee.
- DNG CFA pixels and metadata, full 6064x4040 dimensions and edge samples; LibRaw raw samples and rendered RGB.
- Persistent simulated disconnect, rejected new device operations while offline, and authorized reconnect while request queries remain available.
- Twenty offline calibration-store tests cover sensor binding, checksum/JSON validation, immutable concurrent imports, atomic exports, path and link rejection, and separation from a supplied system-image root. The store is not applied by camerad; capture IDs remain synthetic-uncalibrated.
- Native adapter/service/worker tests cover process cancellation/timeout, failed exits, malformed metadata, missing or mismatched outputs, final-commit ownership, exact Python/native pixels, native offline interval capture and independent LibRaw rendering.
- Native authority and client tests cover durable admission before worker start, leases/epochs, real OS process exclusion, actual kill and post-publication crashes, sticky journal-fault fencing, bounded stdio, and capture uncertainty without replay. Reference/native foreign-journal rejection is tested in both directions.
- Nineteen loopback HTTP client tests cover shared native authority, matched capture identity, dropped responses, bounded total I/O, malformed/oversized responses, redirect/proxy refusal and client closure without stopping the server. Native/client audit regressions additionally cover corrupted persisted metadata, SQLite embedded-NUL text, noncanonical epochs, invalid completion values, unsupported timeout limits and owned-child cleanup. See the [native audit](native-audit-round4.md).
- Two server lifecycle regressions confirm authority cleanup after bind failure and reject an invalid port before opening authority storage.
- Eighteen diagnostic tests and ten shared DNG inspector tests cover read-only source preservation, WAL/hot rollback journals, corruption, missing/orphan/unknown captures, workflow ambiguity, bounded TIFF structures and token suppression. An empty native lock-file regression verifies read-only Windows exclusion without growing the lock file.

Separately, `python -m unittest discover -s hardware/tools -q` and `python -m unittest discover -s fpga/testbench -q`: **4 tests each passed**. These eight are arithmetic and decoded-frame reference models. Together with the Windows suite there were **234 test executions: 232 passed and 2 POSIX-only skips**. The actual RTL simulations, native assertions and overlapping Linux executions below are reported separately rather than counted as additional unique cases. The [supplemental log](artifacts/supplemental-checks.txt) preserves the model, native and syntax checks.

`qmllint ui/on-device/Main.qml`, `node --check daos/console/app.js` and Python bytecode compilation pass. Vendored native dependencies match their recorded checksums. The GitHub Actions definition builds native code and installs optional Qt/test requirements, but no remote CI run was created.

## Actual decoded-pixel RTL simulation

[hf_capture_stream.sv](../../fpga/rtl/hf_capture_stream.sv) is an executable, single-clock logical stream implementation, accepting already-decoded 16-bit-container pixels. It carries frame IDs and markers through a bounded FIFO, supports valid/ready backpressure, validates dimensions/framing and reports sticky error, loss and abort state. Completion waits for the FIFO to drain and remains held until accepted. All pixels remain provisional until the matching good completion. A held but unaccepted pixel is not counted on abort.

Icarus Verilog 13.0 actually compiled and ran **six self-checking configurations**: 4x3 with FIFO depth 1/3/4, 1x1 depth 1, 1x4 depth 3 and 4x1 depth 3. They exercise pixel order, stalls, simultaneous transfers, malformed boundaries/IDs, errors on final beats, loss, abort, completion stalls and reset. Accepted-pixel FNV-1a-64 checksums include the final accepted beat and match the native-core byte convention. This is an accidental-corruption handoff checksum, not SLVS-EC CRC or authentication. Source hashes, simulator output and the official MSYS2 toolchain manifest are in [RTL evidence](../../fpga/testbench/rtl-simulation-evidence.json).

Two additional integration tests pass synthetic Bayer values through a real RTL simulation, compare every transferred sample and completion checksum, then independently decode the resulting DNG with tifffile. A corrupt completion refuses DNG creation even when provisional pixels were transferred. The Windows suite includes these two tests and two RTL runner tests. The [FPGA guide](../../fpga/README.md) documents isolated tool setup and exact scope. No sensor PHY, SLVS-EC, RAW14 packing, CDC, DMA, synthesis, timing closure or bitstream is established.

## Linux native build, packaging and recovery

The POSIX C++ implementation actually compiled with GCC 14.2.0 on Debian Linux in WSL. Both native test programs passed, containing **89 core and 82 authority assertions**, including POSIX lock, randomness, synchronization and publication code. Separately executed Python groups passed **17 native-daemon, 15 native-client, 17 Linux host and 19 HTTP client tests: 68 executions, zero skips**. These overlap Windows cases and are not an extra unique-case total. Commands, platform and binary hashes are preserved in the [build evidence](../../system/linux/evidence/build-evidence.json) and [final host safety evidence](../../system/linux/evidence/host-safety-evidence.json).

The actual nonroot packaged smoke test staged two immutable host releases, completed an HTTP capture, stopped/restarted the service, queried the same persisted completion and ran packaged offline diagnostics without changing camera bytes. A dedicated scratch health capture confirmed a recorded trial; unconfirmed attempt-budget exhaustion selected fallback metadata. `systemd-analyze --user verify` passed, but no user service was installed or enabled. See [deployment evidence](../../system/linux/evidence/deployment-evidence.json) and the [reproduction guide](../../system/linux/README.md).

Recovery state creation refuses recognized bundle/camera ancestry before any directory or SQLite write; regression checks preserve complete file inventories and hashes. Health checks require a new dedicated scratch directory. A disposable cross-OS lock probe observed exclusion in both directions, but persistent data must remain dedicated to one OS on its native filesystem; [the probe](../../system/linux/evidence/cross-os-lock-evidence.json) does not establish general DrvFS durability or cross-OS locking. The A/B implementation is a host state model only: no bootloader, partition, disk image, reboot or installed-release change occurs. Linux Qt/ARM body execution and real firmware rollback remain untested.

## Independent DNG evidence

rawpy 0.28.0 / LibRaw 0.22.2 was installed only in a Temp directory for validation. It opened a synthetic 128x96 DNG, recovered every Bayer value exactly, reported white level 16383 and black levels 64, and produced a 96x128x3 RGB image. tifffile 2023.4.12 and libtiff also read raw values independently. Earlier full-frame verification compared all 24,498,560 Bayer samples exactly, with a 48,997,650-byte file in the earlier metadata revision; the exact file size is metadata-dependent and is not a product format guarantee.

The color matrix and all calibration are synthetic. This evidence does not prove actual camera color accuracy, sensor sample precision, hardware throughput, complete Adobe DNG conformance, or compatibility with two photographic editing applications.

## Browser checks

Headless Edge exercised acquisition, synthetic preview start/stop, hidden/paused preview during capture, resumed preview, committed capture, storage-full failure, release, same-tab refresh and stale credentials after service restart. A deliberately shortened 12-second lease remained owned after 13 seconds with browser heartbeats. A three-exposure workflow completed through the console. Persistent simulated disconnect, release/acquire while offline, and explicit reconnect passed. Desktop and 390-pixel mobile screenshots were examined; mobile document width matched viewport width. Screen-reader testing was not performed. The preserved [browser smoke script](../../tests/ui/browser_smoke.py) was independently rerun successfully. The browser screenshot at `docs/testing/artifacts/console-mobile-390.png` is workspace-only verification evidence and is deliberately excluded from the source handoff.

## Native and imported-hardware checks

`python tools/build_native.py` configured and built the C++17 capture library, worker and durable native authority with Visual Studio 2022 / MSVC 19.44. CTest passed **2/2**, containing **89 capture-core and 82 authority assertions**. Real subprocess tests verify native admission, leases, exclusive ownership, file sync/publication, crash recovery and no replay. Both the native worker under Python authority and the separate native authority were exercised through independent capture and offline interval CLIs. No native pixel data were sent through JSON.

`python -m tools.diagnostics --data-dir <that stopped native service directory> --offline-only` returned a clean report with matching request/DNG identity and SQLite integrity. Source-preservation behavior is separately regression-tested.

`python -B hardware/imports/check_imports.py` independently passed static checks for five preserved Library archives and 313 extracted files, including active mechanical v0.3 with v0.1/v0.2 retained. Checks include PCB/netlist parity, shared STEP identity, supplied budget arithmetic, CSV/XDC consistency, mesh bounds and provisional keepouts. Supplied collision/service-path results remain supplied evidence. The thermal bridge is conditional and not a closed, measured sensor heat path. This is not a KiCad/CadQuery rerun or physical validation; see the [interface review](../../hardware/interface-review.md) for exact gaps.

## Device UI and supervisor reference

The actual Qt/QML launcher executed offline and saved a rendered window with native authority on Windows, using PyQt 5.15.10 / Qt 5.15.2. **Nineteen Qt tests pass**, loading actual QML controls and exercising native capture/cancellation, process abort/restart/unknown reconciliation, request-ID persistence and stale preview rejection. Audit regressions cover corrupt saved state and lossless bounded backup, malformed completion/state/lease metadata, short-lease heartbeat, bounded click queues, eventual reference shutdown and stale-client handoff. A shared native HTTP attachment test confirms UI shutdown releases only its lease without stopping the authority. An explicit acknowledgment allows a different new UUID while preserving the original unknown journal row; neither querying nor restarting restores this permission automatically. See the [UI audit](ui-audit-round4.md), [UI evidence](../../ui/on-device/evidence/validation.json) and [UI guide](../../ui/on-device/README.md). This is desktop software verification, not an ARM camera image or physical controls test.

The supervisor package has **19 host tests** for verified accessory digital input semantics, debounce, reset observation, unknown observations, clock validation and deterministic replay. It has no selected MCU, GPIO, hardware timer, sensor reset or power-control implementation. See [supervisor reference](../../firmware/supervisor-mcu/README.md).

## Remaining boundaries

G1 is a software contract prototype. Generated image data can run in Python, the C++ worker or the separate native authority; Windows Qt/QML photography and Linux x86_64 host deployment are executable. A decoded-pixel RTL path and host A/B recovery model are tested. Production device transport/security, ARM camera deployment, V4L2/libcamera, sensor receiver/CDC/DMA, target MCU firmware, hardware timing and a recoverable board image remain unimplemented or unverified. Actual arbitrary power-loss, sensor recovery, thermal behavior, mount alignment and long-duration hardware tests have not run. Sensor documentation, publication rights, selected hardware interfaces and fabrication are external dependencies. Imported concept files do not resolve those gates. See the release matrix and supplier gate before attempting hardware integration.
