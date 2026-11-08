# Relationship to the existing DaOS architecture

R14 update: the working tree retains a source-traceable native Qt adaptation
of **DaOS Retail's menu/design conventions**, with an explicit user override
of color. Current UI surfaces are opaque black, white or neutral gray; preview
and photograph pixels retain their source color. The original Retail palette
is retained as reference data, not represented as the active monochrome theme.
`Main.qml` opens the viewfinder with a default-open, icon-only left Taskbar,
Q controls, grouped MENU and single-image playback. Seven icon routes provide
direct app access; the settings-category rail and MENU > Tools remain available.
`CameraTaskbar.qml` independently implements left-swipe hide, left-edge
right-swipe reveal and accessible icon navigation. A shared 220ms animation
matches the Taskbar inset with an aspect-preserving preview scale; controls
retain their size. Taskbar gestures/animation hold the shutter, and keyboard
activation of an icon cannot become a capture. No capture contract or pixel
processing is changed. The R11 rail/launcher/command-search workspace survives separately
as `Console.qml` for explicit development-console use. See the
[adapter/provenance record](retail-camera-ui-adapter.md),
[camera guide](../product/camera-apps.md) and
[R14 verification](../testing/round14-taskbar-ui-verification.md).

Selected tokens from `derrickyau9/DaOS-iHOMI` revision
`10d233e316e1c258e8aa365f6a6de4d82d10c7f5` are recorded in
`ui/on-device/retail-theme.json`, where `referencePalette` preserves the pinned
hues and active `light`/`dark` roles follow the user-requested
`cameraAppearance` policy. Raw Retail CSS/JS modules, business modules,
third-party fonts/icons/glass code and the original TypeScript runtime were not
imported. This is source-informed appearance/interaction adaptation; literal
reuse of the original DaOS App Shell/runtime remains incomplete. New UI source
and host/reference-guest results do not change the frozen R10 ARM64 BSD image.
The assessment below records the earlier R9/R10 baseline with that date boundary.
The [R13 neutral-UI record](../testing/round13-neutral-camera-ui-verification.md),
[R12 camera-first record](../testing/round12-camera-first-verification.md)
and their source ZIPs remain unchanged. R14 extends native UI navigation;
it does not port the original TypeScript framework, change the capture
pipeline or create a new BSD appliance image.

Assessment: 2026-11-05, round 9. This is an independently implemented camera-domain prototype informed by DaOS patterns. It is **not** a completed extraction or integration of the existing DaOS framework. The directory name `daos/`, product labels and similar control concepts do not establish framework reuse.

The [provenance ledger](../design-provenance.md) records the read-only inspection of `daos-capability-runtime` revision `f2975e78071a51cc85146f58e0d628a1dc68f5a0`. The [rights inventory](../../licenses/INVENTORY.json) explicitly records `daos_source_copied: false`. No source files from that checkout were imported. The inspected checkout contained local modifications, and its recorded export/root had no license file. Applicable ownership and grants were not independently established. This explains the independent implementation; it does not imply that the user's request grants rights to copy third-party files or settles future reuse permissions.

| Requested DaOS element or pattern | Actual delivered implementation | Relationship and remaining gap |
| --- | --- | --- |
| Existing React App Shell, navigation, panels, notification/status components | [Console HTML](../../daos/console/index.html), [plain JavaScript controller](../../daos/console/app.js), CSS and small local modules | Independently written browser interface. No React/Vite App Shell, upstream component or upstream theme import. It does not provide the requested literal App Shell extraction. |
| Existing `runtime/schema.ts` capability schema | New [camera v1 contracts](../../daos/camera-contracts/v1/README.md) and [bound-record v2 contracts](../../daos/camera-contracts/v2/README.md) | New camera-specific JSON Schemas and APIs. The old click/fill/read and business-domain schema is neither reused nor claimed compatible. A schema filename under `daos/` is not evidence of upstream runtime integration. |
| Existing `runtime/engine.ts` and `WebSurface` execution | Independent Python [BracketRunner](../../daos/capability_runtime/bracket.py), [WorkflowManager](../../daos/capability_runtime/manager.py) and [portable recipe parser](../../daos/capability_runtime/recipes.py) | Reimplements saved-plan execution, checkpoints and evidence for four bounded camera workflow kinds. It imports neither the original engine nor `WebSurface`; it is not the general DaOS web engine, a plugin host or a general workflow graph executor. |
| Existing `runtime/control.ts` token/epoch gate | C++ [camera authority](../../camera/native/src/daemon.cpp), Python [reference authority](../../camera/camerad/service.py), and a per-journal OS execution lock in the Python workflow runner | Reimplements ownership concepts for one device/journal. Camera leases and epochs protect commands; local OS locks fence concurrent workflow execution. No original TypeScript control source is used. This is local filesystem coordination, not a distributed DaOS lease service. |
| Device capability calls instead of webpage actions | [NativeClient](../../camera/camerad/native_client.py), [HttpCameraClient](../../camera/camerad/http_client.py), [local server](../../camera/camerad/server.py) | Camera clients call explicit capabilities, state, configure, capture/query and retained-media operations. Native RAW generation/writing stays in C++; JSON transports metadata. There is no adapter connecting an existing DaOS runtime instance to this camera contract. |
| Shared body/console device state and execution evidence | Native capture journal; separate workflow journal; [Qt controller](../../ui/on-device/controller.py); console status and request queries | Reimplements durable request identity and evidence. HTTP attach allows Qt, console and workflows to share one existing authority. These are separate implemented clients, not imported DaOS state/UI components. No generic checkpoint migration or upstream state-store compatibility is claimed. |
| On-device application | [Python launcher](../../ui/on-device/main.py), [QML interface](../../ui/on-device/Main.qml) and controller | Independent PyQt5/QtQuick application. It shares the camera API/design concepts with the console; it shares no React component code. Native/basic photography runs without the Python workflow package or a model. |
| Calibration and diagnostic capability domain | [Calibration artifacts/bindings](../../camera/calibration/README.md), authority bind/clear operations, [offline diagnostics](../testing/diagnostics.md) | Independent bounded tools and metadata-only synthetic calibration selection. They are not an upstream DaOS calibration/plugin subsystem, measured calibration or applied RAW correction. Complete console calibration/diagnostic management remains absent. |
| Optional LLM recipe generation | Saved recipes execute offline through the independent runner | No LLM planning/confirmation assistant or inherited DaOS discovery loop is implemented. Models are not part of capture, native pixel processing or saved-recipe execution. |
| Existing hospitality/retail application modules | None imported or required | No hospitality/retail runtime or business data dependency. Their absence meets the separation goal, but does not complete extraction of reusable framework components. |

The prototype therefore demonstrates several requested architectural principles: a separate camera domain, capability calls, device-scoped ownership, durable capture IDs, explicit uncertain outcomes, offline recipes and execution evidence. The literal reuse work remains incomplete: identify a permitted upstream file set and exact revision, extract or adapt the shell/runtime under those grants, define an upstream-to-camera domain adapter, and test actual integration and compatibility. That work is feasible software engineering once its source and rights boundary is established; it must not be represented as already done because the independent prototype works.

The [R8 requirements matrix](../testing/requirements-status-r8.md) retains this as a partial result under clauses 1 and 7. The [R9 delta](../testing/requirements-status-r9.md) records settings, calibration-binding and virtual update-model changes; none changes the no-import provenance finding. Physical sensor, FPGA receiver, MCU, mount, display and measured-calibration gates remain separate.
