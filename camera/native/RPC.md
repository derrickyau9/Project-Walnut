# Native synthetic authority RPC

Build with `python tools/build_native.py`. The daemon executable is
`build/native/Release/hf-camerad.exe` on MSVC and `build/native/hf-camerad` on
single-configuration builds. Set `HF_NATIVE_DAEMON` to select an executable.

```text
hf-camerad --data-dir PATH [--lease-seconds N] [--enable-test-faults]
```

The data directory is explicit and persistent. One OS-locked process owns its
`journal.sqlite3` journal and `photos` directory. The journal records
`meta.authority = native`; a populated reference Python journal is rejected.
Use separate directories when comparing the two authorities. The backend and
authority values are both `native`; `simulated` is always true. This process
generates synthetic frames and does not implement a hardware driver or native
preview stream.

Startup validates persisted authority/epoch metadata and capture rows before
using them. Invalid authority or epoch metadata fails startup; inconsistent
capture rows fence queries and mutations with `journal_error` and surface an
unhealthy journal. Invalid records are preserved for offline inspection, never
rewritten as success or automatically replayed. Validation checks recorded
identity/evidence, and does not recreate a photo removed by its owner.

## Wire format

The process reads UTF-8 JSON lines from stdin and writes only JSON responses to
stdout. Each request has one response with the same ID. EOF cancels and joins
remaining work before releasing the directory. A successful explicit `close`
responds and exits; an active capture returns `camera_busy` and keeps the process
alive. Initialization failures return an error with `id: null` before exiting.

```json
{"id":1,"method":"get_state","params":{}}
{"id":1,"result":{"connected":true,"simulated":true}}
```

The abbreviated result above illustrates the envelope. Errors use:

```json
{"id":2,"error":{"code":"control_denied","message":"control token or epoch is invalid"}}
```

An ID is a positive integer no greater than `9007199254740991`, or a nonempty
string of at most 128 UTF-8 bytes without NUL. `params` defaults to an empty
object. Envelope keys and method parameter keys are checked. Duplicate JSON
keys, malformed UTF-8/JSON, nonfinite numbers, and nesting deeper than 32 levels
are rejected. A native input line may contain at most 65,536 bytes before its
newline; oversized input produces one bounded error and exits. Output lines,
including the newline, are limited to 256 KiB. Responses carry metadata, never
Bayer samples, encoded images, or DNG bytes.

| Method | Parameters | Result |
| --- | --- | --- |
| `capabilities` | `{}` | Synthetic modes, limits, and available simulation faults |
| `get_state` | `{}` | Connection, settings, lease owner, journal health, last capture, pipeline generation |
| `acquire` | `owner` | Token, durable epoch, lease duration |
| `heartbeat` | `token`, `epoch` | Renewed lease information |
| `release` | `token`, `epoch` | Release acknowledgement |
| `configure` | `token`, `epoch`, `settings` | Validated merged settings |
| `capture` | Full capture request | Persisted request record after durable admission |
| `query_capture` | `request_id` | Persisted record or `null` |
| `cancel_capture` | `token`, `epoch`, `request_id` | Cancellation acknowledgement |
| `reconnect` | `token`, `epoch` | Explicit synthetic device reconnection |
| `close` | `{}` | `{"closed":true}` followed by process exit |

Capture parameters match the reference service: canonical lowercase UUID
`request_id`, `token`, `control_epoch`, `sensor_mode`, `requested_exposure_us`,
`requested_analogue_gain`, `output_format`, optional `simulation_scene`, optional
`deadline_unix_ms`, and optional `fault`. Admission is asynchronous: a returned
`accepted` or `capturing` record is not a completed photograph. Query until a
terminal status (`complete`, `failed`, `cancelled`, or `unknown`). A `complete`
record follows a checked DNG write, file sync, final rename, and journal commit.

## Python client

```python
import uuid
from camera.camerad.native_client import NativeClient, NativeClientError

client = NativeClient("var/native-camera", timeout_seconds=5)
lease = client.acquire("local-ui")
request_id = str(uuid.uuid4())  # Retain before sending the shutter request.
settings = client.get_state()["settings"]
try:
    record = client.capture(dict(settings, request_id=request_id,
                                 token=lease["token"],
                                 control_epoch=lease["epoch"]))
except NativeClientError as error:
    if error.outcome_unknown:
        # Reopen the same directory, then query request_id. Never recapture
        # automatically to resolve a transport or journal error.
        client.abort()
        client = NativeClient("var/native-camera")
        record = client.query_capture(request_id)
    else:
        raise
```

`NativeClient` owns its subprocess and serializes all RPC calls. It limits
requests to 64 KiB including framing, responses to 256 KiB, and retained stderr
to 64 KiB. Reads, writes, and serialization-lock acquisition have finite time
budgets. `NativeClientError` inherits `CameraError`, exposes `.code`, and carries
`.request_id` and `.outcome_unknown`. A transport failure after a capture send
returns `outcome_unknown` and stops the owned process. It never retries or
automatically restarts a capture. Interrupted admitted requests reconcile as
`unknown` on restart; querying or submitting the same ID cannot re-execute them.
The journal's canonical request payload excludes the control token and epoch,
so a new authorized owner can reconcile an earlier ID. A conflicting payload
returns `request_conflict`.

Call `close()` after accepted work reaches a terminal state. On `camera_busy`,
the client remains usable for query/cancel. `abort()` forces process shutdown;
retain outstanding request IDs for later reconciliation. The `.root`, `.photos`,
`.process`, `.closed`, and `.stderr_tail` properties support local adapters.
Context-manager exit always releases the owned process, including when graceful
close reports `camera_busy`; the error still propagates and the original capture
ID must be queried after restart. Both clients reject unsupported platform
timeouts before I/O. Native and HTTP capture/query responses must match the
original ID and a known status; completion evidence must contain valid actual
settings, frame/capture IDs, and a file path.

## Attach to an existing HTTP authority

Use `camera.camerad.http_client.HttpCameraClient("http://127.0.0.1:8787")`
to attach another frontend to the existing `/api/v1` camera API. It exposes the
same control, capture, query, cancellation, and reconnect methods, plus HTTP
preview start/stop. Its origin must use loopback HTTP; credentials, additional
URL paths, redirects, and environment proxies are rejected or ignored. Requests
are limited to 64 KiB, responses to 256 KiB, and calls have a finite total I/O
budget. Response parsing rejects duplicate keys, nonfinite numbers, and invalid
capture IDs/statuses. `close()` and `abort()` close only the local attachment;
they never stop the server or release its lease. Release control explicitly
before closing a frontend that owns it.

Both clients expose `.request_id` and `.outcome_unknown` on errors. Capture
transport failures use code `outcome_unknown`; journal/internal error responses
preserve the server's code and set `.outcome_unknown` when admission cannot be
ruled out. Retain and query the original ID instead of automatically capturing
again. The HTTP client can query through the same instance after a transport
failure, or through a newly opened attachment to the same authority.

## Explicit simulator fault testing

Ordinary simulated faults are `delay`, `storage_full`, `disconnect`, and
`corrupt_frame`. Reconnect after a simulated disconnect requires a valid lease.
The following process/journal tests are rejected unless the daemon was started
with `--enable-test-faults`:

- `crash_after_accept`: exits after durable admission, before worker creation.
- `crash_after_rename`: exits after final-file publication, before completion.
- `worker_start_failure`: journals a worker launch failure.
- `admission_commit_failure`: rolls back admission and fences journal mutations.
- `completion_commit_failure`: fences the authority and reports an unknown result.

Run the real subprocess and transport checks with:

```text
python -m unittest discover -s tests -p test_native_daemon.py -v
python -m unittest discover -s tests -p test_native_client.py -v
```

## API 1.1 persistent state and calibration trace

`configure` commits validated settings to SQLite before changing confirmed
memory or returning success. Captures always carry explicit parameters and do
not modify the configured baseline. `get_state` exposes `settings_persistent:
true` and `settings_format: hf-device-settings-v1`. Metadata keys `settings`
and `settings_format` are paired: both absent permits one legacy default
migration; exactly one absent, invalid JSON, duplicate keys, nonfinite values,
missing/unknown fields or an unsupported version refuse startup without
resetting the settings or advancing the persisted control epoch. No frontend
cache is the authority for those values.

`bind_calibration` takes `{token,epoch,binding}` and `clear_calibration` takes
`{token,epoch}`. Both require a valid lease and an idle connected simulator.
They commit the new inline snapshot before updating memory. `get_state` and
both mutation results expose the six-field `calibration` descriptor:
`sensor_id`, `binding_id`, `calibration_id`, `package_checksum`,
`hardware_calibrated:false` and `pixel_corrections_applied:false`.
Unbound state uses null binding/checksum and calibration ID
`synthetic-uncalibrated`. The active store uses metadata keys
`calibration_binding` and `calibration_binding_format`, with wrapper
`{format:"hf-active-calibration-v1",binding:<artifact-or-null>}`. Missing,
corrupt or unsupported paired state is fenced on startup; no fallback binding
is selected silently.

The exact artifact and byte-hash domain are specified in
[BINDING.md](../calibration/BINDING.md). Native validation checks the complete
bounded v1 package semantics, Unicode reason length and expected sensor as well
as both SHA-256 identities. It hashes the original ASCII payload bytes, never a
floating-point JSON reserialization. A checksum is an integrity aid, not an
authenticity signature. Payload whitespace/number spelling can produce distinct
valid binding IDs. The RPC request limit remains 65,536 bytes; a larger artifact
accepted by the standalone store cannot be activated through this transport,
and fails before side effects. No request is truncated.

Only a bound capture adds `capture_record_version:2`, its complete
`calibration_binding` snapshot, and the two false calibration/correction flags.
The record and DNG metadata use that snapshot's `cb1-...` ID. Unbound records
keep their legacy shape. The immutable request payload excludes active
selection, so retrying the same capture ID and parameters after a selection
change returns its original result and binding. Pixel values are unchanged by
binding; no measured calibration or correction is claimed.

Additional `configure` test faults are `settings_commit_failure` and
`crash_after_settings_commit` (process exit 88 after commit, before memory).
Bind/clear test faults are `binding_commit_failure` and
`crash_after_binding_commit` (exit 89 at the same boundary). They use the
optional `test_fault` RPC parameter and require `--enable-test-faults`.
Ordinary clients have no fault override. Run the additional real-process checks
with `tests.test_native_settings` and `tests.test_native_calibration_binding`.
