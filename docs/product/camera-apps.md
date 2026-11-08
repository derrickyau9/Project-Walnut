# DaOS Camera controls, menus and apps

R14 adds a **left icon-only Taskbar, open by default**, to the 800x600 camera
interface. It opens Camera, Photos, Recipes, Settings, Devices, Diagnostics and
System directly, without permanent text labels. Accessible names and hover
tooltips identify the icons. Swipe left to hide it and right from the left
screen edge to reveal it; the arrow handle provides a tap alternative.

The interface retains the user's requested
solid black, white and neutral-gray UI backgrounds. Dark mode uses a black
base; light mode uses white with neutral-gray controls. Menus, Q panels,
selection backgrounds and app surfaces are opaque. Preview and photographs
retain their source color; the UI policy is not a monochrome capture style.

Exposure information surrounds the image; the bottom strip contains MENU,
playback, shutter speed, analogue gain and Q. A touch shutter sits beside the
image. Hiding the Taskbar restores the wider viewfinder; there are no dashboard
cards, file paths or request UUIDs on the shooting face.
The earlier R11 workspace remains an explicit development console, not the body UI.

Menu conventions and the design reference come from the inspected
[DaOS Retail interface](../architecture/retail-camera-ui-adapter.md). Current
colors are an explicit user override, not computed Retail hues. The native
Qt implementation uses system fonts and original vector icons. It does not
import the Retail business application or the original TypeScript runtime.

The active theme roles are neutral `light`/`dark` entries in
`retail-theme.json`. Its `referencePalette` preserves the original Retail
colors and pinned revision; `cameraAppearance` records the user-requested
opaque UI policy and the unchanged photographic-pixel boundary. Selected rows
use gray with white labels instead of a colored highlight.

## Taskbar interaction

The Taskbar is 72px wide and starts open on each UI launch. A left swipe
starting on its surface or an icon hides it without activating an app. When
hidden, a right swipe starting in the screen's leftmost 24px reveals it.
A horizontal gesture must travel at least 32px and exceed its vertical travel
by a factor of 1.5; vertical swipes and drags that move out and back do not
become icon taps. Click an icon normally to navigate. Taskbar visibility is
local UI state, not a camera setting.

Opening/hiding uses a shared 220ms ease-in/ease-out transition: the Taskbar
slides while the preview scales with the available width. The full frame stays
visible with preserved aspect ratio; it is not cropped or stretched. Exposure
text and touch buttons retain their normal sizes. A gesture can reverse an
in-progress transition. The arrow handle also permits hide/reveal by tapping.

The shutter is held during a Taskbar gesture or animation. With an icon focused,
Space/Enter navigates that icon and cannot take a photograph. Navigation,
hide/reveal and animation do not configure the camera, replace the original
capture ID or change image pixels. They do not acknowledge an unknown result.

## Photography flow

1. Start the UI. When the authority is ready, the separate synthetic preview
   starts automatically. Returning from a menu resumes it when safe.
2. Open **Q** to change requested shutter speed, analogue gain or framing grid.
   Exposure and gain go through the camera authority; the shutter remains held
   until configuration acknowledgment and state readback agree.
3. Close Q and press the touch shutter or Space once. Saving/saved indications
   show progress. An unresolved result holds the shutter and exposes query,
   reconnect and explicit acknowledgment for the original request. It never
   takes another photograph to guess that request's outcome.
4. Open **playback** for a retained image. Previous/next select saved captures;
   INFO reveals metadata and paths. Export requires a new absolute local `.dng`
   destination and never replaces an existing file.

Space is enabled only on capture, with Q/acknowledgment dialogs closed,
Taskbar navigation unfocused, no Taskbar gesture/animation and capture
permitted. Key auto-repeat cannot submit more photographs. Q opens
quick controls, M opens MENU and P opens playback from capture. Escape closes
an active overlay first or returns from menus/playback to shooting. Enter is
not a global shutter. Physical buttons/dials are not yet integrated.

Gain is an analogue multiplier, not an invented ISO calibration. No aperture,
AF, battery or thermal value appears without a supporting device capability.

## MENU and tools

The Taskbar's Settings icon and MENU both open grouped camera settings. The
additional category rail exists **inside MENU only**. Its groups are Exposure,
Image / RAW, Display, Storage, System and Tools. Exposure rows open Q; image
settings select actual HF-Sim modes `sim-small` (128x96) and `sim-full`
(6064x4040). Full-size simulation can require substantial CPU time and memory.

| Entry | Implemented purpose and boundary |
| --- | --- |
| Exposure | Manual requested shutter speed and analogue gain, confirmed by the authority. Actual per-frame values remain separate capture metadata. |
| Image / RAW | Select the simulation mode and inspect Bayer DNG, 14-bit sample values and 16-bit storage-container facts. No physical sensor mode is claimed. |
| Display | Persist grid, light/dark theme and synthetic preview brightness separately from camera settings. |
| Storage | Retained-media/storage facts and entry into playback. No fake format-card action. |
| System | Local runtime facts and explicit reset of display preferences. No unimplemented updater, reboot or power action. |
| Tools > Recipes | Create, validate, select and export portable bracket plans. The offline CLI executes them separately; there is no pretend Run button. |
| Tools > Devices | Cached backend/mode/connection facts, with explicit absence of physical sensor/lens/FPGA/MCU support. |
| Tools > Diagnostics | Read-only current request, requested settings, runtime and local filesystem facts; explicit create-only JSON export. |
| Tools > System | Actual host OS/architecture/Python/Qt identity and prototype limits. |

App navigation, menu entry, saved-image selection and export do not submit a
photograph or replace the current request ID. Tool apps are reachable directly
from the Taskbar or through MENU > Tools; hiding the Taskbar leaves photography
controls and MENU available.

## State and image boundaries

Display preferences use `shell-preferences.json` beside existing UI request
state. They affect framing, appearance and displayed-image brightness only,
not sensor timing, RAW values or physical backlight. The store uses bounded
strict JSON, atomic commit-before-acknowledgment and unsafe-path checks. Corrupt
preferences preserve the input; explicit reset keeps a backup. Startup does not
replay a prior shot. Recipe/diagnostic files use the UI data directory's `apps/`
subtree; the camera journal, request state and preferences remain separate.

Live view is a Python-generated 320x240 synthetic Bayer scene, capped at three
generated frames per second. Native C++ HF-Sim independently generates and saves
captured Bayer/DNG; playback converts that retained DNG. Live view is not sensor
readout or a downsample of the native buffer. No measured preview rate, real
camera, physical display or hardware calibration is implied.

## Run

Build the current authority into a dedicated directory and select its exact
executable. An older binary without persistent-settings confirmation correctly
holds capture after an unconfirmed parameter edit.

```powershell
python tools/build_native.py --build-dir build/native-r11
$env:HF_NATIVE_DAEMON = (Resolve-Path build/native-r11/Release/hf-camerad.exe).Path
python ui/on-device/main.py --data-dir var/DaOS-camera
python ui/on-device/main.py --data-dir var/DaOS-camera --app settings
```

The default camera interface is 800x600. Select the development workspace explicitly:

```powershell
python ui/on-device/main.py --interface console --width 1280 --height 800 --data-dir var/DaOS-console
```

Use separate data directories for concurrently running authorities. On Unix,
the builder normally emits `build/native-r11/hf-camerad`; select that path with
`--native-command` or `HF_NATIVE_DAEMON`. Do not substitute a Windows binary or
infer architecture from a filename. `--backend reference` explicitly selects
the Python simulator; native failure never silently changes capture backends.

`--app` navigates without taking a photograph. `--kiosk` uses actual Qt screen
geometry. `--offscreen --screenshot NEW.png` renders the selected UI without a
shutter action; it is software UI evidence, not physical-display validation.

See [R14 verification](../testing/round14-taskbar-ui-verification.md),
the [ARM64 Qt review](../../build/DaOS_Camera_UI_R14_ARM64_final_002/UI-review.html),
[Windows comparison](../../build/DaOS_Camera_UI_R14_verified/UI-review.html) and
[Taskbar gesture/transition frames](../../build/DaOS_Camera_UI_R14_ARM64_taskbar_motion_v2/UI-taskbar.html)
for the actual completed checks. Sampled Qt frames do not establish a physical
display frame rate. The preserved
[R13 neutral-UI verification](../testing/round13-neutral-camera-ui-verification.md),
[R12 camera-first verification](../testing/round12-camera-first-verification.md)
and their original screenshots remain unchanged.
The frozen R10 ARM64 BSD image retains its earlier UI. New source and review
screenshots do not silently update that image. The
[historical R11 report](../testing/round11-camera-apps-verification.md) preserves
its separate results and rejected camera-body layout.
