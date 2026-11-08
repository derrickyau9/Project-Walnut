# ARM64 Qt runtime and UI verification - round 7

Date: 2026-11-05. This report is separate from the preserved round-six source
snapshot and evidence. ARM package acquisition/staging and full-system image
construction are maintained by their respective tools and reports; this file
records the on-device application's runtime requirements and actual UI exercise.

## Initial audit

Round six ran the Qt application on Windows, and ran static ARM64 native code
under QEMU. Its packaged API smoke explicitly used host Python with an external
ARM emulator. That combination did not run ARM Python, ARM PyQt or an ARM QML
engine. The earlier static ARM package did not contain the on-device UI or its
Qt runtime, so those results could not establish ARM UI compatibility.

The application requires Python standard-library modules, PyQt5 `QtCore`,
`QtGui`, `QtQml` and `QtQuick`, with QML imports `QtQuick 2.15`,
`QtQuick.Controls 2.15` and `QtQuick.Layouts 1.15`. The UI smoke additionally
requires `QtTest`. The Qt offscreen platform plugin, software Qt Quick renderer,
Controls 2/Templates 2, Layouts, Window and Qt Quick QML plugins, and usable fonts
must be present. The preview imports the local standard-library Bayer generator;
no NumPy, browser, HTTP server, Node or model service is required for native UI
capture. The native client also imports the standard-library SQLite module.

The source's Windows font configuration is conditional. Linux uses `Sans Serif`
and `monospace`; no Windows-only control or required API was found. The Windows
`requirements.txt` pin remains its desktop verification profile. The separately
staged official Debian ARM64 runtime uses its own authenticated package versions;
it is not represented as the Windows runtime.

## ARM smoke entry point

`tests/ui/arm64_smoke.py` refuses non-Linux/non-AArch64 Python and root execution
before creating output. It verifies actual AArch64 ELF64 identities for Python,
all five loaded PyQt extension modules and the native authority. The full-system
profile runs ARM Python and the native ARM authority directly, without a host
Python fallback or reference camera substitution. An explicit `qemu-user` prefix
is supported solely for separately labeled diagnostic runs; it is never silently
selected.

With an authenticated staged runtime, the full-system guest uses a read-only
application/root filesystem, UID 1000 and a separate writable `/data` filesystem.
The image bootstrap sets:

```sh
QT_QPA_PLATFORM=offscreen
QT_QUICK_BACKEND=software
QSG_RENDER_LOOP=basic
QML_DISABLE_DISK_CACHE=1
PYTHONDONTWRITEBYTECODE=1
XDG_RUNTIME_DIR=/run/user/1000
```

The runtime directory must be owned by that user with mode 0700. Qt platform/QML
plugins and fonts must resolve within the guest's staged runtime; no host Qt
plugins may be substituted. When needed, staged paths are supplied explicitly
through `QT_PLUGIN_PATH`, `QML2_IMPORT_PATH` and font configuration. No physical
framebuffer, GPU, display server or touchscreen is implied by this profile.

The first boot runs:

```sh
python3 -B /opt/hasslefree/tests/ui/arm64_smoke.py --native-daemon /opt/hasslefree/bin/hf-camerad --output-dir /data/ui-r7 --execution-mode qemu-system --phase first
```

It requires a new output directory, creates the actual QML window, changes
exposure using a QtTest key event, presses the QML shutter, waits for a complete
native capture, queries that UUID, opens the retained-photo dialog, selects its
row, decodes the actual saved-DNG thumbnail, and exports an identical copy through
the QML control. It writes complete/gallery software screenshots, the retained
PNG, an exported DNG, safe metadata and the durable UI/camera state. Shutdown must
close its native process, and the native SQLite journal must contain exactly one
completed record.

A second boot reuses the data filesystem and runs the same command with
`--phase resume`. It loads the saved UI UUID without a shutter action, checks the
unchanged native/source hashes, confirms the same complete record and DNG bytes,
and repeats retained review/export. Exactly one journal row must remain. The
`all` phase is available for a same-boot UI/authority reopen, but is not itself
proof of data persistence across a guest reboot.

Per-phase files are `first.json`/`resume.json`, `first-complete.png`/
`resume-complete.png`, `first-gallery.png`/`resume-gallery.png`,
`first-retained.png`/`resume-retained.png`, and `first-copy.dng`/
`resume-copy.dng`. The session journal/photo and `device-ui.json` remain beneath
that same explicit output directory. Serial `HF_ARM64_QT_*_PASS` records contain
bounded metadata; compact `HF_IMAGE_UI_RESULT` markers include the report hash.
Neither report contains control tokens or raw pixel arrays. Screenshots may show
guest fixture paths and UUIDs and are workspace-only evidence unless separately
requested for a deliverable.

The smoke uses a bounded 30-second native transport timeout and a configurable
5-300-second UI condition deadline to accommodate CPU emulation. Its output
records the actual Python, Qt compile/runtime and PyQt versions, effective UID,
ELF/source hashes, capture/frame identity, DNG/export hashes and render profile.

## Executed verification

The build-host refusal regression passed:

```sh
python -B -m unittest tests.test_ui_arm64_smoke -v
```

It verifies that ordinary host Python exits before creating an evidence directory
or publishing ARM pass markers. This guard result is not ARM runtime evidence.
Actual ARM imports passed using the staged official Debian ARM64 runtime under
explicit QEMU user mode. The full application then passed two separate
full-system QEMU boots using `build/arm64-image-r7-b`. The measured guest profile
is Linux `aarch64`, effective UID 1000, Python 3.13.5, PyQt 5.15.11 and Qt runtime
5.15.15. All loaded Qt extensions and the native authority passed AArch64 ELF
checks. No product UI compatibility patch was needed. The image overlay also
includes `ui/on-device/main.py`, making the normal launcher available alongside
the QA entry point.

| Actual guest phase | Result |
| --- | --- |
| First boot | Passed in 14.778 seconds: QML exposure change/shutter, original-ID query, retained review/export, native process closed, one complete journal row |
| Second boot with the same data disk | Passed in 14.316 seconds: saved UUID queried without shutter, retained review/export, unchanged DNG, native process closed, still one complete journal row |
| Visual QA | Four actual ARM complete/gallery PNGs inspected at 1024 x 680; fonts, labels, controls, UUID/path, retained caption and export confirmation are legible |
| Read-only system disk | SHA-256 unchanged across both boots; no host mounts, filesystem sharing or network |

The persisted request/capture/frame identity is
`f63bfb93-223d-4d55-ab60-0a7480777166`. Both guest exports and both separately
extracted source DNGs contain 25,662 bytes and SHA-256
`95e539f204daf53e58423847f4ea9ccdc091f023a6dd66611daf45ac08dddcd4`.
The retained PNG is 64 x 48 (1,831 bytes), derived from the original 128 x 96
saved Bayer image. Metadata preserves its actual exposure of 10,100 microseconds.
After reboot the capture settings show the authority's defaults for the next
shot; they do not rewrite the retained record's exposure.

The immutable rootfs SHA-256 is
`40acc21e909b4ab326ec0f8c1390b2662d5e8e7b2c372c9b0aac967fba04a777`.
Full guest reports, source/runtime/PNG hashes and visual inspection flags are
preserved in [round-seven UI validation](../../ui/on-device/evidence-round7/validation.json),
with byte-exact [first](../../ui/on-device/evidence-round7/first.json) and
[resume](../../ui/on-device/evidence-round7/resume.json) guest reports. Original
image build/serial/boot evidence remains under `build/arm64-image-r7-b`.
Host boot-tool guard hardening between phases is identified by each original
boot report's tool hash; guest application, runtime, native and rootfs bytes
were unchanged. All four screenshot paths are textual, workspace-only references:
`ui/on-device/evidence-round7/first-complete.png`, `first-gallery.png`,
`resume-complete.png` and `resume-gallery.png` in that same directory. They are
excluded from the minimal source handoff and are not embedded as dangling image
links. No broad desktop UI suite was repeated because product UI code did not
change. The previous round-six archive and evidence were not modified.

Physical camera-body display/touch, GPU/display drivers, real sensor acquisition,
measured calibration, power management and optical behavior remain unvalidated.
Offscreen software rendering under QEMU cannot pass those hardware gates.
