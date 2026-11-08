# Native simulator authority boundary

Status: implemented and tested on Windows/MSVC and Debian x86_64 Linux/GCC, 2026-11-05. This decision describes a software simulator boundary, not hardware support or a production device release.

## Decision

Keep the Python `CameraService` as an executable reference, and add a separate C++ `CameraDaemon` authority using the same public capture semantics. The native process owns request admission, SQLite transactions, control epochs/leases, one asynchronous capture, DNG generation, file synchronization/publication, and completion records. A Python adapter must not repeat these decisions on its behalf.

Each authority locks its data directory and records an authority marker. A native journal is rejected by the reference service, and a reference journal is rejected by the native authority. No silent migration or switching backend to resolve a failed capture is provided. Use independent directories for comparison.

Admission commits immutable parameters and the UUID before starting a worker. Repeated identical UUIDs return the original result; a conflict is rejected. Startup converts unfinished records to unknown. A final file surviving a process crash is not itself a completion acknowledgement. Query its original UUID and inspect evidence rather than triggering another capture.

Control uses OS-generated random tokens, persisted increasing epochs and monotonic leases. Expiry fences new commands; an accepted capture still owns the capture slot until it terminates. Cancellation is a request observed by the capture path and is never represented as guaranteed merely because the caller sent it. Journal errors fence mutations until restart/reconciliation.

## Interfaces

The native process uses bounded JSON-line stdio RPC for control and metadata only. The process-owned client has finite write/read timeouts; losing a capture response raises outcome_unknown and never retries. The HTTP adapter multiplexes local console/workflow clients over one native process. The Qt/QML default starts a native process directly, so basic photography has no dependency on HTTP or workflows. The optional local HTTP client supports attaching UI clients to an existing authority.

Preview remains an explicitly separate synthetic renderer. A file path and completion state come from the authority. There is no Bayer image array in RPC JSON and no model invocation in capture.

## Verification and remaining limits

Tests cover actual second processes, stale credentials, lease expiry, worker startup failure, SQLite commit failure, kill after admission, kill after file publication, recovery, cancellation, transport loss and independent DNG decoding. The reference and native paths remain separately runnable, and native HTTP/CLI/workflow/UI integration has executed tests.

The POSIX authority was independently built and exercised on Debian x86_64 Linux in WSL, including actual process exclusion, native admission/crash tests and packaged capture/restart health checks. See the [Linux reproduction guide and evidence](../../system/linux/README.md). Persistent camera data must be dedicated to one OS on its native filesystem; a contained cross-OS lock probe is not a general shared-filesystem guarantee.

This is not a native device driver or board image. Sensor/FPGA/DMA/libcamera adapters, device-scoped physical identity, real watchdog/reset channels, target power sequencing, production authentication and device transport remain integration work. Private stdio is not a multi-client native local-socket protocol; shared frontend access currently uses the loopback HTTP adapter, which is a development transport. A real board must supply independently reviewed timing, input, power and recovery interfaces. Windows/Linux process-crash tests do not establish arbitrary power-loss integrity, real-time behavior or ARM camera operation.
