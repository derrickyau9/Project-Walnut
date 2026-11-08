# UI audit round 4

The focused audit found and fixed four UI problems. Validation covers Windows
desktop software with the actual native HF-Sim authority, the loopback HTTP
adapter, and controlled reference/fake responses. No ARM-body or physical-camera
validation is claimed.

| Finding and evidence before the fix | Result after the fix |
| --- | --- |
| Truncated `device-ui.json` opened as `ready / idle / canCapture=True`, silently discarding uncertainty. | Present but invalid/unreadable/oversized state holds capture and preserves the original file. Explicit recovery copies at most 64 KiB losslessly to a unique sibling backup before replacing UI state; filename/hash are traced and no old UUID is invented. Backup failure or oversize stays held. |
| A same-ID response containing only `status=complete` and an empty path displayed `complete / canCapture=True`. | Completion requires matching request/capture/frame UUIDs, positive bounded actual exposure, positive finite actual gain, and nonempty path text. Inconsistent metadata becomes unknown. Paths from metadata are never opened by the controller. State/settings and lease bodies are checked before assigning typed Qt properties or timer intervals; malformed failure-code fields cannot trigger type errors. |
| Idle renewal ran only with the one-second state refresh; a supported 0.3-second lease disconnected. Acquisition could also precede synchronous QML/font initialization. | Acquisition waits for the Qt event loop. Renewal has its own due check and the polling interval adapts to the lease. An actual native HTTP 0.6-second lease remained owned through three lease durations while idle. |
| A reference capture outliving the shutdown grace window finished, but reopening still failed `already_running`: the failed busy close was never retried. | One daemon cleanup thread closes the reference authority after the active capture ends. Native forced shutdown still uses explicit abort, and HTTP shutdown closes only the local client. |

The audit preserved the existing unknown-outcome boundary. A known original UUID
is queried on restart without capture submission. Explicit acknowledgment permits
only a fresh UUID, retains the original unknown journal record unchanged, and is
not reapplied as permission after query/restart. If saved state cannot identify
the previous UUID, the controller keeps it absent and records a lossless raw
backup instead of inventing a journal association. Restarting between recovery
acknowledgment and the new shutter press holds capture again.

UI state is bounded to a 4 KiB read and stores up to 64 acknowledged UUIDs and four
safe backup filename/SHA-256 references. Recovery reads at most 64 KiB plus one
sentinel byte. Existing backups are never automatically deleted. Malformed data
and raw backup contents are not echoed in status/error text. A changed or missing
backup prevents acknowledgment of an unidentified previous request.

Queue inspection and a 100-click stress regression confirmed one serialized
camera worker, at most three pending interactive camera actions (an existing
read, one fresh capture, and one cancel), one final shutdown cleanup task, one
pending preview generation, and at most one
reference cleanup thread. Preview image caching is disabled in QML; the preview
module retains one small synthetic frame. Accepted work is never replayed.
These are software resource bounds, not hard timing guarantees under a stalled
filesystem, suspended process, or unresponsive operating system.

Multiple-client validation used the actual native authority through HTTP. A
second UI was denied control without disturbing the first owner's epoch. After
an explicit suspended-client lease expiry, another client acquired a new epoch;
shutting down the stale UI neither released that new owner's control nor stopped
the server. Normal HTTP UI shutdown released only its own lease, after which a
fresh client acquired control and queried the original capture.

## Regression evidence

```powershell
python -B -m unittest tests.test_device_ui -v
qmllint ui/on-device/Main.qml
```

Result: **19 tests passed, zero skipped**, with the complete focused UI run taking
13.843 seconds. `qmllint` passed. The audit added ten regressions to the previous
nine UI integration tests. The new tests cover malformed completion/state/lease fields,
missing admission after a saved pending UUID, atomic replacement failure, corrupt
state backup failure/oversize, eventual reference closure, action-queue bounds,
actual native corrupt-state recovery with unchanged old record/two final rows,
and native HTTP short-lease/stale-client handoff.

The workspace-only screenshot
`ui/on-device/evidence/corrupt-state-held-800.png` was rendered and visually
checked at 800 x 600 for readable warnings, disabled shutter/query when the UUID
is unavailable, and the explicit preservation action. Completion, ordinary
unknown, acknowledgment, reference preview and HTTP attached screenshots remain
local under `ui/on-device/evidence/`. All these PNGs are excluded from the minimal
source handoff and can be regenerated using the UI guide. No screenshot is
required to execute the tests.

[Validation metadata](../../ui/on-device/evidence/validation.json) records the
original workspace source, native executable and screenshot hashes. Historical
image hashes describe excluded local evidence; the handoff's
`SOURCE-MANIFEST.json` identifies actual bundled bytes and any redacted copies.
Fixture paths/UUIDs are from temporary software sessions, and fixture DNGs were
cleaned after verification.

The native persisted-record validator was handled by the native-core audit in
parallel. The UI independently treats inconsistent records as unconfirmed and
does not inspect or repair journal storage. No broader project suites were run
by this focused UI audit. Hardware display/touch input, ARM execution, real
sensor acquisition, calibration and production storage durability remain outside
this evidence.
