# Security and crash-consistency audit — round 5

Date: 2026-11-05. Scope: current reference/native camera authority, local HTTP and
stdio RPC boundary, publication of DNG files, and owned client/process cleanup.
This round targeted new defects rather than repeating round-four findings. All
experiments used temporary directories and loopback connections. Nothing was
published. Camera hardware and physical power-loss behavior remain unverified.

## Verified new findings

| Finding | Before the fix | Fix and regression evidence |
| --- | --- | --- |
| Reference capture could overwrite files created after admission. | Admit a delayed capture, then create its public `.dng.partial` and final `.dng` paths. The reference writer truncated the former, `os.replace` overwrote the latter, and the journal reported `complete`. | Python captures use newly created private staging directories and an exclusively opened binary stream. Final publication is create-only: Windows `MoveFileExW` with write-through and no replacement; POSIX hard-link publication plus directory sync. Late conflicts preserve both foreign files and cannot produce a successful conflicting completion. |
| Native failure cleanup deleted a staging file it did not create. | Admit a delayed native capture, then create the public `.dng.partial` path. Exclusive DNG creation correctly failed, but unconditional cleanup deleted the conflicting file. | The native writer reports whether it actually created the output entry, including later I/O failures. The authority removes only an entry it owns, and clears ownership when publication consumes the staging entry. Native staging/final collision regressions preserve foreign bytes and fence re-execution through the existing request record. |
| HTTP framing ambiguity and truncated bodies reached authority mutations. | Raw requests received a lease despite a short body followed by write-EOF, conflicting duplicate `Content-Length`, duplicate `Host`/`Origin`/`Content-Type`, or unsupported `Transfer-Encoding` combined with a length. | Require one valid Host, unambiguous Origin/type/length, supported UTF-8 JSON, no request transfer encoding, and exactly the declared body length before dispatch. Rejected requests do not acquire control or create captures. |
| Python JSON acceptance could terminate the owned native authority. | An escaped lone surrogate in `owner` passed HTTP JSON parsing. It reached nlohmann JSON through the process client, caused an unmatched parse-error envelope, and made the client abort the authority. Duplicate JSON keys and unused nonfinite values also passed HTTP parsing. | HTTP rejects duplicate keys, nonfinite numbers, excessive nesting, and unpaired surrogates in keys or values. Both SDK request encoders validate real UTF-8 locally before I/O. Invalid Unicode is a known `invalid_request`; an existing native process, lease owner and epoch remain unchanged. Legitimate Unicode remains supported. |
| Malformed routes could read or cancel real records. | Extra path segments before a capture UUID still returned its record; an extra-segment cancel URL with a valid lease cancelled the actual capture. Extra-segment workflow reads also returned a job. | Capture/query/cancel and workflow routes require exact segment structure. Invalid routes return 404 before invoking the corresponding action. This corrects dispatch ambiguity; it does not add authority to an otherwise invalid token. |
| Native GET errors escaped the HTTP error envelope. | Kill the native process and request `/api/v1/state`: an uncaught client exception closed the connection instead of returning structured JSON. | GET errors use bounded JSON error responses, including HTTP 503 when the authority is unavailable. The server does not respawn the authority or replay a capture. |
| Slow request dribbles could hold a worker beyond the nominal socket timeout. | The per-read socket timeout could be reset by continued header/body progress, retaining one of the bounded request workers indefinitely. | A validated total request deadline shuts down the socket independently of byte progress. Tests use a 150 ms budget, confirm no admission, observe worker-slot release, and then submit a valid request successfully. Already admitted capture work is never automatically cancelled or retried by that deadline. |

## Implementation

- [service.py](../../camera/camerad/service.py) owns private Python staging and
  exclusive, synced final publication. Cleanup affects only that staging area.
- [hf_dng.py](../../camera/dng/hf_dng.py) additionally accepts an already-open
  binary stream without closing it. Existing path-based writer behavior remains
  available to callers; the service uses exclusive creation.
- [dng.hpp](../../camera/native/include/hf/dng.hpp),
  [dng.cpp](../../camera/native/src/dng.cpp), and
  [daemon.cpp](../../camera/native/src/daemon.cpp) track native staging ownership
  across creation, I/O errors, publication, and cleanup.
- [server.py](../../camera/camerad/server.py) validates framing, strict JSON,
  request targets, exact routes, errors, and total request time budgets.
- [native_client.py](../../camera/camerad/native_client.py) and
  [http_client.py](../../camera/camerad/http_client.py) reject invalid Unicode
  before crossing the submission boundary.

The no-blind-retry contract is unchanged. Admission is not completion. A lost or
unconfirmed capture result retains its original UUID for query/reconciliation;
neither an HTTP deadline nor a transport error silently resubmits it. Publication
failure after a final file may exist remains conservative (`unknown`). Existing
admission/post-publication crash tests continue to pass.

## Targeted verification

The Windows C++ rebuild completed successfully with `python tools/build_native.py`;
both native CTest targets passed. The following distinct Python suites passed
after the corresponding fixes: **108 tests, zero skips**.

| Suite | Tests | Relevant coverage |
| --- | ---: | --- |
| `test_camera_service.py` | 14 | Exclusive publication, late conflicts, no completion before commit, real crash recovery |
| `test_native_daemon.py` | 18 | Native staging/final ownership, durable request reconciliation, corruption fencing |
| `test_native_service.py` | 3 | Compiled worker through private Python staging, final conflict preservation |
| `test_dng.py` | 9 | Caller-owned stream lifetime, unchanged pixels/tags, full-frame and independent TIFF reads |
| `test_http_security.py` | 9 | Raw framing/JSON/routes, native survival, structured errors, total server deadlines |
| `test_camera_http.py` | 6 | Existing HTTP camera/preview/workflow behavior and bounded workers |
| `test_http_client.py` | 19 | Shared authority, no retry, local Unicode rejection, transport/protocol bounds |
| `test_workflow.py` | 14 | Existing workflow execution and recovery behavior |
| `test_native_client.py` | 16 | Known-unsent invalid Unicode, process survival, uncertainty and process cleanup |

These are targeted suite results, not a substitute for the parent clean-build,
full-test and Linux reproducibility record. Native executable changes were
frozen and handed to that independent rebuild owner. Qt source/evidence and
bundle publication are outside this audit's edits.

## Residual scope

- Loopback HTTP is a local API, not a multi-user or Internet authentication
  boundary. Leases fence cooperating clients; this work does not isolate a
  hostile process already running with the same filesystem/account privileges.
- Exclusive publication deliberately fails on filesystems that cannot provide
  the required no-replace operation. There is no fallback that overwrites an
  existing destination.
- A process crash can leave private staging artifacts for offline inspection.
  Recovery preserves uncertain records and never deletes arbitrary conflicting
  paths or replays a capture to tidy them.
- The deadline bounds connection handling; it does not promise hard real-time
  behavior for storage, kernel I/O, or an already admitted operation.
- Hardware acquisition, real sensor faults, physical power cuts and deployment
  on a camera body remain unverified.

No unresolved blocker remains in the verified round-five findings. Source and
targeted tests are ready for the independent clean build and full test pass.
