# HUMI interface — Walnut FF-1 research and validation plan

HUMI means **Hardware Universal Mount Interface**, the stable interface name
within Project Walnut. The planned Walnut FF-1 uses this interchangeable-mount
research; the name does not establish universal lens compatibility.
See the [project naming](../docs/product/naming.md).

No mount geometry, tolerances, CAD or lens compatibility has been validated.
This is not a manufacturing specification. Sony E and Canon EF are initial
mechanical module goals, subject to a complete mechanical and legal review.
Nikon Z is a packaging consideration, not a support claim. Sensor reference,
optical stack, inner clearance, mount interface plane and shimming range remain
open until real components and dimensional drawings are measured.

## Required design package

Establish coordinate systems for the sensor photosensitive plane, structural
reference and lens mounting flange. Model the actual sensor package, glass,
fasteners, light baffles, connectors, lens protrusions and tool access as a
section and 3D assembly. Analyze tolerance stack and thermal expansion with
identified measurement uncertainty; establish manufacturable shim increments
from actual measurements. The metal lens-load path must bypass the sensor PCB.
Provide repeatable mechanical location, module identification, controlled light
sealing and an assembly/calibration procedure.

## Initial acceptance record

For each specific lens and module revision record serial, firmware if relevant,
measurement instruments, ambient conditions, shim configuration and measured
flange position at center and around the perimeter before/after repeated
removal. Capture a suitable target at center and corners at infinity and close
distance, document aperture and focusing method, and retain RAW plus analysis.
Define thresholds only after uncertainty and prototype measurements are known.

Publish six independent tested outcomes: mechanical attachment, aperture,
identification/metadata, focus drive, autofocus and optical stabilization.
Initial photography requires a lens whose focus and aperture work manually.
Mechanical mounting by itself is not electronic compatibility.
