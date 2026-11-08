# HassleFree FF-1 engineering source handoff

The new **R14 Raspberry Pi 4 ARM64 BSD image candidate 002** is assembled at
`build/DaOS_R14_RPi4_ARM64/DaOS-R14-RPi4-arm64.img.xz`, with its independent
[verification report](build/DaOS_R14_RPi4_ARM64/VERIFICATION.md) and
[Pi build/flash guide](system/freebsd/raspberrypi/README.md). All five native
read-only filesystem checks passed. An actual QEMU `virt` ARM64 boot displayed
the user-supplied cat Boot Logo, with exact PNG pixel agreement, and reached
the R14 camera and synthetic preview. The observed System view identified
FreeBSD 15.1-RELEASE-p6, arm64, Python 3.12.15 and Qt 5.15.19.
This is a new image identity, separate from frozen R10 and earlier source/UI
deliveries. A separate isolated ARM64 run completed app navigation, parameter
confirmation, one native synthetic capture, playback and DNG export. Offline
inspection verified the sole request and byte-identical 25,666-byte retained/
exported DNG. Application and Xorg exited 0 without a forced stop; the EFI
console's serial log did not show an `All buffers synced` marker, so none is
claimed. A second full kernel boot recovered the same original request, settings
and retained/exported DNG without another shutter. Independent stopped-disk
comparison found the capture row, configuration and DNG unchanged; both guest
sessions recorded application/Xorg exit 0 with no forced stop.
Pi 4B/400 physical boot and Taskbar gesture acceptance remain pending. Current workspace
documentation can postdate its image build; the image receipt and source
package 002 manifest identify the files actually included.

The current R14 source adds a **left icon-only Taskbar, open by default** to
the camera-first native Qt interface. Swipe left to hide it and right from
the left screen edge to reveal it. Seven icons directly open the camera,
photos, recipes, settings, devices, diagnostics and system views. A shared
220ms transition moves the Taskbar and scales the complete aspect-fit preview;
HUD text and touch controls retain their normal size.

The interface keeps **opaque black, white and neutral-gray UI surfaces**,
the viewfinder, Q quick parameters, grouped MENU and single-image playback.
Grouped MENU settings and its Tools entries remain available alongside direct
Taskbar routes. The R11 desktop-style layout is preserved as
`Console.qml`, selected explicitly with `--interface console`; it is no longer
the default camera-body UI. Read the [camera/menu guide](docs/product/camera-apps.md),
[adapter/provenance](docs/architecture/retail-camera-ui-adapter.md) and
[R14 verification](docs/testing/round14-taskbar-ui-verification.md).
This is the user's appearance override of the Retail reference palette, not
a claim that the original Retail hues are monochrome. Photographic pixels
retain their source color. Taskbar gestures and transitions submit no capture,
configuration change or replacement request ID; camera authority and image
processing remain unchanged. The shutter is held during a Taskbar gesture or
animation, and Space/Enter on a focused Taskbar icon only navigates.
The same camera authority and durable original-request behavior remain in use.
Display preferences, recipe editing/export and read-only diagnostics retain
their separate stores. Python generates the independent synthetic preview;
native C++ HF-Sim generates and saves captured Bayer/DNG.

The existing R10 ARM64 image and source ZIP are preserved. UI screenshots and
host/reference-guest execution identify their actual platform; they do not
imply a rebuilt appliance image, physical display or real sensor. The sections
below retain the prior frozen R10 delivery identities and historical results.
The [R11 report](docs/testing/round11-camera-apps-verification.md) preserves its
test results and failed ARM review separately from the redesigned R12 UI.

Current R14 review paths are the [ARM64 camera UI review](build/DaOS_Camera_UI_R14_ARM64_final_002/UI-review.html),
[Windows comparison](build/DaOS_Camera_UI_R14_verified/UI-review.html) and
[Taskbar gesture/transition proof](build/DaOS_Camera_UI_R14_ARM64_taskbar_motion_v2/UI-taskbar.html).
The source-only snapshot path is
`build/HassleFree_FF1_R14_Taskbar_Source_2026-11-05.zip`; the R14 verification
report and external delivery manifest identify completed artifacts and hashes.
The filename retains the project's 2026-11-05 snapshot date; this Taskbar
request and its checks run on 2026-11-06 UTC. No new OS image is implied.
The source bundle excludes images, runtime databases, authentication, generated
binaries and build outputs; the screenshot review is delivered separately.

The [R13 verification](docs/testing/round13-neutral-camera-ui-verification.md),
its [ARM64 review](build/DaOS_Camera_UI_R13_ARM64_final_001/UI-review.html),
[Windows comparison](build/DaOS_Camera_UI_R13_verified/UI-review.html), neutral
background proof and `build/HassleFree_FF1_R13_NeutralUI_Source_2026-11-05.zip`
remain unchanged. The R14 Taskbar proof takes no photographs; the native camera
review remains a separate one-synthetic-capture test.

The preserved [R12 ARM64 review](build/DaOS_Camera_UI_R12_ARM64_final_003/UI-review.html),
[Windows review](build/DaOS_Camera_UI_R12_verified/UI-review.html) and
[R12 verification](docs/testing/round12-camera-first-verification.md) retain
their original identities. Source-only
`build/HassleFree_FF1_R12_CameraFirst_Source_2026-11-05.zip` remains unchanged:
167,139,108 bytes, SHA256
`0267ea430924ec1f95ac6e886b9641932ec5eecc3535d707f7bf9dbb081b4479`.

## Frozen R10 delivery

The active R10 platform is **ARM64 DaOS BSD Unix**, based on source-built
FreeBSD 15.1-RELEASE-p6 world/kernel and a native ARM64 camera authority.
Follow [the pipeline](system/freebsd/README.md),
[verification](docs/testing/round10-freebsd-arm64-verification.md) and
[artifact/run guide](system/freebsd/DELIVERY.md).

Final image003 passed all five native read-only filesystem checks and an
independent Linux EFI check, then booted its complete 800x600 Qt body UI.
One synthetic capture at10100us/gain1.0 survived a full kernel restart with
the same request ID; retained review and GUI export also completed. The
separate [acceptance receipt](system/freebsd/image-boot/evidence/image003-ui-acceptance.json)
binds actual screenshots and both clean shutdowns. No network, SSH, injected
key, NoCloud or host share was used by these appliance boots.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Raw ARM64 image003 | 3,187,712,000 | `69c824a3aa1f992fcb38e285145e4549c76107cab95a2a26e2e391e401b4942a` |
| Compressed ARM64 image003 | 362,424,748 | `e06c24c415894e9e396b7b9af147fb25953c585e01e1ec44fd71994f51dd05a4` |

Reference ARM64 tests separately passed four CTests, 90/98 focused Python
checks (eight skips), 45 workflow tests and 23 Qt interaction tests. Counts
overlap where documented and must not be summed into a unique total.

The image is a tested **synthetic camera appliance prototype**, not a
completed physical FF-1. Qt/Python include binary packages. Original DaOS
framework source integration is absent, BSD A/B updating is absent, and
physical hardware release gates below remain open. Source-only follow-ups
after image003 are identified in the delivery guide; hashes distinguish
those changes from the frozen tested image. No repository was published.

## Historical R9 and Linux handoff

The historical R9 changes and exact verification are recorded in the
[R9 report](docs/testing/round9-verification.md) and
[requirements delta](docs/testing/requirements-status-r9.md). They add durable
settings, detached synthetic calibration snapshots and an executable virtual
A/B boot/health/fallback model. R8 and earlier source ZIPs remain unchanged.
The [DaOS architecture assessment](docs/architecture/daos-relationship.md)
distinguishes independently implemented patterns from actual framework reuse;
there are currently zero imported DaOS source files.

For the current scope and exact remaining requirements, start with the
[R8 requirement-by-requirement audit](docs/testing/requirements-status-r8.md).
The original user specification is preserved separately as requirements text.
The [offline basic guide](docs/testing/offline-basic-round8.md) describes the
standalone CLI; workflow `resume/pause/cancel/list` uses saved plans and original
request IDs. Historical R7/R6 source ZIPs remain unchanged.

This snapshot is an independently runnable **software camera and engineering
prototype collection**. It is not a working full-frame camera, manufacturing
release, sensor driver or validated open-hardware product. Start with [README](README.md)
and the [round-seven virtual-target instructions](system/targets/README.md). The
[round-six results](docs/testing/round6-verification.md) and original source ZIP
remain preserved. No external repository
was published and no service was installed or enabled for this handoff.

The prior round-five Library snapshot is preserved separately. Round six adds
saved-photo browsing/thumbnail/export, portable workflow recipes, source-bound
static ARM64 cross-builds and an actual full-system ARM64 Linux virtual target.
Round seven adds a pinned Debian ARM64 Python/Qt runtime, deterministic regular
ext4 image generation and actual ARM QML capture/review/export with persistent
same-ID recovery after a guest reboot. Follow the unified recipe for a fresh
online acquisition or a verified offline cache replay. No host image mount,
service installation, root access or credentials are needed by the build.
See [change history](docs/CHANGELOG.md); the historical
[round-five verification](docs/testing/round5-verification.md) remains intact.

## Run from a clean extraction

Use Python 3.10 or newer; the verified Windows run uses Python 3.12 and the
Debian x86_64 run uses Python 3.13. The basic Python simulator uses only the
standard library and needs no account, cloud model or sensor hardware:

```sh
python -m camera.camerad.server --data-dir var/hf-sim --port 8787
```

Open `http://127.0.0.1:8787/`, acquire control and capture `sim-small`. The
development API is loopback-only and has no production authentication. Stop
with Ctrl+C. For an independent basic capture without the browser or workflows:

```sh
python -m camera.camerad --data-dir var/hf-basic capture --exposure-us 10000
```

Persist a baseline with `python -m camera.camerad --data-dir var/hf-basic
configure --exposure-us 11700`. A later `capture` without exposure/gain/mode
flags inherits the saved settings; explicit capture overrides do not change
that baseline. The independent CLI also supports `bind-calibration ARTIFACT`
and `clear-calibration`. Read the [binding format](camera/calibration/BINDING.md)
before using them: the supported artifacts are synthetic metadata, with both
hardware calibration and pixel correction explicitly false.

Retain the printed UUID before capture. After interruption, query that same UUID;
an unknown result must not be automatically retried with another capture.
Keep each authority's data directory separate. Never place health/recovery
scratch state in owner photographs, calibration or source trees.

For C++ capture, install a C++17 compiler and CMake, then run:

```sh
python tools/build_native.py --build-dir build/native-r11
python -m tools.check_native_dependencies
```

Windows uses Visual Studio C++ Build Tools; Linux can instead use the offline
direct compiler helper `python3 tools/linux_build.py --build-dir build/linux-native`.
The two vendored C++ dependencies are included and checksum-verifiable; no
dependency download occurs in those builds. See [native usage](camera/native/README.md).
For the independent Qt/QML interface, install `requirements-ui.txt` in an isolated
environment and select the freshly built authority explicitly. On Windows:

```powershell
$env:HF_NATIVE_DAEMON = (Resolve-Path build/native-r11/Release/hf-camerad.exe).Path
python ui/on-device/main.py --data-dir var/DaOS-camera
```

On Unix the corresponding path is normally `build/native-r11/hf-camerad`.
The default interface is the800x600 camera body; `--interface console` selects
the preserved development workspace. See the [UI guide](ui/on-device/README.md).

## Verify and reproduce

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s hardware/tools -v
python -m unittest discover -s fpga/testbench -v
node --check daos/console/app.js
```

The complete pinned optional test/UI environment was resolver-checked on Python
3.12; use Python 3.12 for that environment. The basic standard-library simulator
supports Python 3.10+, while individual optional wheels have narrower ranges.
`requirements-test.txt` supplies optional independent decoders, Schema checks
and transfer/tool support. Tests report explicit skips when optional tools are
missing. Enable `HF_FULL_FRAME_TEST=1` for synthetic 6064x4040 DNG I/O. The complete
verification run also needs native executables, PyQt5, Node and Icarus Verilog.
[FPGA instructions](fpga/README.md) describe actual RTL tools and their limits.
[Linux instructions](system/linux/README.md) describe host packaging, dedicated
scratch health checks and the A/B trial model. None of these exercises changes
an installed release, a boot partition or a physical camera.

The independent review reports in `docs/testing/*round5.md` explain earlier
verified defects and fixes. Round-four/five evidence remains tied to those
historical source revisions; the round-six report describes the newer state.
Remote CI has not run. Tests do not establish hard real-time timing, arbitrary
power-loss integrity, photographic color accuracy or hardware endurance.

For the later ARM64 work, follow [the cross-build/deployment guide](system/arm64/README.md)
and [generic QEMU virt target](system/qemu-virt/README.md). User-mode emulation,
full virtual-kernel execution and an actual board are distinct verification
levels. ARM64 Python/Qt on a physical camera and its peripherals remain untested.

## Source, privacy and licensing inventory

All five supplied prototype hardware archives and their editable extracted
sources are preserved byte-for-byte under `hardware/imports`. Their Library
IDs, versions, archive/child hashes and status are recorded in
[provenance.json](hardware/imports/provenance.json). Mechanical v0.3 is the
active integration-review concept; v0.1/v0.2 remain historical. Use the
[integration overlay](hardware/interface-review.md), not an imported filename,
to determine what has actually been validated.

The bundle's `SOURCE-MANIFEST.json` lists every delivered file's byte length,
SHA-256 and origin/rights classification. Run
`python tools/source_bundle.py --verify /path/to/source.zip` to check that the
archive and manifest agree. This is an integrity check, not a digital signature
or an assertion that a supplied manifest is trustworthy.

The archive excludes browser/native UI screenshots, runtime capture/state files,
credentials, unrelated root files, caches and compiled outputs. Hardware CAD/PCB
renders remain part of unchanged prototype sources. Evidence copies redact
personal source/Temp prefixes and record the original source hash alongside the
delivered hash. Historical evidence may name excluded screenshots or original
artifact hashes; these are workspace evidence references, not missing source.
All original workspace files and imported archives remain unchanged.

[License inventory](licenses/INVENTORY.json) distinguishes confirmed third-party
notices from proposed project licenses. No DaOS repository code was copied.
Proposed GPL/Apache/CERN/CC license choices still require project/rightsholder
confirmation; this private engineering handoff does not turn them into grants.
Do not describe the snapshot as a publicly licensed release or redistribute
third-party material under a new blanket license.

## Hardware gates that remain open

- IMX410 availability, authorized initialization/register/packing information,
  package and optical-stack drawings, and permission to publish necessary data.
- Sensor PHY and SLVS-EC receiver, CDC, real DMA, board-specific drivers,
  libcamera/V4L2 integration and synthesis/timing/bitstream validation.
- Selected sensor/carrier/power hardware, verified pinouts/voltage domains,
  target MCU, real power sequencing and independent hardware recovery.
- Correct sensor envelope and thermal closure, validated E/EF bayonets and HUMI
  datum/tolerances, actual lenses, fabrication and repeated optical measurements.
- ARM BSP/image, real A/B bootloader/update integration, physical calibration,
  slow-media/power-cut/endurance tests and an independent manufactured replica.

The [supplier gate](docs/manufacturing/supplier-gate.md) and
[release matrix](docs/testing/release-matrix.md) define the next acceptance steps.
