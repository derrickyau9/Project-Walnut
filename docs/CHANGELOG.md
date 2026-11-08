# Engineering change history

## Round fourteen - 2026-11-06 UTC

- Added a default-open, icon-only left Taskbar with seven direct app routes,
  accessible names and hover tooltips. Left swipe hides it; right swipe from
  the 24px screen edge reveals it, with arrow handles as a tap alternative.
- Matched Taskbar motion, content inset and aspect-fit preview scaling through
  a 220ms ease-in/ease-out transition. The complete source frame remains visible
  without cropping/stretching; HUD text and touch controls retain normal size.
- Distinguished horizontal swipes from taps, vertical and out-and-back drags.
  Taskbar gesture/animation holds the shutter; focused Space/Enter navigates
  icons without taking a photograph. No authority contract, camera setting,
  original request or image pixel is changed by hide/reveal.
- Retained R13's opaque black/white/neutral-gray surfaces and grouped MENU.
  Added separate Taskbar interaction/animation evidence with no photographic
  capture; native camera review remains a separate one-shot HF-Sim test.
- Preserved R13/R12 source snapshots and evidence and the frozen R10 BSD image.
  Exact current platform checks are in the
  [R14 verification](testing/round14-taskbar-ui-verification.md); sampled UI
  frames do not establish physical-display FPS or a new OS-image boot.

## Round thirteen - 2026-11-06 UTC

- Applied the user's requested solid black, white and neutral-gray UI
  backgrounds to the camera body, menus, Q controls and local app surfaces.
  Removed tinted/translucent panels and colored highlights; selection uses
  contrasting white text on gray. Preview and photographic pixels keep their
  source color; capture processing and camera routes are unchanged.
- Preserved the pinned Retail palette under `referencePalette` and marked the
  active neutral theme as an explicit `cameraAppearance` user override, rather
  than misattributing neutral hues to the original Retail cascade.
- Added focused palette/rendered-background checks and separate R13 review
  paths. Actual results belong to the
  [R13 verification](testing/round13-neutral-camera-ui-verification.md).
- Preserved the successful R12 source ZIP, review evidence and R10 ARM64 BSD
  image. The R13 source filename retains the 2026-11-05 project snapshot date;
  current execution/reporting identifies 2026-11-06 UTC. No new OS image or
  physical camera validation is implied.

## Round twelve - 2026-11-05

- Replaced the default desktop workspace with an 800x600 camera-first body UI:
  full viewfinder, perimeter exposure status, Q quick parameters, grouped MENU
  and single-image playback with explicit INFO and DNG export.
- Moved the left category rail into MENU and optional apps behind MENU > Tools.
  Kept the R11 workspace as `Console.qml` and `--interface console` for development.
- Kept camera settings under the existing authority, with acknowledgment/readback
  before shutter release. Space auto-repeat and menu/dialog keyboard input cannot
  dispatch repeated captures; unresolved original requests remain held.
- Added a separate camera-first Qt interaction suite and source-bound screenshot
  runner. Live preview is explicitly Python-generated synthetic imagery, distinct
  from native C++ HF-Sim capture and retained-DNG playback.
- Corrected a verified FreeBSD preference conflict false-positive by combining
  stable file identity with bounded content checks instead of using ctime alone.
  A reproduced FIFO-swap hang is rejected through nonblocking opens and fstat,
  with a bounded real-FIFO regression. Q gain controls have explicit contrasting
  touch indicators. The short-lease export fixture acquires only after QML load,
  retaining its original0.6s lease,1.3s blocked export and no-retry behavior.
- Preserved the R10 BSD ARM64 image and R11 review evidence. Exact R12 platform
  results and limits are recorded in the [R12 report](testing/round12-camera-first-verification.md).

## Round eleven - 2026-11-05

- Adapted the pinned DaOS Retail light/night tokens, compact/expanded left
  navigation, application launcher and bounded command search to native Qt.
- Added grouped real exposure/gain/mode settings, separately persisted display
  preferences, retained Photos, validated recipe editing/export and read-only
  device/diagnostic/system apps.
- Preserved camera authority, original capture IDs and explicit unknown outcomes;
  navigation and display preferences never submit captures.
- Fixed verified Qt5 component creation, plain-Text font fallback and QML Number
  brightness conversion defects; retained strict model/file validation.
- Kept source provenance and actual-platform tests separate from the unchanged
  R10 BSD image. No Retail business modules or third-party UI assets imported.

## Round ten - 2026-11-05

- Added the FreeBSD ARM64 source-build and cross-build route, target-native
  validation, checksum/license-bound runtime collection and script-free image
  assembly with separate state, calibration and photo UFS volumes.
- Built pinned FreeBSD world/kernel, executed ARM64 camera/workflow/Qt suites,
  then booted the exact assembled image with RAMFB/efifb and the native Qt UI.
- Diagnosed invalid initial FAT geometry, incompatible virtio-GPU framebuffer
  mapping and cropped kiosk geometry; retained failed evidence and fixed each.
- Completed one synthetic capture, full kernel restart, same-ID retained review
  and GUI export without network or host sharing. Image003 and source-only
  follow-up fixes are explicitly distinguished in the R10 report/delivery guide.
- Branding is DaOS. No original DaOS framework code, physical sensor support,
  measured hardware validation or BSD A/B update implementation is claimed.

## Round nine - 2026-11-05

- Added commit-before-acknowledgment persistent device settings to both native
  and reference authorities, independent CLI configuration and responsive Qt
  settings readback. Capture overrides preserve the saved baseline.
- Added content-addressed immutable synthetic calibration bindings, independent
  C++ SHA256/package validation, idle/lease-guarded bind/clear, explicit v2
  capture snapshots and restart/duplicate-request provenance preservation.
  These select metadata only; no measured calibration or RAW correction is claimed.
- Hardened journal SQL/JSON/request context, state/client evidence validation
  and missing-query handling while retaining deliberate unknown outcomes.
- Added real generic ARM64 image trial/confirmation/fallback with bounded,
  durably consumed attempts and preservation checks for actual settings,
  bindings and existing captures. Same-code artificial versions remain a model,
  not a production or authenticated firmware updater.
- Recorded the exact lack of imported DaOS framework code, the independently
  implemented patterns and the missing integration. See the
  [R9 verification](testing/round9-verification.md) and
  [architecture assessment](architecture/daos-relationship.md).

## Round eight - 2026-11-05

- Preserved the complete original specification and mapped its fourteen chapters,
  acceptance gates and release tests to source/evidence and precise open work.
- Added independent offline basic CLI review/export and strict backend selection.
- Fixed cancelled/paused workflow reconciliation, local execution ownership,
  lease-scoped restoration and saved-plan offline continuation commands.
- Corrected CFA plane/layout and linearization handling and finite JSON parsing.
- Bound capture directories, staging and produced/published file identities and
  contents; preserved foreign files and uncertain outcomes after publication.
- Verified actual native ARM CLI plus saved workflows across two Linux/Qt boots.
  See [R8 verification](testing/round8-verification.md); R7 remains preserved.

## Round seven - 2026-11-05

- Authenticated 170 pinned official Debian ARM64 binary packages; isolated
  script-free POSIX staging with per-entry mode/link/hash/ELF verification.
- Deterministic rootless ext4 construction, verified kernel module closure,
  read-only root and private data disk; no host mounts or services.
- Actual ARM Python/Qt/QML capture, saved-photo review/export and same-ID
  recovery through two full-system Linux boots without host-Python fallback.
- Bounded unified target recipe with fresh native/kernel and offline-cache
  paths, complete source/deployed-file binding, guarded output and child cleanup.
- Independent DNG/pixel verification and four matching root-image builds.
  See [round-seven results](testing/round7-verification.md).
- Source handoff privacy/runtime exclusions and exact upstream Debian binary/
  metadata provenance. R6 and all hardware prototype imports remain preserved.

## Round six — 2026-11-05

Changes after the independently delivered round-five snapshot:

- Native/reference completed-capture pagination, metadata-only asset queries,
  pinned verified reads, actual retained-DNG thumbnails and create-only exports.
  HTTP uses binary bodies for images/files, with no raw samples in JSON.
- Qt/console retained galleries and review/export; separate bounded media work
  preserves UI/lease responsiveness. Existing unknown requests are not replaced
  or acknowledged by browsing.
- Strict, bounded `hasslefree.workflow-recipe.v1` import/export, recent-job
  selection and offline CLI recipe execution. Recipes omit credentials, request
  IDs, local paths and results; import is separate from execution.
- Static ARM64 C++ cross-builds with actual QEMU user execution, architecture/
  static-link/source/artifact validation and recorded toolchain inputs.
- A generic full ARM64 Linux kernel/initramfs target: native assertions, worker
  no-overwrite, actual daemon capture/restart/same-ID query and independent DNG
  verification. Two fresh init/initramfs builds are byte-identical with the same
  inputs/toolchain. This is not a Zynq BSP or hardware result.
- A separate static ARM64 deployment profile with native-host guard, verified
  package inventory and explicit storage separation; no emulator is hidden in
  the deployable release.
- Evidence-copy privacy handling now covers ARM64 and virtual-target reports.

See [round-six verification](testing/round6-verification.md),
[retained media](testing/retained-media-round6.md),
[UI/workflows](testing/ui-workflow-round6.md) and
[remaining hardware gates](testing/round6-hardware-gates.md).

## Preserved round-five snapshot

File: `HassleFree_FF1_Source_Engineering_Handoff_2026-11-05.zip`

Library ID: `libfile_ad6ff83270b081918bf50337cc5aea22`

SHA-256: `7e9acde4b075834600e4cc2ed9a2db01972ae27cf0f1891800b1e318c1aafe5b`

It contains 481 source files plus its manifest, the five unchanged prototype
hardware archives and their extracted sources, provenance/rights inventory,
and its then-current verification reports. It was not overwritten by round-six
development. The [round-five report](testing/round5-verification.md) records
259 Windows passes/2 POSIX skips, 85 targeted Linux passes and a clean native
build; those historical counts must not be presented as the newer revision's
test result.

No real sensor capture, physical board deployment, manufacturing release or
project-wide public license grant is claimed by either snapshot. Original
hardware imports and earlier source/validation evidence remain preserved.
