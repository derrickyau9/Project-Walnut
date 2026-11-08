# Architecture and invariants

Status: intended architecture. Only running code and recorded tests establish implementation status. The native synthetic authority and generic ARM64 Linux/Qt virtual image are exercised. No real sensor, PCB, FPGA receive path, DMA driver, mechanical system, MCU or physical board image has been verified here.

Body controls / on-device UI / remote console / workflow -> camera control service -> hardware adapter -> sensor and capture pipeline. The intended Linux production backend uses native C++, V4L2/libcamera and custom drivers; these are future integration work, not implied by the simulator.

Capture data must not traverse React, LLM calls, or JSON business objects in the production path. FPGA reception, frame checks and DMA feed a raw buffer pool; preview and still queues have different backpressure policies. Preview may discard stale frames. An accepted still may never be silently discarded or announced as saved before durable completion. Mode changes may interrupt actual preview and the UI must reflect that.

## Control and request invariants

- One camera authority arbitrates control. Ownership credentials and a monotonically changing epoch fence stale actors. Recovery of hardware must not rely on an unresponsive plugin releasing a lock. Bound all waits and define cancellation semantics.
- Persist a request identity and its immutable parameters before triggering a capture. Same ID with different content is a conflict. After uncertain transport or process failure, query its state; do not automatically trigger another exposure. If outcome cannot be established, return `unknown`, not success or an exactly-once claim.
- Capture metadata records requested and actual exposure and gain independently; actual values are only reported when obtained from authoritative frame metadata. Link frame ID, mode, timestamp source, sensor and calibration versions, output location and status. Unknown is not a measured value.
- Apply, expose, read, verify, write and commit are distinguishable phases. Stage-aware deadlines include exposure and expected readout; no new frame during a valid long exposure alone does not imply a hang.
- Capture files are staged and synchronized before commit; crash and storage semantics depend on OS and media and require repeated power-cut tests. System update health confirmation, photo storage and calibration storage are separate concerns.

## Interfaces and limitations

A transport can carry control and status, but must not be treated as a safe internet-facing service without authentication, authorization, encryption, resource limits and security review. Simulator fault controls must not be exposed on production hardware.

A real backend requires verified sensor register programming, SLVS-EC protocol and RAW14 packing, electrical design and reset sequencing, FPGA and DMA implementation, and drivers. Installing libcamera does not supply those parts. See [libcamera architecture](https://docs.libcamera.org/master/libcamera_architecture.html).

A/B update with boot health confirmation is an architectural target. RAUC exposes boot-slot success/failure marking, but the board bootloader and camera-specific health criteria still need integration and testing: [RAUC basics](https://rauc.readthedocs.io/en/v1.15/basic.html).

## Executable native prototype

The [C++17 implementation](../../camera/native/README.md) includes a capture worker and a separate durable native simulator authority. The mixed reference path still lets Python launch one image worker and own its journal. The native-authority path moves admission, control, capture, DNG and final commit into C++; adapters carry commands and metadata. These paths reject each other's journals and are not a silent migration. See the [authority decision](../adr/0001-native-simulator-authority.md).

The [Qt/QML UI](../../ui/on-device/README.md) runs offline directly against the native authority, with explicit reference and local API modes. Its desktop behavior is tested independently of DaOS workflows. The [supervisor input model](../../firmware/supervisor-mcu/README.md) is based only on verified accessory nets and has no target MCU/GPIO or power outputs. There is no native hardware driver, DMA or libcamera pipeline handler yet.

[Offline diagnostics](../testing/diagnostics.md) inspect copied SQLite snapshots and capture/calibration evidence read-only. They do not recover or replay requests. The [hardware review](../../hardware/interface-review.md) reconciles the separately supplied CAD/PCB sources and records unresolved optical, mechanical and electrical boundaries.

The [decoded-pixel RTL reference](../../fpga/rtl/README.md) runs in actual Icarus simulation with bounded FIFO, frame integrity/error status and completion-gated checksums. It starts after sensor protocol decoding and supplies no PHY, SLVS-EC receiver, CDC or DMA. The [Linux host path](../../system/linux/README.md) compiles and tests the native POSIX implementation on Debian x86_64, stages integrity-manifested simulator bundles and exercises an offline A/B trial model. Its real host capture/restart checks do not establish ARM execution, a camera BSP, bootloader rollback or physical power-loss safety.
