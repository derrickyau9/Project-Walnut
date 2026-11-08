# Retained capture listing, playback and export — round 6

Date: 2026-11-05. This is new work after the preserved round-five snapshot. The
snapshot archive and round-five reports were not modified. ARM build files and
UI implementation/evidence belong to their separate owners.

## Confirmed V1 gap and resulting behavior

The prior camera API supported one request lookup and a separate generated live
preview. It had no retained capture list, file download, or display conversion
of a saved DNG. The console and body UI could show a final path, but another
frontend could not safely browse or retrieve retained images through the API.

Reference and native authorities now expose a durable completed-capture page,
asset metadata, verified file reads, actual retained Bayer thumbnails, and
create-only client-local export. None of these operations creates/replays a
capture, changes its UUID, or requires/acquires a control lease.

## Contract

| Interface | Behavior |
| --- | --- |
| `list_captures(limit=20, cursor=None)` | Metadata-only page, maximum 50 completed items, newest insertion first |
| `get_capture_asset(request_id)` | Canonical filename, media type and bounded byte count; no raw bytes or source path |
| `open_capture(request_id)` | Local service/native SDK returns an owned pinned binary handle after verification |
| `capture_thumbnail(request_id)` | PNG bytes, at most 320×240 and 256 KiB, sampled from the retained DNG |
| `export_capture(request_id, destination)` | Explicit client-local destination, create-only publication, no replacement |
| `GET /api/v1/captures?limit=20&cursor={uuid}` | Completed capture page |
| `GET /api/v1/captures/{uuid}/asset` | Metadata-only asset descriptor |
| `GET /api/v1/captures/{uuid}/file.dng` | Binary `image/x-adobe-dng`, canonical attachment filename |
| `GET /api/v1/captures/{uuid}/thumbnail.png` | Binary retained-DNG display conversion |

Pagination uses a canonical completed request UUID as a keyset cursor over
durable SQLite insertion order. Newly inserted captures do not shift or repeat
an older page; refresh the newest page to see them. `next_cursor` is the last
returned UUID only when more older rows exist. Summaries are allowlisted and
omit filesystem paths and pixel arrays. Failed, cancelled, unknown and active
requests remain available through the existing original-ID query, but are not
gallery entries or export sources.

See the versioned [list schema](../../daos/camera-contracts/v1/capture-list.schema.json),
[asset schema](../../daos/camera-contracts/v1/capture-asset.schema.json), and
[API contract](../../daos/camera-contracts/v1/README.md). Native JSON RPC adds
`list_captures` and `get_capture_asset`; file bytes never enter stdio JSON.

## Source and destination boundaries

An asset must name a canonical lowercase UUID with a committed `complete`
record. The service derives `photos/{uuid}.dng` beneath its own canonical root;
it never follows an arbitrary recorded `file_path` or a client-supplied source
path. Reference records are bound to the requested UUID and journal row before
use; native records retain the existing persisted-row validation.

Source access rejects missing, redirected, nonregular, changed, oversized and
structurally invalid files. POSIX opens use directory-relative no-follow file
descriptors. Windows opens the final entry itself with read-only sharing and
checks its final canonical handle path. A pinned source is checked for identity,
mode dimensions, metadata/request UUID, CFA layout, sample container and bounded
size. DNG inspection can use the same caller-owned handle, avoiding inspection
of one path followed by reopening a different file. The caller retains handle
ownership on inspection success or failure.

HTTP uses bounded binary streaming with exact content length, request identity
headers and a canonical attachment filename. The SDK validates a downloaded DNG
before publishing it locally. Interrupted, wrong-identity and truncated transfers
leave no completed destination. Client-local export writes an owned temporary
file, syncs it, then publishes without replacing an existing entry, including a
destination created during the copy. No server route accepts an output path.

The thumbnail reads actual retained RGGB blocks and performs a simple linear,
uncalibrated RGB display conversion. It does not call the synthetic scene
generator or masquerade as live preview. A regression edits retained sample
values and confirms the thumbnail changes. It is neither a general RAW editor
nor a calibrated color rendering.

## Verification

The native Windows rebuild and both CTest targets passed. Distinct targeted
Python suites passed **108 tests, zero skips**:

| Suite | Tests |
| --- | ---: |
| Retained capture integration | 11 |
| Native daemon / RPC | 20 |
| Native process client | 16 |
| Versioned contracts | 2 |
| Reference service | 14 |
| Existing HTTP service | 6 |
| HTTP client | 19 |
| Raw HTTP security | 9 |
| DNG structural inspection | 11 |

Coverage includes insertion/restart pagination, page bounds, committed-only
selection, byte-identical exports, source UUID binding, missing/corrupt/truncated
or padded files, create-only destination races, pinned source/stream ownership,
sample-derived thumbnails, binary timeout cleanup, and actual native HTTP bridge
operation with exactly one original capture.

A separate actual native full-frame → HTTP SDK probe passed:

```json
{
  "mode": "sim-full",
  "dimensions": [6064, 4040],
  "export_bytes": 48998212,
  "export_seconds": 0.094,
  "sha256_equal": true,
  "thumbnail_dimensions": [320, 213],
  "thumbnail_bytes": 42441,
  "capture_count": 1,
  "lease_unchanged": true
}
```

This proves the binary path transferred a file far larger than the JSON response
limit, with byte equality and a bounded retained thumbnail. The checksum compares
two test files; it is not authentication. The elapsed time is one observation on
this Windows host, not a camera-body or network performance guarantee. All probe
files and owned processes were cleaned up afterward.

## Limits and handoff

The listing records historical journal completion. If its file is later removed
or damaged, asset access reports unavailable; it never repairs/replays that
capture or silently downloads another image. Unknown outcomes require the
existing reconciliation/inspection workflow and cannot bypass it through export.
Existing local-only security, finite network budgets and no-blind-retry semantics
remain in force. Filesystem ownership checks do not provide cryptographic
authenticity against a hostile process with the same account privileges.

Native C++ inputs were frozen and handed to the ARM/cross-build owner before
Python/UI integration finished. QML and console browsing/playback/export tests
are reported by the UI owner. No ARM cross-build files were edited here. Lens
hardware commands remain unsupported; this feature adds no sensor, lens or
calibration hardware capability. No unresolved blocker remains in the retained
media API implementation.
