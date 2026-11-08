# Offline diagnostics

`tools.diagnostics` inspects existing HF-Sim data without opening a camera service,
acquiring a control lease, changing capture status, or submitting a capture. It
never deletes, retries, repairs, renames, or completes a request. It needs only
Python's standard library and the repository's existing calibration validator.
Run it from the repository root:

```sh
python -m tools.diagnostics --data-dir var/hf-local --offline-only
python -m tools.diagnostics --data-dir var/hf-workflow --offline-only
python -m tools.diagnostics --data-dir var/hf-local --workflow-journal var/hf-local/workflows.sqlite3 --calibration-root /persistent/hf-calibration
```

The first argument may identify a direct `CameraService` directory containing
`journal.sqlite3` and `photos`, or a workflow CLI directory containing
`simulator/journal.sqlite3`. The tool automatically looks for the HTTP server's
`workflows.sqlite3`, a `workflow.sqlite3` beside the camera journal, or the
workflow CLI's parent `workflow.sqlite3`. `--workflow-journal` selects an explicit
journal instead. An omitted calibration root is reported as `not_requested`;
`--calibration-root` validates an existing store and never creates one.

JSON goes to stdout. Redirect it to a location outside the inspected data tree
if you want a saved report. Exit status is `0` for no findings, `1` for warnings
only, and `2` for one or more errors. Argument usage errors also use `2`.
Repeated inspection of unchanged files yields the same JSON; reports do not
include wall-clock generation times.

## What the report checks

- SQLite `PRAGMA integrity_check` and the expected capture/workflow query schema.
  An unreadable or invalid snapshot is reported explicitly, rather than opened
  through a service constructor that would perform recovery or state changes.
- Every canonical UUID capture's stored status, record/status identity, and
  recorded output path. A complete capture must have the expected
  `photos/<request_id>.dng`; paths must stay within `photos` and match that UUID.
- Final DNG and `.dng.partial` files, their request UUIDs and journal status, and
  orphan files whose UUID has no capture record. Unknown and accepted captures
  remain unresolved even if a final file exists. When a journal is unreadable,
  file relations are `journal_unavailable`, not asserted to be orphaned.
- Basic DNG structure through the shared bounded
  `camera.dng.inspect.inspect_dng` reader: classic TIFF tables and extents,
  direct uncompressed 16-bit RGGB CFA samples, required DNG tags, nonoverlapping
  strip/metadata ranges, and the namespaced request UUID. This reads metadata,
  not image samples, and is not a complete decoder, pixel-range check,
  independent image-quality check, or hardware-validation result. Unsupported
  DNG layouts are reported with invalid structures, rather than guessed.
- Workflow job/step states, unresolved original request IDs, orphan steps, and
  workflow `completed` steps without a camera `complete` record. There is no
  automatic resume or resubmission.
- Optional calibration packages using the same bounded JSON reader and
  `validate_package` checksum/schema/provenance validation used by the store.
  Sensor, package, and version must match their stored pathname. Valid results
  explicitly retain `hardware_calibrated: false`.

Reports use stable issue codes with `severity` and relevant allowlisted UUID,
job, step, or entry index fields. They do not copy raw request/record JSON,
control tokens, owner/control data, errors, workflow event details, package
reasons, arbitrary image descriptions, or unrecognized filenames into JSON.
The data-root path and valid package/job identifiers are included; review those
local identifiers before sharing a report.

## Snapshot and locking limits

The tool opens an **existing** `.service.lock` read-only and attempts the same
nonblocking OS lock as `CameraService`. When acquired, it holds that lock through
the scan and releases it afterward. It never creates a missing lock file or
writes its byte, takes over an existing service, changes an epoch, or modifies
lease state. A new service attempting to start during the scan may see the usual
`already_running` result until the scan finishes.

`--offline-only` skips journal/output/calibration inspection unless that existing
lock can be acquired. A missing/unreadable lock and a held lock are distinguished
in `consistency.service_lock`; lock failure alone does not prove a service is
running. Without the flag, the tool can inspect an active service, reporting
`service_may_be_active` and `uncoordinated_snapshot`.

Source database files are opened only for byte copying. Database, `-wal`, and
`-journal` bytes are copied to a disposable temporary directory; SQLite opens the
copy with `mode=ro`, `PRAGMA query_only=ON`, and one read transaction. SQLite's
optional temporary sidecars therefore cannot change the original database,
WAL, or shared-memory files. A hot rollback journal needing write recovery is
reported as `sqlite_recovery_required`; diagnostics does not recover either the
source or its copy. Before/after size, identity, and modification-time checks on
the database and its sidecars detect ordinary concurrent changes. A changed
copy is `inconclusive`, with `journal_changed_during_snapshot`.

Database copies, workflow records, calibration packages, and outputs are separate
observations. Even a `stable` copy is not a cross-file atomic snapshot.
Uncoordinated writers and filesystem races remain possible. Reports always carry
this caveat; an observation is never a safe-retry or exactly-once guarantee.
Symlinks and Windows reparse points in inspected paths are rejected. The tool is
an offline support aid, not a filesystem security boundary against a hostile
writer changing paths during inspection.

## Verification

```sh
python -B -m unittest tests.test_diagnostics tests.test_dng_inspect -v
```

Diagnostic tests exercise actual SQLite files, a live service with WAL records,
a subprocess crash leaving a hot rollback journal, an interleaved changed copy,
missing and corrupt DNGs, unknown/accepted/orphan/partial relations, workflow
ambiguity, calibration corruption, output containment, token suppression, CLI
exit status, and missing-root behavior. Ordinary scans compare file sets,
SHA-256 content hashes, sizes, and modification times before and after, including
source sidecars. The shared DNG reader additionally tests bounded metadata-only
reads, malformed/overlapping extents, Exif cycles, and big-endian TIFF input.
These are software fixture results only; they do not establish camera hardware
readiness or measured calibration.
