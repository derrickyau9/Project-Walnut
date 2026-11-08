# Walnut FF-1 supplier and integration verification register

All fields below are **unverified / unanswered** until evidence is entered. This document does not constitute an order or a quotation. No sensor, development board, rights, or reference implementation has been procured or validated.

## Candidate: Sony IMX410CQK

Public [Sony product flyer](https://www.sony-semicon.com/files/62/pdf/p-13_IMX410CQK_Flyer.pdf) reports a 35 mm full-size device, 6064 × 4040 active pixels, 14-bit conversion, eight-lane SLVS-EC, and 19.2 fps at that active resolution in a stated mode. This does not establish current availability, actual camera throughput, exact board-level implementation or publishing rights. The flyer distinguishes 6072 × 4056 effective pixels from 6064 × 4040 active pixels; do not conflate them.

| Question to supplier | Answer / written evidence |
| --- | --- |
| Samples, MOQ, price, lead time and product longevity | Pending |
| Full pinout, voltage limits, sequencing, reset, clocks, thermal and PCB guidance | Pending |
| Register specification, initialization and exposure/gain effective-frame rules | Pending |
| SLVS-EC revision, lane rates and RAW14 packing, alignment and error behavior | Pending |
| CFA, active/optical-black areas, defect maps and calibration requirements | Pending |
| Package datum, optical stack, cover/filter, back focal and mechanical limits | Pending |
| Evaluation board, reference design, actual operating configuration | Pending |
| Permission to publish driver, initialization data, design and sufficient protocol material for independent builds | Pending |

## Receiver and FPGA gate

[Microchip SLVS-EC receiver](https://www.microchip.com/en-us/products/fpgas-and-plds/ip-core-tools/slvs-ec-receiver) lists RAW8/10/12/16, not RAW14, and targets PolarFire transceivers. That list alone does not prove compatibility with this sensor mode, an AMD device, or open-source redistribution. Confirm actual licensing, editable source, protocol and pixel encoding with the vendor. A temporary bring-up receiver must share a documented pixel-stream boundary with any independently redistributable implementation. No connector, pinout, reference clock or timing should be frozen before vendor confirmation.

## First-light checklist (not executed)

1. Inspect assembly; confirm rails, limits, sequences, clocks and reset against authorized data; current-limit and record measurements.
2. Verify identification and control communications; record sensor mode and configuration version.
3. Verify receiver lock, link errors, lane alignment, frame and line counts. Reject bad frames explicitly.
4. Verify raw packing and CFA with test patterns; check black and white levels and expected exposure response.
5. Compare raw buffer values with independently decoded DNG; then add preview and assess performance.
6. Preserve logs, hardware revisions, environmental conditions, test scripts and unexplained errors for every run.

Do not fabricate schematics, footprints, PCB, CAD, supplier approvals or measured tolerances from this list.
