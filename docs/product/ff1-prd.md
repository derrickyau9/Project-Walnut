# Walnut FF-1 product scope and acceptance gates

Status: product requirements and targets, not an assertion that a camera exists. Updated 2026-11-05.

## Product intent

Walnut FF-1 is the planned first camera body of Project Walnut. DaOS Camera
remains the camera software platform; see the [current naming](naming.md).

An independently operable, repairable, extensible full-frame still camera with a native capture path and offline reproducible workflows. DaOS Camera organizes work; camera control and pixels remain in the native capture subsystem. HF-Sim tests the software contract and does not emulate a particular sensor's electrical behavior.

## V1 targets, pending measurements

- 35 mm full-frame, approximately 24 MP; evaluate IMX410 only after supply, documentation, and rights gates pass.
- Manual exposure and focus, single still, 14-bit sensor sampling as the preferred mode, Bayer DNG, removable photo storage, local controls and preview. Target approximately 1 fps under disclosed measured conditions; lower-resolution preview first.
- Complete removable HUMI mount assemblies, first target E and EF mechanical modules with verified manual lenses; report mechanical and individual electronic functions independently. Neither mount is currently validated.
- Separate system, photos, configuration and calibration storage; local recovery/update path; basic photography independent of automation. Interchangeable battery and USB-C external power remain design targets, not approved electrical designs.
- First workflows: exposure brackets, interval capture and dark/flat sequences. A saved workflow must execute offline without a model.

Not V1 release prerequisites: IBIS, eye AF, high-speed burst, 4K video, EVF, continuous AF or PDAF. Future hooks do not constitute supported features.

## Gate evidence (all pending unless separately backed by reports)

| Gate | Required evidence |
| --- | --- |
| G0 | Independent project, source inventory and rights record, product scope and architecture decisions. No unlicensed source copied. |
| G1 | Automated synthetic Bayer-to-DNG-to-independent-readback pixel comparison; state, cancellation, restart and fault cases; runnable control console. Demonstrates software only. |
| G2 | Written vendor answers on sample supply, complete electrical and control data, SLVS-EC/RAW14, physical package and publication rights. Working open receiver and sensor initialization path established. |
| G3 | Actual sensor/receiver/FPGA/driver capture; documented first-light procedure; repeated full frame captures without unexplained errors. |
| G4 | Without a computer: compose, adjust exposure, trigger, store, review and export. |
| G5 | E and EF mechanical assemblies tested with named lenses; axial repeatability, edge focus and load path measured. No unverified electronic compatibility claims. |
| G6 | Traceable calibration, independent DNG decoders, thermal/power/long-run/fault/power-loss results and recoverability. |
| G7 | Editable source, BOM, build and assembly instructions; second independent builder photographs successfully. |

A gate cannot be marked passed merely by the presence of documents, mock data, a rendered UI, or an unrun test.

Implemented software and executed tests are recorded in [G1 evidence](../testing/g1-evidence.md). That evidence covers the HF-Sim contract prototype; native production software and all physical camera gates remain open.
