# Engineering rationale and limits

Date: 2026-11-05. Scope: FF1_Control_A0 only. This is a new low-speed test accessory, not an implementation or validated substitute for an existing Hasselblad camera board.

## Authoritative electrical references

1. Texas Instruments, TLV803E/TLV809E/TLV810E, SLVSES2J, Rev. J: https://www.ti.com/lit/ds/symlink/tlv803e.pdf . Sections 5, 6, 7.5, 7.6, 8.3, 11 and 12 used for part suffix, pinout, timing and decoupling review. Exact orderable part: https://www.ti.com/product/TLV803E/part-details/TLV803EA29DBZR . Retrieved through web tools 2026-11-05.
2. Omron/Aratas B3F manufacturer family datasheet: https://omronfs.omron.com/en_US/ecb/products/pdf/en-b3f.pdf . Manufacturer URL also indexed as https://components.omron.com/system/files/2023-01/datasheet_pdf/A070-E1.pdf (now redirects to Aratas; direct retrieval failed). Gold-contact B3F-1002-G is the selected button; manufacturer product listing: https://www.aratas.com/sg-en/products/switches/B3F . Datasheet electrical values and footprint were also independently checked against manufacturer-origin copy at https://my.avnet.com/wcm/connect/3305df28-7f87-41c9-a4b3-b7517531182e/OMRON-B3F-Series-EN-Datasheet.pdf?CACHE=NONE&CVID=oMwUr.i&ContentCache=NONE&MOD=AJPERES . No claim of procurement availability.
3. Kingbright APT2012SGC candidate LED, DSAA8210 V18B, 2025-06-03: https://www.kingbrightusa.com/images/catalog/SPEC/APT2012SGC.pdf . Read for package, polarity and current/voltage context. A generic footprint is used, not claimed to be the vendor's exact preferred land pattern.
4. Bundled graphics and footprints originate from the installed KiCad 9 libraries. Attribution/license text is in `KICAD_LIBRARY_LICENSE.txt`.

## Circuit calculations

- Each closed button draws about 3.3 V / 10 kΩ = 0.33 mA steady state. This falls within the gold-contact family micro-load rating (100 µA–50 mA at 3–24 V). Do not silently substitute ordinary silver-contact switches.
- Filter capacitor is AFTER the 1 kΩ series resistor. Approximate release time constant is (10 kΩ + 1 kΩ) × 100 nF = 1.1 ms; press time constant is 1 kΩ × 100 nF = 0.1 ms. These are nominal first-order estimates with unloaded inputs, not SPICE or bench validation. Peak capacitor discharge current is approximately 3.3 mA. Mechanical bounce can last 5 ms; RC does not guarantee debounce. Require a 3.3 V Schmitt-capable input and stable-state firmware debounce of 10–20 ms.
- Reset pull-up current while asserted is about 0.33 mA. The selected supervisor has a nominal 2.93 V falling threshold. Its ±2% full-temperature limit gives a calculated interval 2.8714–2.9886 V. Its A-option release delay is 130–270 ms, 200 ms typical. Check the datasheet test conditions when setting a bench acceptance range.
- RESET_N rises only when the external supply clears the positive threshold and the delay completes. Below the supervisor's approximately 0.7 V POR floor, do not assume a guaranteed asserted logic output. This is not a fail-safe indication for arbitrary power sequencing.
- R6 is 2.2 kΩ. If LED Vf is 2.2 V, illustrative current is (3.3−2.2)/2.2k = 0.5 mA. The datasheet's 2.2 V value is specified at 20 mA, so 0.5 mA is an estimate, not a predicted measured operating point. The current upper bound at 3.3 V is 1.5 mA even if Vf is approximated as zero; low-current brightness remains to be checked.

## Footprint and assembly review

- U1 uses bundled KiCad `SOT-23`, with 1.475 × 0.6 mm rounded pads centered at (−0.9375,±0.95) and (+0.9375,0) mm. TI's DBZ0003A land example uses approximately 1.3 × 0.6 mm pads with 2.1 mm opposing-row spacing. The generic land pattern is intentionally disclosed, not claimed to exactly duplicate TI's recommended layout. Final assembler/package review is required before fabrication.
- SW1/SW2 use KiCad's Omron B3F-10xx footprint: hole centers in a 6.5 × 4.5 mm rectangle, 1.0 mm drill. The two terminals on each long row share the same schematic terminal. Confirm continuity on a sample part before assembly.
- D1's KiCad pad 1 is cathode; pad 2 anode. Do not infer polarity solely from tape orientation.
- Top-side SMT parts, through-hole header and switches. 0603 refers to imperial size, 1608 metric. Confirm solder paste, soldermask and mixed-technology assembly with the chosen assembler. No pick-and-place production file is released.
- There are 5 through vias and no intentional via-in-SMD-pad. No filled/capped via process is assumed.
- Nominal FR-4, 1.6 mm complete board, two copper layers. Copper weight, actual dielectric construction, finish, mask, plating and finished-hole tolerances are not yet approved by a fabricator. Default EDA geometry is not a released stackup.
- Only the outer mounting holes and low-speed layout are established here. No grounded chassis, EMI shield, cable retention, connector keying, button actuator linkage or lens/sensor datum is established.

## Integration gates: IMX410 / Zynq camera

Do not connect this accessory to an unverified camera or development board. A separate interface adapter must establish: exact host connector/pins; bank supply and input limits; compatible Schmitt thresholds; common ground; power-up/down order; back-power and clamp-current behavior. No host pin is assigned in this package. RESET_N may be sampled as a supply flag after compatibility review; it must not be assumed suitable for Zynq PS_POR_B, system reset, or sensor reset.

The following remain unknown and block the sensor/mainboard design: authoritative IMX410 full pinout and package drawing; all sensor supply voltages/current transients and sequence; reset and clock requirements; SLVS-EC lane count/rate/mapping, receiver IP and licensing; selected Zynq part and transceiver/IO capabilities; memory and storage throughput; battery/charging and thermal budgets; exact camera envelope and optical datums. No empty sensor symbol or fictitious net names are connected into this board as a substitute.

## Validation boundaries

Actual: KiCad parses the project; ERC passes; DRC with schematic parity passes; explicit circuit netlist comparison passes; SVG/PDF visual review completed; board-only STEP exported. No DRC exclusions used.

Not run: assembled board continuity or functional measurements; hardware-in-loop tests; Monte Carlo/temperature analysis; complete rail sequencing; ESD/EMC; cable transient testing; manufacturer DFM; component procurement; physical enclosure fit; image acquisition. No production order or publication has been made.
