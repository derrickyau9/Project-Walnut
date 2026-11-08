# Information to request before sensor-board design

This is a technical checklist, not a sent message or purchase request. The parent/user must authorize any external contact and any information to share. Do not invent organization, volume, budget or commercial commitments.

## Sony or an authorized imaging distributor

- Exact available/orderable IMX410CQK variant, lifecycle state, minimum order quantity, sample availability and lead time. No current pricing or stock is assumed.
- Full electrical specification, revision and errata; 294-pin map; pin orientation; reserved/NC behavior; all ground and rail contacts.
- Package and recommended land-pattern drawings, board assembly/reflow restrictions, optical datum and height tolerances, cover-glass specifications, handling/storage instructions and thermal limits.
- All nominal and absolute-limit supplies, current versus operating mode, startup surge, decoupling guidance, power-on/off sequence and fault conditions.
- Control interface details, permitted logic levels, 72 MHz clock electrical/jitter requirements, reset timing and exact register-initialization/mode tables.
- SLVS-EC standard revision, actual lane baud rates/modes, supported lane counts, lane ordering/polarity, electrical common-mode/swing, required coupling and termination, packet/embedded-data format.
- Reference schematic/layout and any authorized sensor-driver board or evaluation kit with documented IMX410 support. Ask which interface board/software version is necessary and whether raw data export preserves 14 bits.
- Rights to use the documentation for a custom board and to publish the resulting hardware/driver. Ask separately about register tables, sample code and reference design redistribution.
- What application review, NDA, license or other agreements are required. Request terms for user review; do not assume acceptance.

## Receiver-IP provider

- Confirm IMX410CQK compatibility against the exact mode, lane count, rate, RAW14 packing and metadata.
- Supported FPGA device/package/speed grade, tool version, GT configuration and reference-clock requirements.
- Evaluation duration, simulation models, error diagnostics, testbench/patterns and integration reference designs.
- Price/license scope, source-code terms, build reproducibility, redistribution limits and obligations for an open camera project.
- Any sensor-vendor or patent agreement required independently of the IP purchase.

## SoM vendor / fabricator

- Exact module revision and current connector pin tables, power and enable sequence, peak current, bank-voltage inputs and exposed PL GT list.
- Carrier stackup/routing guidance, thermal/mechanical drawings and mounting/connector load limits.
- A manufacturable sensor land pattern and stackup, appropriate finish/flatness/cleanliness, assembly capability and inspection plan after sensor specifications are known.

Completion evidence should be authorized documents or written vendor confirmations, with revision/date. Marketing pages alone do not close a pinout, timing, procurement or licensing gate.
