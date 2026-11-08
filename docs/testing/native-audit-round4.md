# Native authority and client audit — round 4

Date: 2026-11-05. This review covers the native synthetic authority, stdio RPC,
Python process/HTTP clients, and their ownership/uncertainty boundary with the
Qt frontend. It is a software audit on Windows; no camera hardware, physical
trigger timing, measured calibration, or power-loss tolerance is established.

The review used current source plus contained subprocess and loopback HTTP
experiments. It targeted gaps beyond the existing happy-path and injected-crash
suite. Experiments used temporary data directories, retained request IDs before
submission, and closed database handles and owned child processes afterward.

## Verified findings and fixes

| Finding | Reproduction before the fix | Result after the fix |
| --- | --- | --- |
| Persisted completion rows were trusted without validating their structure and identity. | Complete one native capture, stop the authority, then replace `record_json` with only the same request UUID, `status: complete`, and a nonexistent file path. Restart: query and same-ID submission both returned the malformed completion; journal health stayed true. | Native row validation checks canonical JSON, UUID/status columns, immutable request settings, native identity, outcome fields, expected contained output path, and completion evidence. Corruption fences queries and mutations with `journal_error`; state reports an unhealthy journal and omits the invalid last capture. The corrupt row and existing DNG are preserved without repair or re-execution. |
| SQLite text reads and epoch parsing hid additional metadata corruption. | Append an actual NUL plus junk to a valid row, or use authority value `native\0foreign`: C-string reads truncated the bytes, and the authority remained healthy. Persisted epoch `-3` was parsed as unsigned and granted epoch `18446744073709551615`. These were reproduced with an isolated executable copy so the shared build stayed unlocked. | SQLite reads preserve the full byte length and bound metadata size. Persisted epochs require a canonical positive decimal representation. Embedded-NUL rows are fenced; invalid authority/epoch values fail startup without rewriting the original values. |
| The stdio client validated envelopes but accepted invalid result values. | A real fake-child process returned the correct RPC envelope ID with a different capture UUID, an invalid status, or an incomplete completion. The client accepted the value. | Non-object metadata and invalid capture/query records are rejected. An unconfirmed capture becomes `outcome_unknown` with its original UUID; an invalid query becomes `native_protocol_error`. The owned process is stopped and no capture is retried. |
| Context-manager exit could leave an owned authority and directory lock behind. | Leave `with NativeClient(...)` immediately after a delayed accepted capture. `close()` raised `camera_busy`, while the child remained alive and another owner received `already_running`. | Context exit aborts the owned process if graceful close fails, while preserving the exception. A real authority regression confirms the process is dead, the lock can be reacquired, and the interrupted request returns `unknown` after restart. Explicit manual `close()` still preserves a busy process so its owner can query or cancel it. |
| Very large finite timeouts bypassed validation and overflowed platform blocking APIs. | `timeout_seconds=1e100` passed construction. Native startup then raised raw `OverflowError` after spawning a child, leaving the process/lock alive; HTTP calls also raised an untyped overflow. | Both constructors reject unsupported values before I/O, including values above `threading.TIMEOUT_MAX` and integers too large to convert. A regression asserts native `Popen` was never called. |
| An invalid Unicode query identifier escaped the HTTP error contract. | `query_capture('\ud800')` raised raw `UnicodeEncodeError` before sending. | Lone surrogate identifiers produce `HttpClientError(code='invalid_request')` without an HTTP request. |
| Completed responses could contain unreasonable actual settings. | A loopback peer returned the requested UUID with a completed record containing a 1001-digit exposure integer or gain `1e300`; the HTTP client accepted it. | Native and HTTP completion validators enforce the advertised synthetic exposure range of 100–30,000,000 microseconds and gain range of 1–16, alongside identity and file metadata checks. Malformed capture replies remain uncertain; malformed query replies are protocol errors. |

The authority changes are in [daemon.cpp](../../camera/native/src/daemon.cpp).
Client fixes are in [native_client.py](../../camera/camerad/native_client.py)
and [http_client.py](../../camera/camerad/http_client.py). These checks detect
inconsistent local state and protocol values; they do not authenticate arbitrary
filesystem modifications or prove that a historical photo still exists.

## Regression evidence

The targeted Windows run after rebuilding `hf-camerad.exe` passed **51 tests,
zero skips**:

| Suite | Tests | Elapsed |
| --- | ---: | ---: |
| `test_native_client.py` | 15 | 2.496 s |
| `test_native_daemon.py` | 17 | 2.715 s |
| `test_http_client.py` | 19 | 2.482 s |

The new persisted-row regression checks nine corruption forms: missing outcome
fields, a wrong UUID, status-column disagreement, changed immutable exposure,
changed actual exposure, an output path outside the photo directory, malformed
JSON, duplicate JSON keys, and an actual embedded-NUL suffix. Each case verifies `journal_error`, fenced
admission, unchanged corrupt-row text, unchanged DNG bytes, and exactly one
existing DNG. A metadata regression additionally checks an embedded-NUL authority
and signed, spaced, zero-padded, or NUL-suffixed epochs; startup is refused and
the metadata bytes are preserved. The suite also exercises actual native
context-exit recovery.

```powershell
$env:HF_NATIVE_DAEMON = (Resolve-Path 'build/native/Release/hf-camerad.exe').Path
python -m unittest discover -s tests -p test_native_client.py -v
python -m unittest discover -s tests -p test_native_daemon.py -v
python -m unittest discover -s tests -p test_http_client.py -v
```

The rebuilt native core and authority CTest targets also passed. The separate
Linux execution record is owned by the native core review; Windows timings here
must not be presented as Linux or camera-body measurements.

## Targeted blocked-I/O experiments

- A child answered startup and then stopped reading stdin. A 64,000-byte capture
  request returned `outcome_unknown`, retaining its UUID, in 203 ms with a
  200 ms budget. The child was terminated and no native-client I/O threads
  remained after 50 ms.
- A child trickled stdout one byte every 25 ms. The capture returned uncertain
  in 219 ms with a 200 ms budget; process and thread cleanup completed.
- A loopback peer trickled response headers or body one byte every 25 ms.
  Eight calls with a 100 ms budget returned `http_timeout` in 109–110 ms. No
  `http-camera-request` threads remained after 150 ms.

These are finite-budget cleanup observations on this host, not latency bounds or
real-time guarantees. Existing tests additionally cover no automatic capture
retry, lost-reply reconciliation, redirects, proxy avoidance, shared native
authority/workflow ownership, and local-only HTTP attachment shutdown.

## Qt boundary review

The controller retains its shutter UUID before submission, treats an unconfirmed
reply as unknown, and queries that UUID after reconnect. HTTP attachment shutdown
releases its lease explicitly and closes only the attachment; it cannot terminate
the shared server. An owned stdio client may be forcibly stopped after a failed
graceful close, leaving admitted work to reconcile as unknown.

Potential GUI handling of corrupt saved UI state and malformed result values was
forwarded to the independent Qt reviewer for its companion fixes and regression
evidence in [test_device_ui.py](../../tests/test_device_ui.py). Acknowledged
unknown-request history is an audit trail, not permission to capture after a
restart or a fresh query: another deliberate acknowledgement remains required.

No unresolved blocker remains in the audited client/RPC findings. The companion
GUI review and platform-wide test run are reported separately by their owners.
