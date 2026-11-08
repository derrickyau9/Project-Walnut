# DaOS Camera experience / 800×480

The current camera experience adds a configurable twelve-cell Q menu, My Menu,
C1–C3 UI workspaces, six layout templates, portable workspace import/export,
momentary Fn input, interruptible fluid motion and capture-first input routing.
The taskbar automatically hides during capture. Dedicated Qt Camera/CameraFocus
keys and `cameraInput.dispatch(...)` provide the input boundary for a future MCU
adapter; an actual GPIO/MCU bridge is not implied.

Live UI simulation uses bounded QImage frames through `image://daosframes`,
640×424 at a configured 30fps, without PNG/base64 transport. It is explicitly
independent synthetic viewfinding, not a physical sensor or the native RAW stream.
Actual throughput is recorded in `ui-performance.json`; the 60fps UI/30fps sensor
targets require separate real-device measurement.

Playback reads verified DNG samples for fit review and bounded original-pixel
tiles. Ratings, protection, reversible trash, exposure/contrast/saturation,
rotation and centre crop are stored in per-capture sidecars. RAW files are
preserved. The optional JPEG exporter produces an **uncalibrated SDR proof up
to 2048px**, rather than claiming full-resolution calibrated camera JPEG.

Capture recipes now execute through the existing journaled runner, with pause,
resume, cancel, progress and original-UUID reconciliation. The UI releases its
lease during execution and reacquires it afterward. Capture workflows and photo
looks have separate screens. See [soft keyboard details](SOFT_KEYBOARD.md) for
the offline Pinyin dictionary, password/terminal handling and bundled CJK font.

Application-only releases are built with `python -m tools.camera_experience_bundle`.
The installer `system/freebsd/raspberrypi/deploy_camera_app.py` verifies hashes,
backs up existing files, checks a newly ready GUI, and rolls back on failure.
Each release is fully prepared in its own directory before an atomic selector
switch; the previous application tree remains available for recovery.
Boot files, the kernel, wireless drivers and user photographs are outside its
write set. `--stage-root` is available for scratch image trees; it does not boot
or validate an image. Live deployment and Pi performance must be recorded
separately from desktop checks.

Real sensor acquisition, ISO/color calibration, electronic lenses/AF, burst and
pre-capture, audio/video encoding, IMU/IBIS, flash and power telemetry remain
hardware/driver gates. Capability pages state their unavailability explicitly.

## Historical R14 baseline

R14 adds an **icon-only left Taskbar, open by default**. Its seven icons route
directly to Camera, Photos, Recipes, Settings, Devices, Diagnostics and System.
Swipe left on the rail to hide it; swipe right from the leftmost 24px to reveal
it. Arrow handles also permit tapping. The rail and full aspect-fit preview
animate together over 220ms; HUD text and touch buttons retain normal sizes.
Icons have accessible names and hover tooltips, without permanent text labels.

The interface retains **opaque black, white and neutral-gray UI backgrounds** on the
800x480 camera-first interface. Dark/light modes use black/white bases and
neutral-gray controls; colored accents and tinted/translucent panels are
removed. Preview and retained photographs keep their source color. This does
not change saved image pixels or select a monochrome capture style.

The camera interface retains its full viewfinder,
perimeter exposure information, Q quick parameters, grouped MENU and
single-image playback. A separate settings-category rail appears inside MENU;
Tools remain reachable there as well as directly from the Taskbar. R11 is preserved as
`Console.qml` and requires `--interface console`. See the
[camera/menu guide](../../docs/product/camera-apps.md),
[R14 verification](../../docs/testing/round14-taskbar-ui-verification.md),
[ARM64 Qt review](../../build/DaOS_Camera_UI_R14_ARM64_final_002/UI-review.html),
[Windows comparison](../../build/DaOS_Camera_UI_R14_verified/UI-review.html) and
[Taskbar gesture/transition frames](../../build/DaOS_Camera_UI_R14_ARM64_taskbar_motion_v2/UI-taskbar.html).

Native C++ HF-Sim generates and saves synthetic Bayer/DNG. The current viewfinder
is a separate Qt synthetic preview; earlier R14 used 320x240 PNG/base64 at 3fps.
It is not sensor readout or a downsampled native capture buffer.
Playback reads the retained DNG. Host/offscreen rendering validates no
physical sensor, body display, buttons, measured calibration or preview rate.

## Launch

From the repository root, install `requirements-ui.txt` in an isolated Python
environment, build a current C++17 native authority and select that exact file:

```powershell
python tools/build_native.py --build-dir build/native-r11
$env:HF_NATIVE_DAEMON = (Resolve-Path build/native-r11/Release/hf-camerad.exe).Path
python ui/on-device/main.py --data-dir var/DaOS-camera
```

The verified Windows runtime is Python 3.12.7, PyQt 5.15.10 and Qt 5.15.2.
On Unix, the builder normally emits `build/native-r11/hf-camerad`; use its
explicit path through `HF_NATIVE_DAEMON` or `--native-command`. An older binary
without persistent-settings confirmation correctly holds capture after an
unconfirmed edit. Native failure never silently changes capture backends.

```powershell
python ui/on-device/main.py --app settings --data-dir var/DaOS-camera
python ui/on-device/main.py --backend reference --data-dir var/DaOS-reference
python ui/on-device/main.py --interface console --width 1280 --height 800 --data-dir var/DaOS-console
```

Use dedicated directories for separate concurrently running authorities.
Default native mode needs no HTTP server, browser, Node, workflow service,
cloud model or network. `--app` changes route without taking a photograph.
Fresh camera preferences default to dark; saved preferences remain authoritative.
Minimum/default geometry is 800x480; `--kiosk` uses actual Qt screen geometry.
Both saved theme choices now use the neutral palette. The pinned Retail hues
remain as provenance in `retail-theme.json`'s `referencePalette`; active
`light`/`dark` roles follow the explicit `cameraAppearance` user override.

## Photography, menus and retained images

The 72px Taskbar starts open for each launch and can be hidden without changing
camera configuration. A horizontal swipe of at least 32px and 1.5 times its
vertical travel hides/reveals it; vertical or out-and-back motion is not an app
tap. `taskbarProgress` drives both the rail inset and the preview's equal-axis
scale through a 220ms `Easing.InOutCubic` animation. `Image.PreserveAspectFit`
keeps the complete source frame visible, while touch targets remain unscaled.
Reversing a transition is supported. This is UI geometry, not a pixel-format or
capture-pipeline change, and sampled frames make no measured FPS claim.

Ready capture starts synthetic preview automatically. MENU stops that preview;
returning resumes it when safe. Q exposes requested shutter speed, analogue gain
and grid. Exposure/gain/mode edits are field-level commands under the existing
lease. Configure acknowledgment and readback must agree before the shutter is
released; rapid edits are coalesced. Failed/unconfirmed configuration holds
capture until explicit reconnect/readback, without automatic configure retry.

The touch shutter or Space submits one photograph. Space is disabled outside
capture, with Q/acknowledgment dialogs open, during Taskbar gesture/animation,
with Taskbar navigation focused, or while capture is held. Space/Enter on an
icon only navigates. Key
repeat cannot submit extra shots; Enter is not a global shutter. Q, M and P
open quick controls, MENU and playback from capture. Escape closes an overlay
first, then returns from menus/playback to shooting; Tools uses route history.

Playback has previous/next, INFO and explicit DNG export. INFO contains
metadata and paths instead of occupying the shooting screen. Completed entries
are bounded to 20 per page; older pages replace the model. Export requires a
new absolute `.dng` destination with an existing parent. Existing files are
rejected. Selection/export do not submit a shot, change the original request,
acknowledge an unknown outcome or acquire control.

Display preferences are separate bounded strict JSON: theme, grid and
preview brightness. They do not alter RAW, sensor exposure or physical
backlight. Recipes provide validated plan editing/export; the offline CLI
executes them separately. Diagnostics/device/system tools expose facts,
without invented hardware telemetry or nonfunctional updater/power buttons.

## Capture identity and recovery

Before submission, one UUID is atomically saved in `device-ui.json`; failure
blocks the shutter. Unknown results retain that ID and show recovery controls.
Query original reads it without capture; reconnect restores access and queries
it without resubmission. Native mode may relaunch its owned process; attach
mode only reconnects to the existing authority.

Explicit acknowledgment preserves the old authority record and permits a
separate shutter press with a new UUID. It never resolves, replays, deletes or
marks the old record complete. Saving acknowledgment must succeed. Restarting
or querying a still-unknown current request holds the shutter again. The
bounded UI-state format retains up to 64 acknowledged UUIDs and reads earlier
versions. Unreadable state holds capture rather than starting a new session;
lossless backup behavior is covered by the
[controller audit](../../docs/testing/ui-audit-round4.md).

RPCs run serially on a worker thread. Polling/lease renewal leave the GUI event
loop available. Preview rejects late frames across settings, capture,
disconnect and unknown-state boundaries. Retained-media work has a separate
bounded worker so export does not occupy camera RPCs. Closing stops preview,
releases its lease and closes an owned authority; unfinished work is
conservatively reconciled when reopened. Closing is no guarantee of a saved photo.

## Attach to an existing local API

```powershell
python ui/on-device/main.py --backend http --endpoint http://127.0.0.1:8787 --ui-state-dir var/DaOS-camera-attach
```

This explicit mode starts no authority and opens no server journal. Its UI
state is local to `--ui-state-dir`, separate from server data; use one state
directory per attached authority. Control remains exclusive across clients.
Closing an attached UI leaves the shared server running. Native/reference use
`--data-dir`; `--ui-state-dir` can separately override local UI state.

## Tests and actual screenshots

```powershell
python -B -m unittest tests.test_camera_first_ui -v
python -B -m unittest tests.test_camera_taskbar_ui -v
python -B -m unittest tests.test_device_neutral_theme -v
python -B -m unittest tests.test_device_shell_model tests.test_device_apps -v
python -B -m unittest tests.test_device_ui tests.test_device_shell_ui -v
```

The 12-case camera-first suite loads `Main.qml` and asserts its identity; it
cannot silently test the old workspace. It covers QML input, geometry, menu/tool
routes, configuration acknowledgment, retained DNG playback, preferences,
unknown-request safety and shutter isolation. Historical 23 controller/UI cases
and 12 shell cases now load `Console.qml`; those tests do not establish the
camera-first layout. Missing optional Qt/native prerequisites produce explicit
skips; actual results and failures belong to the linked platform reports.
The focused neutral-theme tests inspect active palette roles and rendered menu,
Q, shooting-bar and optional-tool backgrounds in both theme modes, while
checking that those interactions do not submit a photograph or alter camera
settings. The taskbar suite covers default icon routes, swipe/tap separation,
hide/reveal, intermediate animation geometry and shutter/keyboard isolation.
Actual current results belong to the R14 report; R13 evidence remains frozen.

Render without a shutter action:

```powershell
python ui/on-device/main.py --data-dir var/DaOS-render --offscreen --screenshot build/DaOS-camera-preview.png
```

Record Taskbar gestures and animation without taking a photograph:

```powershell
python -B tools/review_taskbar_motion.py --output build/DaOS-taskbar-review-new --timeout-seconds 90
```

This tool uses the Python reference authority and independent synthetic preview,
not a native daemon. It requires a fresh output directory, records actual Qt
endpoint/intermediate PNGs and `review.json`, and verifies zero shutter actions,
zero capture rows, unchanged request/settings, unchanged source and clean
fixture closure. Each animation frame records a request/ready observation
interval; the render instant lies inside that interval. It does not invent an
exact timestamp, measured FPS or physical-display result. Preserve failed
reports; do not replace them with reconstructed frames. The
[Taskbar review gallery](../../build/DaOS_Camera_UI_R14_ARM64_taskbar_motion_v2/UI-taskbar.html)
contains the actual recorded frames separately from native capture evidence.

Create a fresh source-bound camera-first review:

```powershell
python -B ui/on-device/review_camera_first.py --native-command build/native-r11/Release/hf-camerad.exe --output build/DaOS-camera-review-new
```

**This review command takes one synthetic photograph.** It needs a fresh output
directory, operates actual QML controls, records one shutter intent and checks
the original request and retained DNG/export identity. Eight PNGs use actual
800x600 primary geometry with a secondary larger view. `review.json` records
runtime, separate preview/capture paths, source/PNG hashes, settings and native
shutdown. Uncertain capture aborts without retry. Do not repeat the command in
the same session to hide a failure. The new runner uses QtTest for input;
historical `review_capture.py` and `review_shell.py` remain Console-based tools.

```powershell
python -B tools/create_ui_review_gallery.py --review-dir build/DaOS-camera-review-new --output build/DaOS-camera-review-new/UI-review.html
```

The create-only offline gallery validates the successful report and original
PNG bytes before embedding them. Host renders identify their actual runtime;
they are never evidence of an unperformed BSD appliance image boot.

## Platform and historical evidence

The active appliance target is ARM64/aarch64 DaOS BSD Unix. **Frozen R10
image003 retains its earlier body UI**, with separate actual ARM64 full-system
boot/capture/restart evidence. R14 source and screenshots do not update that
image. A new appliance image needs a separate build/boot receipt. See
[R10](../../docs/testing/round10-freebsd-arm64-verification.md),
[R11](../../docs/testing/round11-camera-apps-verification.md),
[R12](../../docs/testing/round12-camera-first-verification.md),
[R13](../../docs/testing/round13-neutral-camera-ui-verification.md) and
[R14](../../docs/testing/round14-taskbar-ui-verification.md).

Earlier audit/controller evidence is preserved in
[round 4](../../docs/testing/ui-audit-round4.md),
[round 5](../../docs/testing/handoff-audit-round5.md),
[round 6](../../docs/testing/ui-workflow-round6.md) and
[Linux ARM round 7](../../docs/testing/arm64-ui-round7.md).
Historical hashes and temporary-path screenshots are not rebound to current
source. The clean source handoff excludes runtime databases, credentials, DNGs
and unnecessary build output; its manifest identifies included bytes and any
normalized evidence copies. Real sensor/board/display, measured calibration,
thermal, power-loss and manufacturing validation remain separate work.
