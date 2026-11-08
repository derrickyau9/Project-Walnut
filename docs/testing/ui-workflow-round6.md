# UI and workflow completion - round 6

Date: 2026-11-05. This work follows the preserved round-five snapshot. The
snapshot archive, original import bytes and previous screenshot/validation
files were not modified. These results concern desktop software with synthetic
captures. They make no camera-body display, sensor or measured calibration claim.

## Confirmed gaps and delivered behavior

The earlier UI and console could display one capture result and an independent
synthetic preview, but could not browse committed captures or review/export a
saved DNG. Existing workflow execution already supported bracket, interval,
dark/flat, durable IDs, pause/resume and cancellation. The missing workflow
surface was a portable recipe file and recent-job selection; those features
were implemented without replacing the existing runner or journal semantics.

The native, reference and HTTP adapters now share a completed-capture listing,
actual retained-DNG thumbnail and verified export contract. Backend source and
transfer validation are documented separately in
[retained media round 6](retained-media-round6.md).

The Qt dialog and console load 20 summaries per page, use canonical UUIDs for
selection, and replace the page on navigation. Only allowlisted display fields
enter either gallery model. The PNG is bounded before decoding and is derived
from the selected saved Bayer samples. Its visible caption says it is a
synthetic, uncalibrated retained-DNG display conversion, separate from live
preview. Original unknown/failed requests stay on their original query path;
browsing does not acknowledge them or change the current request.

Qt export accepts an explicit absolute client-local `.dng` path, uses the SDK's
create-only publication and confirms byte count after success. An existing file
is refused without replacement. HTTP attach never sends that local destination
to the server. The console downloads the canonical DNG through an attachment
endpoint; destination/overwrite behavior belongs to the browser. No gallery
operation acquires a lease, submits a shot, repairs a journal or deletes a file.

## Workflows and portable recipes

`hasslefree.workflow-recipe.v1` contains exactly a display name and a strict plan
under a versioned format field. It carries no tokens, owner details, job/request
IDs, output paths or results. The 64 KiB boundary rejects duplicate keys,
non-finite values and unknown fields. Python and browser normalization agree on
all four supported kinds, including integral JSON numbers and defaults.

The console imports validated settings without starting a job and exports only
the portable recipe. Starting remains a separate action that creates a new job
ID. Recent-job browsing returns the newest 50 jobs; older journal rows remain
inspectable by ID. Pause/resume/cancel continue using existing job identities.
The offline CLI accepts `run --recipe PATH` and offers camera-free create-only
`recipe-export`; recipe execution refuses conflicting manual plan overrides.
Sharing is a local file operation through a user-selected channel, with no
outbound message or cloud dependency. The independent Qt capture application
still needs no workflow runner, HTTP server, browser or Node runtime.

## Responsiveness and state bounds

Qt uses a separate single-worker executor for retained-media work and accepts
only one pending gallery action. Camera status, cancellation and heartbeat stay
on their existing serial worker. A regression gates the file copy, clicks the
controls 100 times, verifies only one export was submitted, and holds a
0.6-second lease for 1.3 seconds while the copy waits. Paging replaces a maximum
20-entry model and clears old review selection. Thumbnail data is at most
256 KiB and 320 x 240, validated before Qt image decoding. Close drains accepted
gallery work before closing the client; stalled storage on shutdown has no
bounded-latency guarantee.

The console streams bounded metadata (128 KiB) and PNG responses (256 KiB), uses
a 10-second fetch timeout, keeps one pending gallery action and revokes old image
object URLs. Browser download runs through the browser without buffering a DNG
into the page. New recipe parsing bounds size, nesting and node count. None of
these operations changes the current capture UUID or its saved UI trace.

## Executed verification

| Check | Result |
| --- | --- |
| `python -B -m unittest tests.test_device_ui -v` | 23 passed, zero skips; 19.652 seconds |
| `python -B -m unittest tests.test_workflow tests.test_workflow_recipes tests.test_console_contracts -v` | 30 passed, zero skips; 13.686 seconds |
| `python tests/ui/round6_smoke.py` | Actual Edge/native-HTTP smoke passed, zero skip |
| Node syntax checks of app/recipes/retained modules | Passed |
| `qmllint ui/on-device/Main.qml` | Passed |

The four new Qt retained tests exercise actual reference thumbnail identity,
older-vs-newest image selection, byte-identical export and duplicate destination
refusal; unresolved-current-request preservation; 20-entry paging and bounded
slow export; and actual native HTTP review/export with unchanged epoch/request,
released UI lease and a server that remains available after UI shutdown.

The 12 recipe tests execute all four offline modes, malformed/private input,
concurrent create-only publication and copied-native execution. Reopening the
same job and unchanged recipe creates no duplicate captures; a deliberately new
job uses new IDs. The four console contracts compare JavaScript with Python,
reject malformed recipes and unsafe summary metadata, and confirm bounded job
listing without deleting history. Existing 14 workflow regressions also passed.

The actual browser smoke captures once through the native HTTP authority,
loads/decodes its retained thumbnail and downloads an identical DNG hash while
preserving its request ID and epoch. It imports a local recipe without creating
a job, exports a Python-readable recipe, selects/inspects the recent job, pauses
a three-shot interval job after its first shot, resumes to three complete shots,
and cancels another interval job after one shot. It verifies seven capture
records total, released control, and no horizontal overflow at 390 pixels. The
smoke takes no browser screenshots and uses a temporary browser profile and
copied native executable.

## Visual evidence and limits

The two new actual Qt gallery renders were inspected at 800 x 600 (reference)
and 1024 x 680 (native HTTP). Selection, true retained-image caption, explicit
export path, success byte count and duplicate-destination error remain legible.
Workspace-only paths are
`ui/on-device/evidence-round6/retained-library-reference-800.png` and
`ui/on-device/evidence-round6/retained-library-native-http-1024.png`.
These contain temporary fixture paths/UUIDs and are excluded from the minimal
source handoff. There are no dangling image embeds. Source/runtime/screenshot
hashes are in [round-6 validation metadata](../../ui/on-device/evidence-round6/validation.json).
Previous evidence bytes and hashes were left untouched.

This closes the verified software gaps for retained-image review/export and
portable recipe handling. Full-resolution 49 MB backend transfer is separately
verified in the retained-media report; the Qt gallery integration uses small
synthetic fixtures. Uncalibrated thumbnails are for review, not color-accurate
RAW development. Gallery export requires an existing writable destination
parent and the backend's supported create-only publication operation. Browser
save behavior is browser-controlled. Removable-media mounting, FAT/exFAT
semantics, camera-body display/touch, physical buttons, real sensor acquisition,
optical calibration, power-loss recovery and real-time interval cadence remain
unverified. Physical G4 validation is not implied by these software results.
