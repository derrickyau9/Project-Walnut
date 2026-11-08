# Release gates and concrete blockers

| Gate | Present evidence | What closes it | Work blocked |
|---|---|---|---|
| G1 Sensor design data | Two-page official flyer only | Authorized complete pinout/package/electrical/mode documents | Sensor symbol/footprint, power page, sensor copper |
| G2 Receiver/IP | Public product guides; no selected license | Exact IMX410 mode and RAW14 support, evaluation/build access and acceptable terms | Validated receiver and GT clock design |
| G3 Host hardware | ZCU102 HPC0 reference pins checked | Selected physical board/module revision, schematic, pin table and reference constraints | Released adapter/carrier pinout |
| G4 Power | Control-board supply only | Mode-specific rail/current/startup/sequence budget | Sensor regulator selection, thermal/battery model |
| G5 SI/assembly | No released sensor interconnect | Electrical limits, connector choices, fabricator stackup and SI/DFM review | Sensor/SoM production layout |
| G6 Digital bring-up | Arithmetic and reference-data tests only | Known sensor test pattern captured to DDR with checked ordering/errors | Claims of reliable acquisition |
| G7 Optical validation | Not started | Real frames, dark/flat/color tests and thermal measurements | Image-quality and finished-camera claims |

## Work that is truly done

- Separate control/supervisor PCB: editable and electrically routed, with its own KiCad checks and prototype limits. It does not close any sensor power or image-acquisition gate.
- This package: host-side reference mapping, candidate comparison, bandwidth calculations, documentation checklist and data-validation tests.

## Work deliberately not fabricated

No guessed sensor BGA/LGA map, rail values, reference-clock frequency, link baud rate, impedance target, track-width/skew rule, FPGA bitstream or blank “production-ready” carrier. The XDC file is a pin-reference fragment; no Vivado synthesis, implementation or timing closure was run. A table test cannot validate an FPGA design.

## Next executable sequence

1. Obtain and review G1/G2 evidence. External outreach requires the user's permission; terms or purchases need their own applicable approval.
2. Match the actual lab board revision, then build and simulate a receiver integration using authorized IP.
3. Create the genuine 294-pin symbol/footprint from approved drawings and cross-check every pin twice.
4. Design rails, sequence, clock/control and receiver electrical interface; perform schematic ERC and independent review.
5. Route an approved low-risk sensor evaluation adapter using a reviewed stackup; run DRC, parity, DFM and SI checks.
6. Assemble only after prototype approval; prove test-pattern capture before attempting optical performance claims.
7. Move the validated chain onto a compact SoM carrier and revisit thermal/mechanical limits. Do not miniaturize an unproven image chain merely to fit the current enclosure concept.
