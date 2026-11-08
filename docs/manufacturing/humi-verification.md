# HUMI mechanical development and test plan

Status: design inputs only; no dimensions, CAD, hardware or lens compatibility are approved. No manufacturing order should use this as a drawing.

## Design sequence

1. Obtain sensor package drawing, image plane datum and optical stack; specify protective glass and filter stack before choosing the body reference plane.
2. Collect official mechanical envelope and mount definitions and measure independently where rights and accuracy permit. Model the mating flange, lens rear projection, bayonet, lock, light baffles, fasteners and contacts; record source and uncertainty for each.
3. Establish the structural datum chain from sensor package/image plane to rigid lens mount frame. Model the interchangeable module, locating pins and shim stack. Keep lens load out of the sensor PCB. Evaluate fastener shear and service access.
4. Parametric sectional and 3D interference models must include the full tolerance stack, sensor translation and tilt adjustments, mounting screws and likely lens protrusion. Reserve Z exploration without claiming support. Do not set a universal 12 mm datum until the envelope and interfaces are demonstrated.
5. Specify material, manufacturing process, tolerances and measurement procedure against lens and focus tests; release editable part and assembly files and a revision-controlled BOM.

## Test record template

- Body, module, sensor and shim versions; service history; lens model, serial, adapter and aperture; test distance, temperature, instrument identity and calibration.
- Measure flange-to-sensor image plane and tilt at center and four corners, as physically accessible. State method, instrument uncertainty and datum definitions.
- Assemble and remove repeatedly using prescribed torque; record each measurement and shim change, seating defects and light leaks. No numeric pass criterion is claimed until design analysis and instrument capabilities set one.
- Test infinity and near focus, center/corners, wide-open and stopped down; compare documented reference captures. Check for mount wobble and alignment under representative lens loads.
- Document each lens separately: mechanical fit, aperture, electronic identity/EXIF, focus drive, autofocus and stabilization. A mount fit does not imply electronic compatibility.

Calibration and module identity must survive system updates. Replacing a sensor or structural part triggers re-verification. No module is hot-swappable without a separate verified electrical and safety design.
