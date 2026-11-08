# DaOS Retail appearance and interaction adapter for Camera

Reference inspected: `derrickyau9/DaOS-iHOMI` commit
`10d233e316e1c258e8aa365f6a6de4d82d10c7f5`, 2026-11-05. The user explicitly
requested the DaOS Retail interface logic/appearance, camera settings, menus,
left navigation and application entries. This record identifies the actual
reference and the native adaptation boundary. It is not a claim that Retail's
business application or general framework runtime has been ported.

The camera-first body introduced in R12 retains the viewfinder by
default, Q controls, grouped MENU and single-image playback. R12/R13 placed the
category rail inside MENU and applications behind MENU > Tools. R14 adds a
default-open icon-only Taskbar for direct app routes, with swipe hide/reveal;
grouped MENU remains available. The R11 dock,
launcher and command-search workspace is preserved as the explicitly selected
development console (`--interface console`), not the current camera layout.

R13 follows the user's subsequent request for UI backgrounds to be pure black,
white or gray. Active camera UI surfaces are opaque and achromatic. This is
an explicit **user override of the Retail reference colors**, not a claim
that the source Retail theme used neutral hues. Preview and photograph pixels
retain source color; image processing is unchanged. R14 keeps that palette
while extending UI navigation; it changes no camera capability contract.

## Exact reference and selected data

The [theme token file](../../ui/on-device/retail-theme.json) records SHA256 and
computed Git blob SHA1 identities of the four inspected, locally staged UTF-8
files. Raw reference modules stay outside the source handoff. The computed blob
IDs describe those inspected bytes; no new publisher signature is asserted.

| Source at the pinned revision | Selected evidence | Camera use |
| --- | --- | --- |
| [public/daos-design.css](https://github.com/derrickyau9/DaOS-iHOMI/blob/10d233e316e1c258e8aa365f6a6de4d82d10c7f5/public/daos-design.css#L3080) | The late day/night overrides at3080-3114 supply the reference cool white/slate surfaces and readable text. Controls/cards at3168-3238 use44px minimum hit areas,12px control corners and20px card corners. | Reference palette and selected design dimensions. R13 active hues are the user's neutral override; CSS is not imported into Qt. |
| [public/daos-design.css final dock rules](https://github.com/derrickyau9/DaOS-iHOMI/blob/10d233e316e1c258e8aa365f6a6de4d82d10c7f5/public/daos-design.css#L3322) | Desktop navigation reserves72px, with44px icon buttons and accessible labels. The shared glass rail is inset inside this gutter. | R11 development-console navigation and R14's independently implemented 72px icon Taskbar. R14 appearance is opaque neutral gray and its preview transition is camera-specific; this is not a glass/CSS port. |
| [public/daos-activity-glass.css](https://github.com/derrickyau9/DaOS-iHOMI/blob/10d233e316e1c258e8aa365f6a6de4d82d10c7f5/public/daos-activity-glass.css#L73) | A single tinted surface encloses the dock; per-button filters are avoided. Reduced-transparency/high-contrast conditions use solid fallbacks. | Source reference only for R13 color/rendering: current backgrounds are opaque neutral surfaces, with no source tint, blur, liquid-glass renderer or optical-equivalence claim. |
| [src/daos-design-components.js](https://github.com/derrickyau9/DaOS-iHOMI/blob/10d233e316e1c258e8aa365f6a6de4d82d10c7f5/src/daos-design-components.js#L196) | Searchable grouped commands, available-target filtering, arrow selection, Enter execution, Escape close and focus restoration; Ctrl/Cmd+K opens the menu. | Bounded known app/menu routes and explicit close/focus behavior. Ctrl+K search remains in the development console; camera MENU/Q input cannot become shutter input. |
| [public/taskbar-swipe.js](https://github.com/derrickyau9/DaOS-iHOMI/blob/10d233e316e1c258e8aa365f6a6de4d82d10c7f5/public/taskbar-swipe.js) | A hide/reveal handle persists visibility, moves focus out of hidden navigation, and avoids converting app-list scrolling into a hide gesture. | Reference principles for independently authored native R14 swipe/handle/focus behavior. R14 visibility is session-local and starts open, not the reference's persisted setting; raw JS is not imported. |

The pinned Retail reference's final accent is **red**, not the initial Carbon
blue fallback. At
line1936 the interactive token becomes the brand-red variable; the late light
brand-red override is `#c52b25`, with `#fff0ee` selected surfaces. Night mode's
more-specific interactive override remains `#ff6b66`, while the late night
brand-red value is `#ff8c86`. These original roles remain distinct under the
token file's `referencePalette.light` and `referencePalette.dark`, bound to
`referencePalette.sourceRevision`. They are not the current active camera
colors. Earlier R11/R12 evidence retains its original source-informed palette.

The reference light palette is based on `#f3f5f8`, `#ffffff`, `#20242b`, `#485365` and
`#dce1e8`; night mode uses `#151a22`, `#202733`, `#f3f5fa`, `#ced6e3` and
`#445064`. These are selected source token values, not a browser-computed
screenshot or proof of every source application's final visual cascade.

## User-requested neutral camera palette

R13 introduced active `light` and `dark` roles separate from `referencePalette`;
R14 retains that neutral policy for the Taskbar and other UI surfaces.
`cameraAppearance` explicitly records `policy: achromatic-opaque-ui`,
`userRequested: true`, `opaqueSurfaces: true` and `imagePixelsUnchanged: true`.
Dark backgrounds use `#000000`, `#111111`, `#222222` and `#333333`; light
backgrounds use white and neutral grays. The selected accent is `#666666`,
with white selected labels. UI panels no longer tint or translucently reveal
the photographic image behind them. Preview/playback pixels remain in their
source color; RAW/DNG and display-image generation are not converted to gray.

The UI reference, menu behavior, system-font approach and independently drawn
icons remain source-traceable. The active monochrome appearance is the user's
requested camera adaptation; it is not represented as Retail's computed theme,
a new upstream license grant, or a change to camera capture semantics.

## Native camera boundary

The Qt shell adapts design and navigation conventions to the existing camera
controller. The API and native authority remain responsible for control,
accepted settings, capture IDs and committed files. Navigation and theme
preferences must not bypass camera ownership, acknowledge settings before
authority confirmation, resubmit a capture during app switching or modify
RAW samples. Requested and actual exposure remain separate fields.

Retail places a mobile dock below its861px desktop breakpoint. R11 initially
adapted the desktop dock for an800x600 body screen; that desktop-style product
layout was rejected and is retained only as a development console. R12 has no
permanent shooting navigation rail; R13 retained that flow. R14 follows the
user's new direction for a default-open 72px icon Taskbar, hidden with a left
swipe and revealed by a right swipe from the leftmost 24px. The 144px settings
category column still exists inside MENU, with Tools entries as an additional
route. These dimensions and route choices are camera-specific adaptations, not
a literal Retail layout port. The token file
still records72px reserved navigation and the56px actual desktop glass-rail
width separately for provenance and the retained console.

R14 uses one 220ms `Easing.InOutCubic` progress value to drive Taskbar motion,
content inset and equal-axis preview scaling. `Image.PreserveAspectFit` keeps
the complete source frame visible; HUD text and touch controls are not scaled.
Horizontal gestures require 32px travel and 1.5 times vertical dominance; vertical
and out-and-back drags do not activate an icon. Arrow handles provide a tap
alternative, and in-flight reversal is allowed. Icons have accessible names
and hover tooltips without permanent labels. The UI holds capture during a
Taskbar gesture/animation and routes focused Space/Enter only to navigation.
No camera configuration, request identity or image pixels change through this
transition. Sampled Qt frames verify geometry without claiming a physical
display frame rate.

Settings/app categories should use actual implemented operations: capture,
retained-photo review/export, saved camera workflows, diagnostics and system
appearance. Features requiring a real sensor, lens, MCU or updater remain
unavailable and must say why. A clickable launcher tile is not evidence that
an application backend exists. New settings must distinguish local appearance
preferences from device configuration confirmed by the camera authority.

The inspected Retail module imports Carbon token/icon packages, Geist fonts,
Material Web and shared liquid-glass resources. This adaptation imports none
of those modules, fonts, icon paths, artwork or optical renderer. Installed
platform fonts and independently authored Qt controls/icons avoid representing
third-party asset reuse as a new project license grant. System-font fallback
also means exact Geist typography is not claimed.

## Source and rights provenance

The pinned [README license section](https://github.com/derrickyau9/DaOS-iHOMI/blob/10d233e316e1c258e8aa365f6a6de4d82d10c7f5/README.md#license-and-ownership)
states that the repository has mixed ownership and no blanket open-source
license. The [ownership notice](https://github.com/derrickyau9/DaOS-iHOMI/blob/10d233e316e1c258e8aa365f6a6de4d82d10c7f5/config/legal/ownership.html)
attributes original shared DaOS Core materials to Derrick Yao and separately
scopes IHOMI business implementations to International House of Music Inc.
It explicitly says the notice does not itself supply an open-source license.

This work follows the user's requested adaptation of reusable UI appearance
and interaction conventions for their camera project. It does not import
IHOMI retail workflows, orders, inventory, customer records, credentials,
private services or business-specific modules. No public redistribution,
blanket license clearance, automatic ownership verification or license change
is asserted. Third-party components retain their own terms. This updates the
earlier prototype's relationship to DaOS only to the extent of the explicit,
source-traceable theme/interaction adaptation; it does not establish reuse of
the original TypeScript engine, control implementation or complete App Shell.

The local Hospitality repository and older IHOMI operations folders were not
substituted for the exact Retail reference. Their presence or similar styling
is not evidence of this pinned Retail UI integration.

## Verification boundary

Token-file parsing, source-byte hashes and color/dimension values are checked
locally. Actual app interactions, settings confirmation, retained capture,
camera layout, focus/keyboard behavior and screenshot review are separate
UI/controller verification. See the [R14 report](../testing/round14-taskbar-ui-verification.md)
for current Taskbar results, the preserved
[R13 report](../testing/round13-neutral-camera-ui-verification.md) for neutral appearance,
[R12 report](../testing/round12-camera-first-verification.md) for camera-first
verification and the [R11 record](../testing/round11-camera-apps-verification.md)
for the preserved earlier workspace. Earlier image003 ARM64 evidence applies
to its frozen body UI; it must not be relabeled as execution of R14. New host
or reference-guest renders identify their runtime/platform. A new appliance
image must be built and booted before claiming it contains the current UI.
