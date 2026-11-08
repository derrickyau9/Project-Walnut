# Physical and release gates after round six

The current software is executable on Windows/Linux and as static ARM64 programs
under user/full-system emulation. This does not pass the FF-1 hardware gates.
The Library round-five snapshot remains preserved; round-six source/evidence are
separate work. No purchases, supplier messages, fabrication or public repository
publication occurred.

| Gate | Required input/action | Acceptance evidence |
| --- | --- | --- |
| G2 sensor supply and implementation rights | Obtain an actual sample/evaluation path, quotation and authorized electrical/register/mode/RAW14/SLVS-EC information; confirm what initialization/driver/receiver material can be published. | Complete the [supplier register](../manufacturing/supplier-gate.md), with attributable answers, exact document revisions and a legally usable public implementation path. |
| Sensor module and optical stack | Obtain the IMX410 package/datum/cover-glass/filter drawings and verified power/clock/reset constraints. | Replace the undersized 40x28 mm concept placeholder and resolve connector, board, keepout and optical-stack interference without guessing. |
| Receiver and board pipeline | Select a publishable SLVS-EC implementation or authorized specification/test vectors, then verify lane/electrical/packing mappings on actual sensor/FPGA hardware. | Real link lock, line/frame integrity/error counters, Bayer sample correctness and DMA/driver tests; logical RTL alone is insufficient. |
| Physical platform | Select SoM/carrier/display/storage/battery/MCU, verify interfaces and implement the actual BSP, boot chain, input/power/reset supervision and V4L2/libcamera integration. | Boot and independent basic capture on that exact board revision; real interrupt/DMA/timing/storage evidence. A generic QEMU virt guest is not the board. |
| HUMI and mechanics | Resolve the unapproved 12 mm concept plane, real E/EF bayonets/locks/contacts, lens rear envelopes, sensor plane/shims, load path and conditional thermal bridge. | Fabricated assembly, named manual lenses, flange/parallelism/corner-focus/reassembly measurements and measured temperatures. Existing annuli and CAD reports are not validated lens mounts. |
| G6 image quality and reliability | Acquire a working real sensor chain and perform dark/flat/temperature/color/optical calibration; test actual media, power supply and thermal conditions. | Sensor-bound measured calibration, real DNG decoder checks, 1,000 real samples, 8-hour operation and repeated storage/update/power-cut trials with conditions and exclusions recorded. |
| Public release and G7 replication | Confirm project/rightsholder license grants, resolve third-party/runtime/tool obligations and complete editable manufacturing/build/repair materials. | An independent person builds the selected hardware/software revision and makes a photograph. Private source delivery, clean compilation or a virtual image is not this gate. |

See the [hardware integration overlay](../../hardware/interface-review.md) for
specific contradictions in the supplied CAD/PCB packages, including sensor size,
unverified host voltage/connector assumptions and the incomplete thermal path.
Those five prototype archives remain unchanged and separately versioned.

Additional software work remains possible without a sensor: ARM64 Qt/runtime
validation, a Linux virtual-media backend, further workflow/plugin boundaries and
storage/update testing on an explicitly chosen simulated target. These are not
claimed complete or physically blocked. They cannot establish board electrical
correctness, real image quality, mount fit or manufacturing reproducibility.

Unperformed checks are explicit: native ARM64 Python/Qt on a physical board,
target display/GPU/touch/buttons, real sensor/SLVS-EC/FPGA timing/CDC/DMA,
nonvolatile media power cuts, bootloader rollback, battery/thermal behavior,
optical alignment, hardware endurance and independent manufactured replication.
