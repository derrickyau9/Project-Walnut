# Design provenance and reuse ledger

Date checked: 2026-11-05. This is a record of observed sources and intended independent work, not an IP clearance opinion.

## Existing DaOS

A read-only local checkout of `daos-capability-runtime` at `f2975e78071a51cc85146f58e0d628a1dc68f5a0` was found. It had local modifications in `src/app/dashboard/DashboardPage.tsx`, `src/components/layout/FullScreenViewSheet.tsx`, `src/styles/daos-theme.css` and `src/styles/dashboard.css`; they were not changed by this project. Inspect the precise revision and rights before reuse. The project README describes web discovery and model-free replay, not a camera. `runtime/schema.ts` defines click/fill/read and business-specific fields, `runtime/engine.ts` directly constructs a `WebSurface`, and `runtime/control.ts` uses a single-process Promise gate and epoch/token mechanism. The camera domain is a new contract and requires different execution semantics.

The local `DAOS_PROVENANCE.md` describes the framework export, its reused UI and states the export had no license file. The inspected runtime root has no license file. No ownership or rights clearance has been independently verified; a public repository is not by itself a grant of rights. Do not import source or imply an upstream license until each relevant rightsholder grants an appropriate license and every imported file and revision is recorded. Third-party licenses remain applicable. No files from the checkout are represented as imported here.

Sources: [Runtime README](https://raw.githubusercontent.com/derrickyau9/daos-capability-runtime/main/README.md), [provenance](https://raw.githubusercontent.com/derrickyau9/daos-capability-runtime/main/DAOS_PROVENANCE.md), [schema](https://raw.githubusercontent.com/derrickyau9/daos-capability-runtime/main/runtime/schema.ts), [control](https://raw.githubusercontent.com/derrickyau9/daos-capability-runtime/main/runtime/control.ts), [engine](https://raw.githubusercontent.com/derrickyau9/daos-capability-runtime/main/runtime/engine.ts). Online `main` links are mutable; local commit above is the traceable reference.

## Independent design decisions

| Problem | Public principle / source | Intended project interface and validation |
| --- | --- | --- |
| Image data integrity | Separate recording and preview resource policies, derived from system requirements. | Frame identity, validity and actual parameters flow from sensor backend to writer; compare Bayer values independently and test backpressure. Not yet hardware validated. |
| Device abstraction | [libcamera architecture](https://docs.libcamera.org/master/libcamera_architecture.html) separates pipeline handlers and image-processing algorithms. | Define a device backend behind the camera authority; a real handler and drivers remain unimplemented until verified. |
| Service recovery | [RAUC](https://rauc.readthedocs.io/en/v1.15/basic.html) has explicit boot-good/boot-bad signaling. | A/B slots and camera health tests are future integration, requiring real power-loss testing. |
| Full-frame candidate | [Sony IMX410 product flyer](https://www.sony-semicon.com/files/62/pdf/p-13_IMX410CQK_Flyer.pdf) specifies 6064 × 4040 active pixels, eight SLVS-EC lanes and a 14-bit 19.2 fps mode. | Candidate only; determine complete implementation rights, voltage, protocol and purchasing details before design freeze. |
| Open hardware deliverable | [OSHWA definition](https://oshwa.org/resources/open-source-hardware-definition/) requires editable design files and permission to study, modify, distribute, make and sell. | Publish original CAD and PCB sources, BOM, build and test data with clear licenses when actually available; component internals need not be claimed as open. |

Do not incorporate manufacturer firmware, extracted color data, UI assets or proprietary binaries into an independent implementation. Add each later reference, interface, original implementation and actual verification result here before release.

## R10 FreeBSD-derived ARM64 appliance

The later user-selected BSD route builds FreeBSD source commit
`95719d8a895bf8170851e9844d9b4092b1d2b6fe` into an ARM64 world and `DaOS`
kernel configuration including upstream GENERIC. This reuses FreeBSD source
under its component terms; it is not an independently written Unix kernel or
new sensor driver. The [source lock](../system/freebsd/base/source-p6-arm64.lock.json)
and [base-build evidence](../system/freebsd/base/evidence/arm64-p6/build-state.json)
record the actual archive, commands and outputs. The Qt/Python/Xorg closure
uses declared ARM64 binary packages, with retained notices and per-file
checksums; these components were not all rebuilt from source.

Independent project code supplies camera contracts, native authority, simulator,
workflows, body UI, image assembly and bounded virtual boot/review tools.
The [R10 report](testing/round10-freebsd-arm64-verification.md) binds actual
ARM64 tests, the exact assembled image, complete800x600 native Qt display,
one synthetic DNG, full kernel restart/same-ID recovery, independent decoding
and byte-identical GUI export. Physical sensor/board/optical tests, original
DaOS framework extraction and BSD A/B updating remain unimplemented. The
[rights inventory](../licenses/INVENTORY.json) and per-package notices retain
their separate scope; product naming does not grant a blanket BSD license.

## R12 camera operating interface

The user rejected the R11 permanent application workspace as a camera-body
design. R12 independently implements a viewfinder-first Qt interface: edge
exposure information, explicit Q controls, grouped MENU, single-image playback
and optional tools inside MENU. The previous workspace remains an explicitly
selected development console. No manufacturer camera interface assets or
firmware were copied to make this change.

Selected colors and bounded navigation conventions are traceable to the
inspected DaOS Retail commit `10d233e316e1c258e8aa365f6a6de4d82d10c7f5` in the
[Retail adapter record](architecture/retail-camera-ui-adapter.md). This is not
an import of its business modules, original TypeScript runtime, fonts, icons
or glass renderer. Original vector icons, platform fonts and Qt components
provide the implementation. The [R12 verification](testing/round12-camera-first-verification.md)
separates actual platform execution, synthetic preview/capture sources,
preserved failures and hardware/image-deployment limits.

## R13 user-directed neutral surfaces

The user explicitly requires black, white or neutral gray backgrounds for all
UI elements. Active camera and console surfaces therefore use an independently
selected opaque achromatic palette, with the original Retail hues retained
only as non-rendered reference data. Selected controls use gray with white
labels. This is a recorded user override, not a claim that Retail's original
red/slate palette was monochrome. No photograph or synthetic image is converted
to grayscale for this change. See the [R13 verification](testing/round13-neutral-camera-ui-verification.md).

## R14 user-directed icon Taskbar

The user requested left icon navigation with swipe hide/reveal, animation and
proportional preview adjustment. Independent Qt code uses the existing bounded
app registry and original vector icons, with one shared animation progress for
the72px rail and preview geometry. Gesture recognition consumes navigation taps;
camera request identity and the RAW pipeline remain separate. The grayscale
palette is retained. This does not import a Retail business module or a vendor
camera interface. The [R14 verification](testing/round14-taskbar-ui-verification.md)
separates behavioral tests, original Qt animation samples and hardware limits.
