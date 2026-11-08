# Walnut FF-1 hardware

These materials belong to Project Walnut's planned first camera body,
[Walnut FF-1](../docs/product/ff1-prd.md).

This directory now includes preserved, editable mechanical and control-board prototype sources, plus a sensor-interface study. Read the [integration review](interface-review.md) and [provenance manifest](imports/provenance.json). These sources have not established a manufacturable or electrically/optically validated camera. Do not manufacture from the imported concepts as a released system.

## Gate to a first board

Before schematic freeze, obtain and record a supplier's written answers for
sample availability and ongoing supply; schematic/pinout and supply sequencing;
reference clocks; control registers and effective-setting timing; receiver
protocol and lane rate; pixel packing, active area and optical black; sensor
package and optical stack; evaluation hardware; and permission to publish the
driver, initialization, FPGA receiver and necessary board files. Record any NDA
constraints separately. No quotation or permission is represented as obtained.

The first sensor module must have a separately supported mechanical reference,
safe access to power/clock/reset/control test points, and an identified thermal
path. Specify voltages, tolerances, current limits, PCB stackup, connector and
signal integrity only after acquiring the device data and selecting parts.
Sensor removal requires power-down and subsequent optical recalibration.

## First light protocol

Keep the sensor protected and use current-limited supplies. Record equipment,
board revision, firmware hash and environmental conditions for each attempt.
Check rails and sequence against the actual device data, then clock, reset and
control communication; establish receiver lock; validate frames and counters;
inspect Bayer and controlled exposure response; validate DNG pixels; only then
evaluate preview. Escalate discrepancies instead of masking damaged frames.

Before fabrication, independent schematic and layout reviews must cover power,
clocking, return paths, isolation, thermal effects, connectors and mechanical
clearance. Obtain the electrical and manufacturing specifications first.
