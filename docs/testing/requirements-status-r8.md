# FF-1 requirements status — round 8

Assessment date: 2026-11-05. Source: the complete [original user specification](../product/original-user-specification.md), preserved from user-authored message `Sentinel_841a040dcd248191b0b8f004b0b79d54`. Clause numbers below refer to that text, including proposed implementation choices and planning assumptions. This is a requirements audit, not a claim that the physical FF-1 is finished.

The R7 source ZIP and its evidence remain historical, unchanged artifacts. The [R7 verification report][r7] establishes a generic ARM64 Linux/Qt **software-camera** target. The [R8 verification record][r8] adds a fresh target with independent CLI and saved-workflow execution across two persistent-data boots, plus the separately recorded host fixes/tests below. These results do not retroactively change the R7 ZIP or its test results. This audit reads code and existing reports. It does not count an unrun test, a directory, a prototype render, a proposed license or a checklist as completed implementation.

States: **S** = implemented and exercised in the stated software/synthetic scope; **P** = partial or a narrower substitute; **N** = missing software/integration that can be developed without pretending to have hardware; **V** = vendor, supply, implementation-data or publication-rights dependency; **H** = physical implementation/measurement not performed. Combinations distinguish a useful software result from the remaining physical requirement. An optional/deferred feature is not promoted to a V1 release gate.

## 1. DaOS starting point and independent camera domain

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 1.1, 7.1 | Extract reusable DaOS patterns, record exact source/version/rights, create an independent camera project. | P | [Design provenance][provenance] records the inspected Runtime commit and explains why no DaOS source was copied. The delivered console/runtime are independent implementations. Literal extraction of React/TypeScript/App Shell components was not performed, and upstream/rightsholder grants remain unresolved. |
| 1.1 | App shell, navigation, notifications, capture/device/calibration/diagnostic views. | P | [Console][console] supplies capture, preview, retained images, workflows, capabilities and status/messages. It is plain browser JavaScript, not the proposed reused React/Vite shell. Calibration and diagnostics are standalone tools/APIs rather than complete console views; no plugin/device manager is implemented. |
| 1.1 | Native camera capability schema and device-state checkpoints instead of DOM actions. | S/P | [Contract v1][contract] and [workflow runner][workflow] use camera methods, durable steps and capture status. They are intentionally not compatible with webpage click/fill/read recipes. Actual sensor parameter-latch/readback confirmation and detailed hardware progress events do not exist. |
| 1.1 | Device-scoped ownership, epochs and execution evidence including calibration identity. | S/H | [Native authority][daemon] owns admission, leases, epochs and the capture journal; `test_real_process_exclusivity_and_epoch_fencing` and `test_capture_identity_metadata_and_no_duplicate_execution` in [native daemon tests][native-tests]. Sensor/calibration IDs and actual parameters are explicitly synthetic, not physical measurements. |
| 1.1 | Optional model-assisted recipe generation stays outside capture. | S/N, optional | Saved [portable recipes][recipes] execute without a model. No LLM generation/confirmation assistant is implemented. No model call is needed by shutter, readout, DNG writing or offline replay. |
| 1.2 | Hospitality/retail business code and services are not firmware dependencies. | S | [Provenance][provenance], [inventory][inventory] and independent camera/Qt/CLI entry points establish the separation. No hospitality/retail source is included. This does not grant permission to reuse those repositories later. |

## 2. V1 product boundary

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 2.1 | Distinguish HassleFree, FF-1, DaOS Camera, HUMI and HF-Sim. | S, definition | [PRD][prd] and component READMEs use these scopes. Naming a subsystem is not evidence that its physical implementation exists. |
| 2.2 | 35 mm full-frame, about 24 MP, IMX410 candidate subject to supply/publication gates. | S/V/H | `sim-full` produces 6064 x 4040 synthetic pixels; [native full-frame tests][native-worker-tests] include `test_full_frame_native_output`. No sensor was procured or acquired. The [supplier register][supplier] is unanswered; a correctly sized file is not a full-frame sensor test. |
| 2.2 | Native Bayer DNG, 14-bit sampling, manual exposure and single still. | S/H | Native [C++ core][core]/[writer][native-dng], reference writer and actual native/Qt tests implement synthetic single captures and manual exposure/gain. Pixels are 14-bit values in 16-bit containers. Physical sampling precision, shutter and exposure response remain unverified. |
| 2.2 | Manual focus with usable lenses. | H/N | No working mount/lens/sensor exists; no focus magnification/peaking or focus interface is implemented. The [HUMI plan][humi] requires manually operable test lenses and does not claim electronic lens support. |
| 2.2 | Approximately 1 fps with disclosed measured conditions. | N/H | No camera throughput acceptance report or sustained 1 fps measurement exists. [Bandwidth estimates][bandwidth] are arithmetic; build/test durations and one successful synthetic export are not camera frame-rate measurements. Software benchmarks remain feasible. |
| 2.2 | Low-resolution preview first, then 720p/30 fps target. | P/H | [PreviewStream][preview] generates a separate bounded 320 x 240 synthetic scene; Qt caps generation at three fps and the console polls at at most five Hz. It is not sensor live view or a 720p/30 implementation. |
| 2.2 | Replace complete mount assemblies; screen, two-stage shutter, dials, back/custom controls. | P/H | Qt provides exposure/gain/mode/shutter/query/review controls. [SupervisorInputs][supervisor] conditions independent digital half/full observations in host tests; it has no GPIO/capture adapter. Imported E/EF annuli are not complete functioning mount assemblies. |
| 2.2 | Separate system storage and removable photo media; interchangeable battery and USB-C external power. | P/H | R7 demonstrates a read-only virtual system disk and separate writable ext4 data disk. Removable-card provisioning/eject/export, battery, charger and USB-C power circuitry are not implemented or measured; see [image scope][image] and [hardware review][hardware]. |
| 2.2 | Bracket/interval/calibration sequences, local update/recovery and self-built firmware. | S/P/H | Bracket/interval/synthetic dark-flat workflows and native source builds run offline. The [A/B host model][recovery] is not an installed target update/recovery system. Upstream Debian/Python/Qt binaries are authenticated/extracted, not rebuilt from source. |
| 2.2 | IBIS, eye AF, high-speed burst, 4K, EVF are not V1 prerequisites. | Deferred | These are not implemented and are not treated as blockers to the specified minimal V1. No implementation or compatibility claim is inferred from future extension language. |
| 2.2 | Without a computer: compose, expose, trigger, save a parseable full-frame DNG, repeat with another mount. | P/H | Actual ARM Qt capture/review/export works inside QEMU, with independent basic photography software. There is no physical body, sensor live view or validated second mount; physical G4/G5 have not passed. |

## 3. Independent design principles and provenance

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 3 | Own measured color management; do not transplant HNCS/OEM data. | P/H | [Calibration store][calibration] explicitly supports synthetic statistics/uncalibrated markers only. No OEM firmware, UI or extracted color resources are used. Own measured color matrices, display/output characterization and tuning remain absent. |
| 3 | Prioritize acquisition/control over UI/background work. | P/N | Native capture, UI camera worker, preview worker and single-pending media worker are separated; [UI tests][ui-test] include `test_retained_page_replaces_model_and_slow_export_does_not_starve_lease`. There is no measured Linux real-time scheduling/priority policy or worst-case hardware/storage latency budget. |
| 3 | Preview may discard old frames; accepted stills may not silently disappear. | S/H | [PreviewStream][preview] caches only current generated output and invalidates transitions. Native admission/journal failures are explicit; `test_no_completion_before_file_commit` in [service tests][service-tests] and native crash/commit tests establish software behavior, not physical media guarantees. |
| 3 | Plan thermal paths early; keep looks separate from RAW. | P/H/N | Mechanical v0.3 is a conditional thermal-routing concept, documented in [hardware review][hardware]. RAW does not bake a user LUT. A configurable look/LUT/JPEG pipeline and measured thermal design are absent. |
| 3 | Record problem, public principle, interface, own implementation and validation. | P | [Design provenance][provenance] and [native-authority ADR][adr] provide traceable decisions and boundaries. Not every later runtime/UI/RTL change has a complete per-design five-part ledger entry; later test reports provide separate evidence. |
| 3 | OEM research is separate from product source and startup dependencies. | S | [Inventory][inventory] and source handoff exclusions document independent source and retained third-party notices. No proprietary camera executable or extracted calibration file is required to start the software camera. |

## 4. Architecture and platform

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 4.1 | Qt body UI and console/workflows share one camerad API/authority. | S/P | [Qt native and HTTP modes][native-ui] and [contract][contract] use one authority for a selected journal/device session. `test_http_attach_native_capture_releases_lease_and_keeps_server_alive` and HTTP handoff tests exercise sharing. No physical sensor identity/driver is bound yet. |
| 4.1 | RAW pixels bypass React/Node/HTTP JSON/LLM business objects. | S/H | Native authority generates/writes pixels in C++; stdio/HTTP JSON carries bounded metadata. Retained files/PNGs use binary transfer. The reference simulator still creates pixels in Python and is explicitly a different backend, not production native acquisition. |
| 4.1 | Driver/libcamera/V4L2 -> FPGA receive/DMA -> RAW pool -> preview/still; independent MCU. | P/V/H | Native software boundaries and a logical [decoded-pixel RTL][rtl] exist. Sensor control, receiver PHY/protocol, real DMA, V4L2/libcamera pipeline handler and MCU power/reset path do not. Installing those frameworks would not implement the missing hardware adapter. |
| 4.2 | ARM+FPGA prototype, later small SoM/custom carrier; validated high-speed board interfaces. | P/V/H | R7 runs actual ARM64 binaries/Python/Qt on generic QEMU `virt`, not Zynq. Imported ZCU102 pin references were reviewed; [hardware review][hardware] leaves sensor lane/electrical/clock mapping and SoM/carrier selection unresolved. |
| 4.2 | Deterministic trigger/event timing belongs to sensor/FPGA/real-time logic. | N/V/H | Synthetic software timestamps exist; there is no precise trigger implementation or measured synchronization. Browser/UI timers are not used as a claimed physical exposure clock. |

## 5. Sensor, receiver and repairable hardware

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 5.1 supply | Samples, MOQ, price, lead time and longevity confirmed in writing. | V | All are pending in the [supplier register][supplier]. Public product listings and the original budget are not quotations or purchases. |
| 5.1 electrical | Full pinout, rails, sequencing, reset, clocks, layout guidance. | V/H | Register and first-light checklist exist; no authorized complete implementation set or measured sensor board is recorded. Values must not be guessed from a flyer. |
| 5.1 control | Initialization, registers and effective-frame exposure/gain rules. | V | No sensor initialization table or actual control driver exists. Synthetic requested/actual equality is not sensor register-readback evidence. |
| 5.1 data | SLVS-EC revision/rate/lanes, RAW14 packing/alignment/errors. | V/H | Candidate interface study and host pin references are not a receiver implementation. Required mode-specific protocol material and vectors remain pending. |
| 5.1 image | CFA, active/optical-black regions, defects and calibration requirements. | V/H | Simulator RGGB/full-raster assumptions are explicit. Actual sensor-area/CFA/black/defect interpretation is not established by synthetic DNG tags. |
| 5.1 optics | Package/image-plane datum, cover/filter stack and mechanical limits. | V/H | [Hardware review][hardware] identifies an undersized 40 x 28 mm placeholder and an unresolved real package/optical stack. No fit/datum release is approved. |
| 5.1 development | Working evaluation board/reference capture configuration. | V/H | No actual evaluation capture or operating configuration is recorded. Generic virtual Linux is not a sensor evaluation board. |
| 5.1 publication | Permission to publish the driver, initialization, board and sufficient protocol material. | V | Unanswered; [license inventory][inventory] does not establish vendor implementation rights. Schematic freeze/G2 cannot pass on document presence alone. |
| 5.2 | Separate temporary bring-up and openly deliverable receiver/initializer paths. | P/V | The distinction and common pixel-stream boundary are documented in [FPGA guide][fpga] and supplier register. Neither a working closed bring-up chain nor a publishable sensor receiver/initializer is delivered. Free/encrypted IP is not claimed to be source-open or RAW14-compatible. |
| 5.3 | Lane recovery/alignment, packet parsing/checks, Bayer unpack, frame metadata, async FIFO/DMA. | P/V/H | [RTL][rtl] starts with already-decoded 16-bit containers in one clock domain. It implements bounded FIFO, frame IDs and logical line/frame/range/error checks; not lanes, packet CRC, RAW14 serial unpack, clock crossings, timestamps/actual sensor settings or DMA. |
| 5.3 | Count loss/packet/line/frame/FIFO/DMA faults; never save bad frames as good. | S/P | Logical sticky errors, completion ownership and checksums run in six real Icarus configurations. [RTL-to-DNG tests][fpga-dng] reject corrupt completion. Physical lock/packet/DMA error counters and association remain unimplemented. |
| 5.4 | Repairable sensor module with power/clock/temperature/ID/high-speed interface; no hotplug. | P/V/H | Imported sensor requirements are a study, not an operating sensor PCB. Temperature/serial identity hardware and service/recalibration procedure are not validated. No hotplug support is claimed. |
| 5.4 | Authorized rail sequencing; impedance/reference plane/return path/no arbitrary ground splits; test points. | V/H | These are written prerequisites in [supplier][supplier]/[hardware][hardware], not measured layouts or released electrical designs. The low-speed control PCB does not supply them. |
| 5.4 | Lens load bypasses sensor PCB. | P/H | Concept carrier/load-frame separation is visible in editable CAD. Stiffness, fasteners, tolerance stack and actual lens loading have not been measured or approved. |

## 6. Bandwidth, buffers and honest preview behavior

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 6 arithmetic | 6064 x 4040 = 24,498,560 samples; RAW14 42,872,480 B; 16-bit container 48,997,120 B; 8/16 frames about 392/784 MB; 1/5/19.2 fps about 49/245/941 MB/s in 16-bit containers. | S, arithmetic | [bandwidth.py][bandwidth] and [its tests][bandwidth-test] compute payload/container/buffer sizes, plus a limited single-burst backlog model. 1/5/19.2 fps payload estimates are not measured interface, DDR or storage performance. |
| 6 capacity | 4 GB target, reserved DMA memory and at least eight RAW buffers. | P/N/H | [core.hpp][core] implements bounded vector-buffer reuse (defaults: 64 MiB retained, two buffers) and one still admission slot. That is not eight DMA buffers, reserved physical memory, shared-buffer ownership or a 4 GB camera configuration. |
| 6 burst | Size for instantaneous mode readout, overhead and worst downstream stalls. | P/V/H | The calculator separates burst and drain rate. There is no measured sensor burst, bus/DDR arbitration, protocol overhead or worst-case card-stall acceptance; actual receiver parameters remain vendor-dependent. |
| 6 preview | Latest preview may drop stale data; accepted still is not dropped; show real mode interruption. | S/H | [Preview tests][preview-tests] cover rate/cache/settings/mode transitions; Qt late-frame and unknown/disconnect guards prevent stale output from reappearing. Independent synthetic preview is labeled as such; actual mode switching/blackout is not measured. |
| 6 responsiveness | Slow storage must not freeze operating controls. | P/N/H | Qt's separately bounded export worker preserves a short lease in a gated test. This does not test a real slow card, blocked sensor driver or arbitrary fsync stall. Capture/write-stage progress, I/O watchdogs and realistic slow-storage exercises remain feasible software work. |

## 7. Domain contract, native authority and uncertain outcomes

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 7.1 | Independent project and file-level source/license permissions. | P | No old DaOS/business source was copied; [provenance][provenance] records the inspected revision. Proposed project licenses are not grants, and literal reuse remains subject to rights clearance. |
| 7.2 camera | Capabilities/configure/capture/query/start-stop preview in a new versioned schema. | S/P | [v1 schemas/API][contract] implement those simulator operations. Native stdio authority has no sensor preview source; native UI/HTTP adapters explicitly provide the independent synthetic preview. Schema compatibility with old web recipes is not claimed. |
| 7.2 lens/workflow/calibration | Lens capabilities/aperture/focus; start/pause/cancel; dark/flat capture capabilities. | S/N/H | Managed workflow start/pause/resume/cancel and synthetic dark/flat plans exist. A lens API/driver and dedicated `calibration.capture_dark/flat` capability integration do not; a dark/flat synthetic sequence does not establish physical calibration. |
| 7.2 request | ID, epoch, mode, requested exposure/gain, format and deadline. | S/P | Capture schema and native admission validate identity/immutable settings/epoch and optional `deadline_unix_ms`. Deadline is not mandatory or automatically derived per stage; Qt shutter does not set one. Units/naming are the new contract, not a literal old-schema migration. |
| 7.2 completion | Capture/frame IDs, actual exposure/gain, timestamp, sensor/calibration ID, path and completion status. | S/H | Complete records contain separate requested/actual fields and namespaced DNG evidence; incomplete/unknown records do not invent a measured result. [Native worker tests][native-worker-tests] compare exact synthetic values. Actual equals requested because the synthetic backend generated it, not because a real sensor was read back. |
| 7.2 time | Trace the timestamp source and parameter-effective frame. | P/N/H | Native metadata explicitly describes a host software timestamp; daemon completion time is not an exposure-trigger measurement. No sensor latch/readout timestamp provenance or cross-device timing association is implemented. |
| 7.3 | C++ camerad is sole register/control authority; V4L2/libcamera adaptation. | S/P/H | Native authority owns its journal, control and commit. Explicit Python reference/mixed-worker paths remain for simulation, with foreign journals rejected. Driver/register ownership is not implemented because there is no physical backend. |
| 7.4 | Server-side token/epoch, bounded waits, cancellation, process supervision and independent recovery. | S/P/H | Native/process/client/HTTP tests cover lease expiry, stale epochs, active-slot exclusion, cancellation, deadlines, bounded framing and kill/restart. UI media/action queues are bounded. Independent hardware reset, a plugin supervisor/SDK and cancellation of an arbitrarily blocked device/fsync are not proven. |
| 7.5 | Persist ID before triggering; query the same ID after uncertainty; never blind retry or claim exactly once. | S | [Native tests][native-tests] cover crash after admission/rename and failed commit; [UI tests][ui-test] retain ID before submit, hold unknown and query after restart. Explicit UI acknowledgment permits a separate new UUID shot while preserving the old unknown record; it never converts the old result to success. |
| 7.5 R8 workflow | Cancel/resume a recovered job without hiding an unresolved capture or resubmitting it. | S/P, R8 | [Recovery regressions][wf-recovery-tests] query/cancel the original ID and observe a terminal result; missing/unknown results keep the ID and unknown outcome. Local OS ownership prevents a second manager/direct runner from recovering a live job. Saved-plan CLI resume/pause/cancel/list and read-only terminal reopen are covered by [continuation tests][wf-cli-tests]. The [source-bound R8 report][wf-r8] records 45 tests passing on both Windows and Debian Linux, zero skips. This is local-filesystem ownership, not a distributed lease or hardware recovery; fresh R8 ARM execution additionally confirms saved-plan completion can be reopened after reboot without another capture; see the validation section below. |
| 7.5 inspection | Reconcile conservatively without destructive automatic repair. | S | [Read-only diagnostics][diagnostics] inspect SQLite snapshots, missing/unknown/orphan files and calibration integrity, with an active-service caveat. They never delete, retry or repair a request. |

## 8. HUMI mechanical and electronic boundaries

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 8.1 | Complete interchangeable module: metal load frame/datum, bayonet/lock, flange, shims, ID and optional electronics. | P/H | Editable concept CAD and a service/load-path study exist. [Hardware review][hardware] confirms E/EF parts are annuli without functional bayonet, lock, contacts or verified fit. There is no manufactured whole mount. |
| 8.1 | First E/EF mechanical modules; original nominal references E 18 mm / EF 44 mm; Z 16 mm / 55 mm packaging exploration only. | P/H | [HUMI plan][humi] treats E/EF as goals and Z as packaging exploration. Reference planes are not tested support for any lens or camera mount. |
| 8.2 | Do not freeze a 12 mm common datum before full stack/interference/tolerance analysis. | P/V/H | Imported concept Z=12 remains an explicitly unapproved historical assumption. The integration review identifies the undersized sensor placeholder and missing package, stack, lens rear envelope/contact/lock data. No approved datum is released. |
| 8.3 | Metal datum/pins/shims; micron adjustment; reassembly, center/corner/tilt and load measurements. | P/H | [Verification plan][humi-plan] specifies records, instruments, shim/version history and test method. No fabricated parts, quantified uncertainty, repeatability, corner focus or lens-load results exist. Unverified numeric tolerances are not promises. |
| 8.3 | Module ID/version/calibration/service history persists. | N/H | The generic calibration store is sensor-bound, not a HUMI/module service registry. No implemented module identification, mount calibration binding or reassembly record database is demonstrated. |
| 8.4 | Initially use lenses with manual focus/aperture; publish six separate per-model/firmware capabilities. | P/H/N | The plan separates mechanical fit, aperture, identification/EXIF, focus drive, AF and stabilization. There are no named tested lenses or populated compatibility results, and no body-side module MCU/electronic lens interface. |

## 9. RAW, DNG, color and calibration

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 9.1 | Preserve Bayer RAW; separate corrected preview/style/JPEG path. | S/P | Capture writers preserve generated sample values and do not bake a user LUT. Preview is independently generated; retained PNGs derive from saved Bayer data. There is no complete black/defect/flat/demosaic/WB/matrix/tone/look/JPEG camera pipeline. |
| 9.1 | Own measured color and style data, not imported manufacturer profiles. | N/H | Current color matrices/WB assumptions are explicitly synthetic. Measurement capture, profile fitting, display/output characterization and versioned styles are not implemented; measured results also need hardware. |
| 9.2 | Correct CFA/active/crop/bit depth/black/white/WB/color/Exif metadata. | S/H | [Native writer][native-dng] and [DNG tests][dng-tests]/[native worker tests][native-worker-tests] check synthetic full-raster crop, RGGB, 14-in-16, black 64/white 16383, color/WB and actual Exif exposure. Real optical-black/active-region interpretation and measured color remain unvalidated. |
| 9.2 | Two independent decoders, exact Bayer equality and explicit crop/color checks. | S/H | [R7 final target report][r7] records tifffile and LibRaw/rawpy each matching all 12,288 small-frame samples; [G1][g1] and native full-frame tests record larger synthetic cases. This is stronger than visually plausible output, but not physical image quality or broad editor compatibility. |
| 9.2 R8 | Reject metadata whose CFA-plane mapping/layout or capture JSON identity is inconsistent. | S, R8 | [Inspector tests][inspect-tests] add `test_cfa_plane_mapping_and_layout_must_describe_rectangular_rggb` and `test_overflowing_json_float_invalidates_capture_identity`; [retained tests][retained-tests] reject changed mapping/layout before display/export without recapture. The narrow inspector also rejects unsupported `LinearizationTable` transforms rather than displaying stored nonlinear samples as linear Bayer. [The native/DNG audit][native-r8] records independent TIFF/LibRaw mutation checks and focused regressions. These are R8 fixes with a fresh R8 target, not a retroactive change to R7 evidence. |
| 9.2 precision | Never describe a 16-bit container as 16-bit sensor sampling. | S | Constants, metadata, UI labels and reports explicitly identify 14-bit values/16-bit containers. There is no physical sensor sampling claim. |
| 9.3 storage | Sensor-serial binding, immutable versions/checksums, export/import, separate persistence and update survival. | S/P | [CalibrationStore][calibration] validates sensor/path identity and immutable versions; [tests][cal-test] cover restart/import/export/corruption/concurrent publication/disjoint image roots. It is standalone bookkeeping; the target does not provision a separate calibration partition or exercise reflash survival. |
| 9.3 content | Black vs mode/gain/temperature, bad/hot pixels, response/uniformity, color matrix, optical/lens shading and sensor plane. | N/H | The current schema stores bounded synthetic dark/flat statistics or an uncalibrated marker. It does not implement these measured models, defect maps, optics records or sensor-plane calibration. Richer schema/algorithms can be developed with explicit synthetic fixtures; real package values require measurements. |
| 9.3 activation | Captures reference the active sensor-bound calibration; calibration and looks have separate versions. | N | Service/pipeline integration is explicitly absent; captures remain `synthetic-uncalibrated`. No package activation/binding or separate style-version pipeline exists. An imported package does not silently change metadata or pixels. |
| 9.4 | Manual-focus assistance -> supported lens drive -> contrast AF -> continuous AF -> conditional PDAF. | N/H, staged | No focus assistance/drive/AF/PDAF implementation or data mapping is demonstrated. Later stages are deferred, not V1 prerequisites; sensor data, lens protocol and calibration must precede any PDAF claim. |

## 10. System stack, interfaces and saved recipes

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 10.1 boot/system | U-Boot recovery, fixed Linux BSP/Buildroot image, board device tree and drivers. | P/N/H | R7 provides an authenticated pinned Debian generic ARM runtime and normal ext4 development image, with real guest boots. It is not Buildroot, U-Boot, a board BSP, factory recovery, secure boot or production firmware. |
| 10.1 application | Native C++, Qt/QML, console, domain workflows and offline Python calibration tools. | S/P | Native authority and Qt run on host and virtual ARM; console/workflows/calibration code is independent and local. Literal React/TypeScript reuse and integrated calibration/device/plugin tools are absent. |
| 10.1 buffers | Validate V4L2/videobuf2/DMA-BUF layout, sharing, cache synchronization and ownership. | N/H | The native pool owns copied vectors; no V4L2/DMA-BUF path exists. No zero-copy claim is made. A real adapter and driver need the selected hardware; ownership-model/synthetic stress tests remain feasible. |
| 10.2 body UI | Photography-first large preview, clear exposure, shallow controls, physical dials. | S/P/H | Qt supplies a photographic layout, clear synthetic state, exposure/gain/mode and retained review/export. [23 UI tests][ui-test] and [ARM QML evidence][armui] exercise it. Actual sensor view/display/touch/GPU/dials/buttons remain unverified. |
| 10.2 console | Same API with link/temperature/errors/fps/workflows/calibration traces; do not claim React becomes Qt. | P | Console exposes states, request/actual metadata and workflow detail; separate Qt code consumes the API. Real link/temperature/fps telemetry, full calibration management and device/plugin views are absent. |
| 10.3 recipes | Portable save/import/export/share/inspect and offline deterministic replay. | S/P | [Recipe format][recipes], console and [recipe tests][recipe-tests] cover all four kinds, strict 64 KiB schema, duplicate/nonfinite/private-field rejection and create-only export. Recipes exclude tokens, job/request IDs and results. Sharing is a file operation, not an automatic message. The format supports four exposure-plan kinds, not arbitrary plugin/lens actions, branching or a general product-photography capability graph. |
| 10.3 bracket semantics | Acquire, preflight, apply, verify effective settings, capture/commit each step, restore settings and release. | P | Runner journals identities/checkpoints, configures, polls and restores/releases on a best-effort basis. Synthetic state is authoritative only for the simulator. The runner checks service state and requires positive actual values/path in completed records, but has no explicit free-space/media-budget preflight or sensor effective-frame readback policy. These are narrower than the original bracket semantics; R8 recovered-cancel/ownership/restore fixes are tracked separately. |
| 10.3 automation loss | Basic photography remains available without HTTP, Node, models or workflows. | S/P | Native Qt runs independently. R8 adds standalone CLI capabilities/status/list/asset/export and explicit backend-selection guards, alongside capture/query. The seven host CLI checks include `test_capture_review_and_export_survive_cli_restarts_without_workflows_or_replay`, `test_existing_export_and_missing_or_corrupt_source_are_never_replaced` and `test_invalid_backend_selection_cannot_silently_fall_back_or_create_data` in [offline CLI tests][offline-cli-tests] plus the two legacy CLI cases. The actual ARM [offline smoke][offline-smoke] passed in both R8 guest boots: camera capture/query/list/asset/export ran with all DaOS imports blocked, retained the same UUID and one journal row, and exported the same bytes after reboot. |
| 10.3 optional LLM | Generate a recipe for explicit confirmation; saved execution is offline. | S/N, optional | Offline execution exists; natural-language generation and confirmation UI do not. This optional assistant does not block independent basic capture. |

## 11. Power, thermal behavior, progress and recovery

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 11.1 | Independent MCU startup/shutdown/buttons/battery/power switches/supervision/reset. | P/N/H | [Supervisor reference][supervisor] and [19 tests][supervisor-tests] provide deterministic input conditioning only. The imported accessory PCB has no MCU. No target firmware, battery readings, output driver, power sequencer or independent reset decision exists. |
| 11.1 | Precise sensor trigger timing is independent of browser timers. | N/V/H | This separation is documented. No physical trigger timing implementation exists; host software capture timestamps are not substituted for it. |
| 11.2 | Measure power/hotspots for idle, preview, save, sustained capture, transfer and charging; derive runtime from measurements. | H | No such measurements or battery-runtime result is recorded. The original 36 Wh/85%/15 W example is arithmetic only. [Thermal concept review][hardware] explicitly leaves supplier-approved sensor contact and operating limits unresolved. |
| 11.2 | Separate sensor/SoC/storage/power thermal paths, fixed sensor support before IBIS. | P/H | v0.3 contains a conditional routing concept, not demonstrated thermal closure. No real heat flow, contact pressure, temperature rise or final enclosure qualification is measured. |
| 11.3 | Monitor progress at FPGA, DMA, camerad, storage and UI independently. | P/N/H | Native status/journal health, logical RTL counters, UI connection/capture state and bounded process failures exist. A combined progress/event protocol with frame/byte/stage advancement and layered health decisions is absent. |
| 11.3 | Stage-aware deadlines include exposure/readout/processing/write; long exposure is not a hang. | P/N/H | Native `CaptureControl` and deadline checks bound cooperative synthetic loops. There is no physical exposure wait/readout model or watchdog budget per apply/expose/read/validate/write/sync/commit stage; arbitrary blocking I/O is not made cancellable by a timeout field. |
| 11.3 | Local recovery escalates plugin -> workflow -> service -> link -> system with a finite budget. | P/N/H | Native process abort/restart, conservative unknown recovery and bounded systemd restart/trial budgets exist. There is no plugin isolation framework or implemented full recovery ladder; hardware link/system/MCU reset is unimplemented. |
| 11.4 photos | Temporary file, synchronization, exclusive final publication and durable commit/journal. | S/H | Native/reference tests cover no completion before commit, rename/journal crash windows, orphan/conflict preservation and restart unknown. Actual chosen-media power cuts, controller caches and fsync behavior remain untested; no universal no-loss claim follows. |
| 11.4 R8 boundaries | Detect redirected/replaced output directories and staged/published file changes before claiming completion. | S/H, R8 | [Native/DNG audit][native-r8] and [reference path tests][capture-path-tests] cover startup/admission directory identity, real Windows junctions, POSIX replacement races, foreign-file preservation and tampered staging. The native writer supplies an opened-file receipt; identity, byte count, whole serialized-file checksum and stable statistics are verified around sync/publication. These are boundary checks, not an atomic sandbox against a hostile process with the same filesystem permissions, cryptographic authentication or physical power-loss acceptance. |
| 11.4 update | A/B new-slot install, boot/core-device health confirmation, fallback and local recovery. | P/N/H | [RecoveryStore][recovery] persists/consumes finite host trial attempts and validates matching health evidence; [tests][recovery-tests] cover abrupt process exit/stale health/fallback. It neither installs an image nor chooses an actual bootloader slot; R7 PID 1 does not call it. Virtual A/B target integration is still feasible software work. |
| 11.4 storage | System slots, photos, configuration and calibration are independently persistent. | P/N/H | R7 has one read-only system filesystem and one writable data filesystem. `/data/config`, `/data/userdata`, caches and photo/session state are directory boundaries, not distinct photo/config/calibration partitions. No removable media lifecycle or reflash/calibration-survival integration exists. |
| 11.4 settings | Preserve meaningful device configuration separately from request recovery state. | P/N | Qt persists latest UUID/unknown acknowledgments/backup trace, not next-shot exposure/gain/mode. R7 restart correctly uses new-authority defaults while retained metadata keeps the old actual exposure. A versioned persistent settings policy is not implemented. |

## 12. Gates and release acceptance

| Gate / clause | Required result | Status against the original acceptance condition |
| --- | --- | --- |
| G0 | Independent project, PRD/provenance/licenses/ADR and no business runtime dependency. | **Partial.** Independent software, scope, provenance and ADR exist. Literal DaOS extraction was replaced by independent code; project/upstream grants are not all resolved. Proposed license text is not a grant. |
| G1 / 12.1 | Synthetic Bayer -> DNG -> independent exact readback; control console, exposure/modes/delay/corrupt/full/disconnect/cancel/restart tests. | **Demonstrated in the stated software scope.** [G1 evidence][g1], [R6][r6] and [R7][r7] record actual tests/decoders and runnable console/native paths. This does not emulate IMX410 electrical behavior or pass G2/G3. |
| G2 | Written supply/authorized documents, implementation rights and publishable receiver/initialization path. | **Open — V.** [Supplier register][supplier] is unanswered; no quotation, approved init/receiver or publishable implementation set is established. |
| G3 / 12.2 | First real full-frame RAW and repeated complete frames with no unexplained errors. | **Open — V/H.** [First-light record][firstlight] has every physical step Not run: power/clock -> control -> lock/integrity -> Bayer -> exposure -> DNG -> preview. Synthetic/native/RTL success cannot substitute. |
| G4 | Standalone physical body composition/exposure/shutter/save/review/export. | **Partial — software demonstrated, physical open.** Real ARM Qt/authority in a virtual guest completes capture/review/export and restart persistence. No physical body, sensor live view, inputs or removable-card workflow is validated. |
| G5 | Two complete mounts, real lenses, repeatability and center/corner optical results. | **Open — H.** Editable concept annuli and a plan are not E/EF bayonets or lens tests. |
| G6 | Sensor-bound calibration, image quality, fault recovery, thermal/power/endurance evidence. | **Partial software only.** Synthetic DNG/identity/recovery tests and calibration bookkeeping exist; measured calibration and physical reliability are absent. |
| G7 | Complete editable manufacturing/build package; another builder constructs and photographs. | **Open — P/H.** Clean software rebuilds, pinned runtime recipe and preserved editable prototypes exist. There is no complete validated manufacturing design/BOM/assembly release or independent manufactured-camera photograph. |

| 12.3 test | Current evidence | Remaining acceptance |
| --- | --- | --- |
| 1,000 actual RAW samples | Synthetic raw-value tests, including independent decoders and full-size generated files. | No 1,000 real-frame acquisition acceptance run; no unexplained-error accounting from a real sensor. |
| Eight-hour operation | No retained eight-hour camera run. | Software soak tests are feasible now; physical power/temperature/error endurance still requires hardware. |
| Repeated mode changes/no stale association | Preview/cache tests, native request identity/validation, UI late-frame rejection. | No repeated real sensor-mode transition/effective-frame acceptance. |
| Slow/full cards | Explicit simulated storage-full failures and gated Qt export responsiveness. | No measured slow card, real ENOSPC media matrix or uncontrolled write/fsync stall coverage. More software I/O fault/stress work remains feasible. |
| Plugin crash/hang | Native child/process timeout, cancellation, authority kill/restart and retained unknown tests. | No actual plugin subsystem or independent hardware recovery acceptance; basic path under arbitrary plugin failure is not yet a product guarantee. |
| Abnormal power at write/update stages | Process-crash commit-window tests, durable host trial-budget tests, clean guest unmount/reboot. | No power-cut matrix; clean reboot is not abrupt power loss. Virtual crash/update tests can precede physical media tests. |
| Repeated mount installation | Written measurement template and concept service study. | No fabricated mount, measured flange/corner image series or lens-load result. |
| Calibration after reflash | Standalone store restart/import/export and disjoint-root tests. | No system reflash with active sensor-bound calibration selection and verified survival. |
| Clean source rebuild / second developer | Actual clean source/native builds and fresh/offline generic ARM recipes; source manifests and notices. | Remote CI and an independent human hardware reconstruction/photograph are not demonstrated. AI subagent review or a second directory is not G7 replication. |

## 13. Repository, manufacturing deliverables, rights and CI

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 13.1 | One coherent repository with docs/domain/native/UI/driver/FPGA/firmware/hardware/mount/test/system/tool/license boundaries. | S/P | The source tree follows these concerns. Native, UI, RTL and tools contain executable implementations; hardware/driver/receiver/DMA slots and plans must not be counted as implementations. |
| 13.1 | Bind every hardware revision to FPGA/driver/image/calibration formats. | P/N/H | [Import provenance][imports] preserves five source archives and versions; R7 target inputs bind virtual image/runtime/native/source hashes. There is no complete physical board/bitstream/driver/calibration compatibility manifest or supported hardware revision set. |
| 13.2 | Editable PCB/mechanical sources, BOM, assembly, source/build environment/test methods/calibration tools. | P/H | Five immutable imports include KiCad and editable CadQuery sources, with integration review. Source/tools/tests are supplied. Sensor/carrier/power designs, exact integrated BOM/assembly/tolerance/thermal/optical instructions and measured calibration are not manufacture-ready. |
| 13.2 | Proposed CERN-OHL-S / GPL3+ / Apache2 / CC BY-SA grants, retain third-party terms, no implied DaOS grant. | P, rights | [License README][licenses] and [inventory][inventory] explicitly keep project proposals pending. SQLite/json notices, original prototype notices and tool/runtime provenance are preserved. A private source ZIP is not a public open-hardware or blanket-license release. |
| 13.2 | Accurate open boundary and reproducible necessary implementations. | P/V | Silicon internals/tools are not claimed open. Native camera code can be built from source. 170 Debian runtime binary packages are pinned/authenticated with original notices, not rebuilt upstream; sensor receiver/init/publication rights and exact redistribution/source obligations remain open. |
| 13.3 ordinary CI | TypeScript/C++/contract/DNG/fault validation. | P | [CI workflow][ci] defines Ubuntu/Windows Python/native/Qt/contract/DNG/fault/RTL and Node syntax checks. Console is JavaScript, not a TypeScript application. No retained remote GitHub Actions run is evidence here; local test reports are distinct. |
| 13.3 FPGA CI | Actual simulation, CDC and executable synthesis with tool/license versions. | P/V/H | Real Icarus simulation and tool hashes are recorded. No CDC analysis, synthesis/implementation/timing/bitstream job or licensed board toolchain acceptance exists. |
| 13.3 HIL / accessible contribution | Self-hosted board power/capture/log checks; contributors can develop without sensor hardware. | S/N/H | HF-Sim and local tests allow source-only contributions. No physical self-hosted HIL runner, power controller or board capture test is installed. ARM image/offline-replay CI and an explicit expected-skip policy also remain software work. |

## 14. People, budget and execution plan

| Clause | Requirement | State | Evidence and remaining work |
| --- | --- | --- | --- |
| 14.1 | Product/API/workflow/tools/community leadership; actual high-speed FPGA/electrical, mechanical/optics/calibration collaborators. | Planning/H | The original division of responsibilities is preserved. No named staffed hardware team, laboratory commitment, supplier engagement or fabrication capability is established by source/code review. |
| 14.2 | Provisional USD 7,600-27,700 base / about 10,000-36,000 with reserve; replace assumptions with quotations. | Planning/V | These are original budget allowances, not spend or current component prices. They exclude labor, commercial IP, major instruments and production introduction. The supplier register has no quotation; no actual budget closure is evidenced. |
| 14.2 | Software/docs before expensive acquisition; reliable RAW before final CNC. | P | Software verification and source review have progressed without purchases/fabrication. Reliable **real** RAW has not been achieved, so a complete manufacturing release would be premature. |
| 14.3 | 9-18 month resource window is an assumption, not a delivery promise; supply tracked separately. | Planning | Preserved in the original specification. No measured physical-project schedule or committed supplier lead time validates it. |
| 14.3 first five | PRD/exclusions; independent console; versioned API; HF-Sim DNG/readback; vendor/open-receiver/budget verification register. | S/P | PRD, runnable independent console, contract and synthetic readback are demonstrated. A vendor/register template exists but its substantive answers/quotes/open implementation path remain missing. The five-file checklist is not the end-to-end product acceptance condition. |

## Executed R8 validation and evidence boundaries

The final [R8 verification record][r8] and original copied logs distinguish
full-suite discovery, focused runs and actual guest execution. Overlapping
focused checks are not added to the full-suite count.

| Recorded run | Actual result and scope |
| --- | --- |
| [Windows full suite][r8-windows] | 428 run in 102.048 seconds: 405 passed and 23 explicit platform skips. This invocation remains 428, not 429. |
| [Final Windows source-bundle-only check][r8-bundle] | 16 passed, zero skips, 1.568 seconds. One delivery-only case was added after full-suite discovery, so this is a separate check. |
| [Linux critical and delivery checks][r8-linux] | 120 passed, zero skips, 23.805 seconds. |
| [Workflow recovery/continuation][wf-r8] | The same 45 tests passed on Windows and Debian Linux; source hashes and original logs are retained. |
| [Native/DNG integrity audit][native-r8] | 33 focused Python cases passed without skips; Windows CTest passed both targets and Linux reported 89 core plus 90 authority assertions. The dynamic Linux staging-interposition fixture is not claimed to execute in static ARM binaries. |
| [Fresh R8 ARM first boot][r8-first] / [resume boot][r8-resume] | Passed in 28.909 / 25.890 seconds with ordinary ARM Python 3.13.5 and Qt/native execution, independent CLI, and a saved native workflow. System filesystem unchanged; persistent data reused. |

The Qt capture request `b86572e6-c679-4079-8f3d-2d2c5c1239bb` has one completed
journal row in both boots. Independently, the basic CLI executes with a DaOS
import blocker and keeps request `abd646a3-77e6-4063-97cf-a6e557eee799`, one
completed journal row, and identical DNG/export SHA-256
`2bed91cab1738f3be5406b3990924c888accfd68803e4d1d5b72fb62f4f568ae`.
The CLI's actual simulated exposure is 11,700 microseconds. This is an explicit
software independence check, not merely an unavailable automation server.

Saved workflow `offline-5f2535c7efde4ef49b51eb0312f17236` retains its two original
DNGs, two journal rows and both hashes across the reboot. The resume phase reports
`resumed_saved_plan: true` and `completed` without taking another frame. The
read-only system-image SHA-256 remains
`675c2212ef7a893ef7525c3137848c752bf23837ccabe5e1823b438159ab5882`.
This image and these identities are R8 results; they are distinct from the R7
image and Qt request documented in the historical reports.

The verified capture-path fixes reject startup/admission redirects, directory
replacement and staged-file changes. Native receipt checks cover the actual
opened file, full serialized bytes and publication boundaries. The [focused
audit][native-r8] explicitly limits these to detection at defined boundaries:
path-based operations are not an atomic same-UID sandbox. These results do not
establish physical filesystem power-loss integrity, sensor provenance or color
calibration. All guest captures remain synthetic, Qt remains offscreen/software
rendered, and no physical camera/display/lens/DMA acceptance is inferred.

## Feasible work must remain separate from external gates

The following are meaningful software tasks, not consequences that must wait for a sensor purchase: active calibration-package binding/schema and explicit synthetic algorithm tests; stage/progress telemetry and watchdog budgets; slow-I/O/soak/stress exercises; persistent configuration/store boundaries; a virtual A/B install/boot/confirm/fallback target; input-event-to-command adapter tests; ARM/offline-target CI; richer device/diagnostic/calibration console integration; and image/format compatibility validation. The R8 CLI, workflow and DNG fixes now have their own executed host and fresh-target evidence; their bounded success does not implement these other missing subsystems.

Actual first light, sensor timing/packing/receiver acceptance, physical display/input/MCU reset, real removable media/power cuts, optics/mount manufacture, measured calibration/thermal/runtime and independent camera replication still require authorized data, selected hardware, people and measurements. Those dependencies do not justify calling all remaining software blocked, and software progress does not close those physical gates.

[prd]: ../product/ff1-prd.md
[provenance]: ../design-provenance.md
[adr]: ../adr/0001-native-simulator-authority.md
[contract]: ../../daos/camera-contracts/v1/README.md
[daemon]: ../../camera/native/src/daemon.cpp
[core]: ../../camera/native/include/hf/core.hpp
[native-dng]: ../../camera/native/src/dng.cpp
[native-tests]: ../../tests/test_native_daemon.py
[native-worker-tests]: ../../tests/test_native_worker.py
[service-tests]: ../../tests/test_camera_service.py
[native-ui]: ../../ui/on-device/README.md
[ui-test]: ../../tests/test_device_ui.py
[armui]: arm64-ui-round7.md
[r7]: round7-verification.md
[r6]: round6-verification.md
[g1]: g1-evidence.md
[console]: ../../daos/console/README.md
[workflow]: ../../daos/capability_runtime/bracket.py
[recipes]: ../../daos/capability_runtime/README.md
[recipe-tests]: ../../tests/test_workflow_recipes.py
[calibration]: ../../camera/calibration/README.md
[cal-test]: ../../tests/test_calibration.py
[preview]: ../../camera/pipeline/preview.py
[preview-tests]: ../../tests/test_preview.py
[fpga]: ../../fpga/README.md
[rtl]: ../../fpga/rtl/README.md
[fpga-dng]: ../../tests/test_fpga_dng.py
[bandwidth]: ../../hardware/tools/bandwidth.py
[bandwidth-test]: ../../hardware/tools/test_bandwidth.py
[humi]: ../../mounts/README.md
[humi-plan]: ../manufacturing/humi-verification.md
[hardware]: ../../hardware/interface-review.md
[supplier]: ../manufacturing/supplier-gate.md
[firstlight]: first-light-record.md
[supervisor]: ../../firmware/supervisor-mcu/hf_supervisor.py
[supervisor-tests]: ../../tests/test_supervisor.py
[recovery]: ../../system/linux/recovery.py
[recovery-tests]: ../../tests/test_linux_recovery.py
[image]: ../../system/arm64-image/README.md
[diagnostics]: diagnostics.md
[dng-tests]: ../../tests/test_dng.py
[inspect-tests]: ../../tests/test_dng_inspect.py
[retained-tests]: ../../tests/test_retained_captures.py
[imports]: ../../hardware/imports/provenance.json
[ci]: ../../.github/workflows/ci.yml
[licenses]: ../../licenses/README.md
[inventory]: ../../licenses/INVENTORY.json

[offline-cli-tests]: ../../tests/test_camera_offline.py
[offline-smoke]: ../../tests/ui/offline_smoke.py
[wf-recovery-tests]: ../../tests/test_workflow_recovery.py
[wf-cli-tests]: ../../tests/test_workflow_continuation_cli.py

[wf-r8]: ../../daos/capability_runtime/evidence/round8-workflow.json

[capture-path-tests]: ../../tests/test_reference_capture_paths.py

[r8]: artifacts/round8/verification.json
[r8-windows]: artifacts/round8/windows-suite.txt
[r8-bundle]: artifacts/round8/source-bundle-windows-tests.txt
[r8-linux]: artifacts/round8/linux-critical-tests.txt
[r8-first]: artifacts/round8/arm64/first-boot.json
[r8-resume]: artifacts/round8/arm64/resume-boot.json
[native-r8]: ../../camera/native/evidence/round8/REPORT.md
