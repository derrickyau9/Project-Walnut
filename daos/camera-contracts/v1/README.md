# Camera contract v1 (simulation)

HTTP binds to loopback for local development; it has no production authentication or transport security. Tokens are bearer credentials held only by the caller; epochs persist and increase on startup and acquisition. Request IDs are canonical lowercase UUIDs. The caller must retain the ID and query `GET /api/v1/captures/{request_id}` after a lost response. A duplicate ID with identical capture settings returns its original record; a different payload is rejected. There is no automatic re-execution. On restart, nonterminal requests become `unknown` because a file may have been committed without the journal being updated. An unknown request requires inspection, not an automatic new capture.

`POST /api/v1/control/acquire` accepts `{ "owner": "console" }` and returns `token`, `epoch`, `owner`, and `lease_seconds` (30 by default). Renew with `POST /api/v1/control/heartbeat` using `token` and `epoch`; call well before expiry. Lease time uses the service's monotonic clock. Once expired, old credentials cannot renew or issue commands; a new owner receives a higher persisted epoch. Expiry does not cancel an already accepted capture, and a new owner cannot start another until the active one terminates. Release uses `POST /api/v1/control/release` with `token` and `epoch`. Capture uses the adjacent schema. `POST /api/v1/captures/{id}/cancel` requests cancellation using token and epoch; callers must query the record to observe whether it took effect. `GET /api/v1/capabilities` and `GET /api/v1/state` report simulator capabilities and state. `POST /api/v1/configure` accepts token, epoch, and a `settings` object. The simulator returns requested and actual settings as separate fields, but the actual values are simulated and are not hardware measurements. Terminal statuses are `complete`, `failed`, `cancelled`, `unknown`; accepted and capturing are nonterminal. Completion means the simulator file was renamed and its journal record committed; arbitrary power-loss integrity is not established.

API is intentionally new and does not claim compatibility with the existing web capability schema. No image pixels travel in JSON. Sensor identity, mode and calibration are synthetic. Disk failure injection is not an operating-system or real storage hardware test.

`POST /api/v1/preview/start` and `/stop` require a valid token and epoch. `GET /api/v1/preview.png` returns the latest synthetic preview with frame and timestamp headers or HTTP 503 when stopped or paused for capture. It is a small demand-driven synthetic scene, not a sensor readout or a guarantee of non-interrupted hardware preview. A capture transition invalidates the cached frame.

Workflows run through the same service: `POST /api/v1/workflows` accepts `job_id` and `plan`; list and inspect with `GET /api/v1/workflows` and `GET /api/v1/workflows/{job_id}`; request pause, resume or cancel via `POST /api/v1/workflows/{job_id}/{action}` with an empty JSON object. The workflow acquires its own control lease: release a console lease before starting it. The journal and original capture IDs are reconciled on restart; an `unknown` capture must be inspected rather than replaced automatically.

If a journal write fails, the service blocks further mutating requests until restart and reports `journal_healthy: false`. Restart and query the original ID; an unsuccessful response is not proof that no record reached storage. A lock file prevents multiple service processes from sharing a journal. These controls have been tested locally, not against sudden device power loss or media faults.

Injecting `disconnect` sets the simulated device offline for the rest of the process until an authorized `POST /api/v1/device/reconnect` with token and epoch, or until service restart. Capture queries remain available; new captures, configuration, and preview start are rejected while offline and the preview endpoint reports unavailable. This tests software behavior only; reconnect is not a real hardware recovery procedure.

## Retained completed captures

`GET /api/v1/captures?limit=20&cursor={uuid}` returns the adjacent
`capture-list.schema.json`: an allowlisted metadata page containing only
durably completed records. The default limit is 20, maximum 50. Order is newest
first by durable journal insertion order. `next_cursor` is the last returned
UUID when older records remain, otherwise null. Pass it unchanged for the next
page; a fresh first page sees newly completed captures without shifting an
older page or re-executing a request. Failed, cancelled, unknown, accepted and
capturing records remain queryable by their original ID and are not gallery
items. Missing files do not rewrite historical journal completion; asset access
reports the missing file separately.

`GET /api/v1/captures/{uuid}/asset` returns `capture-asset.schema.json`, containing
only request identity, canonical filename, byte count and media type.
`GET /api/v1/captures/{uuid}/file.dng` sends the verified retained DNG as a binary
`image/x-adobe-dng` response with attachment filename `{uuid}.dng`.
`GET /api/v1/captures/{uuid}/thumbnail.png` sends a bounded PNG display conversion
sampled from that retained DNG's actual Bayer values. The thumbnail is explicitly
uncalibrated playback, distinct from the independently generated live preview.
Image data is never embedded in list, query, asset-descriptor or stdio RPC JSON.

All asset routes require a canonical lowercase UUID and a committed `complete`
record. They derive the source from the authority's photo directory, never a
caller-supplied path or the record's arbitrary file-path value. Source access
requires a pinned regular file, contained/no-follow path checks, bounded size
and dimensions, structural DNG validity, and matching DNG request identity.
Unknown/uncommitted, missing, redirected and corrupt sources are rejected; no
asset operation triggers a capture or repairs the journal. These read-only
operations require no control lease in the local development security model.

Local SDKs expose `list_captures(limit=20, cursor=None)`,
`get_capture_asset(request_id)`, `capture_thumbnail(request_id)` and
`export_capture(request_id, destination)`. The destination is chosen on the
client machine; no server route accepts a source or destination path. Export
uses temporary output and create-only publication, refuses an existing target,
and never replaces a retained source. A binary download failure must be retried
only as an explicit read/export action, never by resubmitting a capture.
