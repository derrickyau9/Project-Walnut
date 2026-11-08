# Hardware prototype integration review

Reviewed 2026-11-05. **Concept/prototype evidence only. No fabrication release, validated lens fit, sensor acquisition, or electrical host compatibility is established.**

Five supplied Library archives are preserved unchanged in separate version directories under [imports](imports/). Each contains an `extracted/` tree. [provenance.json](imports/provenance.json) records the exact Library/file IDs, Library version 0, archive sizes and SHA-256 values, and every extracted file hash. In the original Windows workspace, the archive files retain verified native NTFS Library identity/version metadata. The portable source ZIP carries those identities in the provenance ledger; it does not preserve or invent NTFS extended attributes on extracted copies. Extracted children are linked to their parent archives by that manifest; they are not new Library items.

| Package | Editable entry / source | Role |
| --- | --- | --- |
| Mechanical concept v0.1 (historical) | [CadQuery source](imports/mechanical-concept-v01-library-v0/extracted/HassleFree_FF1_3D_Concept_v01/source/build_camera.py), [parameters](imports/mechanical-concept-v01-library-v0/extracted/HassleFree_FF1_3D_Concept_v01/source/parameters.json) | Packaging concept, STEP/STL, Blender scene, renders and supplied geometry reports |
| Mechanical engineering review v0.2 (historical) | [CadQuery source](imports/mechanical-concept-v02-library-v0/extracted/HassleFree_FF1_v02_Engineering_Review/source/build_camera.py), [parameters](imports/mechanical-concept-v02-library-v0/extracted/HassleFree_FF1_v02_Engineering_Review/source/parameters.json), [supplied feasibility audit](imports/mechanical-concept-v02-library-v0/extracted/HassleFree_FF1_v02_Engineering_Review/validation/feasibility_audit.json) | Superseded by v0.3 for the active mechanical review; packaging and service-path evidence retained |
| Mechanical thermal-routing concept v0.3 (active) | [CadQuery source](imports/mechanical-concept-v03-library-v0/extracted/HassleFree_FF1_v03/source/build_camera.py), [thermal concept](imports/mechanical-concept-v03-library-v0/extracted/HassleFree_FF1_v03/source/thermal_bridge.py), [keepout explanation](imports/mechanical-concept-v03-library-v0/extracted/HassleFree_FF1_v03/keepouts/README.json) | Conditional isolated-side-landing geometry; no completed sensor heat interface or thermal performance |
| Control board A0 | [KiCad project](imports/control-board-a0-library-v0/extracted/hasslefree-ff1-pcb/hardware/FF1_Control_A0.kicad_pro), [schematic](imports/control-board-a0-library-v0/extracted/hasslefree-ff1-pcb/hardware/FF1_Control_A0.kicad_sch), [PCB](imports/control-board-a0-library-v0/extracted/hasslefree-ff1-pcb/hardware/FF1_Control_A0.kicad_pcb) | Low-speed buttons and local supply supervisor; libraries are bundled |
| Sensor integration requirements A0 | [interface study](imports/sensor-integration-a0-library-v0/extracted/ff1-sensor-integration/design/interface_requirements.json), [architecture](imports/sensor-integration-a0-library-v0/extracted/ff1-sensor-integration/docs/ARCHITECTURE.md) | Candidate host references, arithmetic and unresolved release gates |

Imported source files have not been changed to conceal incompatible assumptions. This review is the integration overlay; the existing [hardware prerequisites](README.md), [HUMI requirements](../mounts/README.md) and [supplier register](../docs/manufacturing/supplier-gate.md) still govern release.

## Mechanical datum reconciliation

Mechanical v0.3 is the active integration-review revision. V0.1 and v0.2 remain preserved unchanged as historical evidence. All three revisions retain the same nominal optical planes; the v0.3 parameter file and independently inspected STL bounds use millimetres and place the active sensor surface at Z=0, with +Z toward the lens.

| Plane | Imported Z | Meaning in this workspace |
| --- | ---: | --- |
| Active surface | 0 mm | Reference coordinate only; actual package-to-image-plane datum unknown |
| HUMI common plane | 12 mm | Unvalidated packaging assumption; not an approved common interface |
| E flange | 18 mm | Nominal reference plane; 6 mm in front of the assumed HUMI plane |
| EF flange | 44 mm | Nominal reference plane; 32 mm in front of the assumed HUMI plane |

The two flange offsets differ by 26 mm. The supplied E/EF parts are plain annuli; the source explicitly omits bayonet lugs, lock/release, contacts, thread, fit tolerances and optical stack. Neither the names nor the exported STEP/STL files establish attachment of a real lens. The 50 mm bore, 74 mm base diameter, 64 mm bolt circle and six 3.2 mm holes are assumptions requiring analysis and measurement.

[HUMI verification step 4](../docs/manufacturing/humi-verification.md) expressly leaves a universal 12 mm datum unapproved until the component envelopes and interfaces are demonstrated. Preserve Z=12 only as this concept revision's parameter. Selecting a real sensor, protective stack, lens rear envelope, locating/shim system and manufacturing process must precede a released datum chain. The concept's independent carrier and internal lens-load frame express useful structural intent, but do not validate stiffness, alignment, fasteners, repeatability or sealing.

**Known sensor-envelope incompatibility:** All three mechanical revisions through v0.3 still use a 40 × 28 mm sensor-substrate placeholder. The official [Sony IMX410CQK flyer, page 2](https://www.sony-semicon.com/files/62/pdf/p-13_IMX410CQK_Flyer.pdf) lists a chip size of 41.071 × 30.815 mm. That already exceeds the placeholder in both directions, and chip size is not a package extent/datum drawing. The present placeholder cannot establish IMX410 fit. Obtain the authorized 294-pin LGA package and optical-stack drawings, then revise the carrier, clearances, thermal path and tolerance analysis in a new working revision.

The mechanical package's `source/FF1_Control_A0_board_only.step` is byte-identical to the control package's board-only STEP. Its 60 × 42 mm outline and four 2.7 mm mounting holes provide a consistent starting envelope. The mechanical source rotates the electronics for its final grip orientation rather than mirroring a PCB. Its board envelope is nominally 1.6 mm thick; the imported board-only STEP's approximately 1.51 mm solid is not a confirmed finished stackup. Components, the upright header, mating cable, enclosure actuators, service access, solder joints and manufactured mounting tolerances remain to be reconciled. An outline fit does not establish an assembled fit.

## Mechanical v0.2 changes and evidence limits

Source inspection confirms that v0.2 encodes the following changes; this is not an independent rerun of the CAD intersection solver:

| Change | Source and supplied evidence | Remaining limitation |
| --- | --- | --- |
| Shell, grip wrap and rear cover | Boolean separation of previously overlapping solids | Manufacturing gaps, retaining details and structural connections are unapproved |
| External buttons/dial | Shafts and matching shell holes added | Travel, switch linkage, retention and real actuator interfaces unverified |
| Battery extraction | Continuous downward tunnel added with a concept clearance allowance | Actual selected battery, latch, wiring, seals and tolerances absent |
| Sensor support and service | Carrier opening reduced to 38 × 26 mm; removable sensor bridge added | Support area applies only to the undersized 40 × 28 mm placeholder; real package/clamping/locating and shim design absent |
| Control PCB support | Removable rails and four spacers added | Actual fasteners, insulation, grounding, populated parts and cable access unverified |
| Mount references | Cartridge outside diameter increased to 72 mm; reference flange to 74 mm | 2.4 mm nominal outer ligament is arithmetic, not strength or lens-fit validation |
| Sensor-plane symbol | Cosmetic symbol moved to the side at nominal Z=0 | Symbol is not a calibrated datum or measurement surface |

The supplied `feasibility_audit.json` and `final_QA.json` report 29 part groups, 351 part-pair checks for each E/EF variant and four passing service-path checks. The source selects 27 groups per variant, which agrees with 351 unordered pairs. All-pair checking tests modeled solid intersection volumes with a numerical threshold; it does not validate face contact, manufacturing clearances, tolerance stacks, preload or load paths. The supplied independent-review assertion remains part of the imported author's evidence, not a newly performed physical review.

The source's battery and storage service checks use continuous unions of swept per-solid bounding boxes, a conservative sufficient clearance test for the modeled straight translations. Sensor-bridge and control-assembly checks reconstruct constant Z sections, check reconstruction differences, then sweep those sections continuously. They are not merely a few sampled positions. Their scope still depends on the explicit removal order: rear cover/display/control caps are removed; sensor service additionally removes the control assembly, main-board envelope and thermal reservation. Cables and retaining hardware are unmodeled. These checks do not establish complete real-world disassembly or tool access.

Local static inspection parsed the supplied Python sources, inspected all 29 binary STL groups against the supplied manifest bounds, confirmed the control-board STEP is still byte-identical to the PCB package and checked report/count consistency. No imported CAD generator, B-rep interference computation, service sweep or STEP round-trip was executed on this executor. The local evidence is recorded separately from the supplied CAD results in [integration-validation.json](imports/integration-validation.json).

**Historical v0.2 thermal blocker:** in the preserved v0.2 model, the sensor placeholder and heat-spreader reservation are separated by 19 mm along Z. That gap is independently visible in the v0.2 exported mesh bounds and is explicitly reported as unresolved by v0.2. There is no bridging thermal interface, measured dissipation, selected cooling path or validated temperature performance. A copper-colored reservation does not close that gap. The sensor package/optical stack, functional bayonet/lens clearance, actual electronics/cabling, structural fastening and tolerance analysis remain release blockers; the supplied `engineering_release_ready` value is false.

## Mechanical v0.3 conditional thermal routing

V0.3 supersedes v0.2 for the active mechanical review. It retains the earlier packaging concept and adds a proposed one-piece fixed thermal bus, a separate adjustable side landing, screw/nut space reservations and extra chassis-mount seats. The landing is beside the optical assembly rather than a new contact on the sensor or its carrier. The current nominal route spans the original 19 mm axial region, but **the sensor heat path remains disconnected**. The source and reports explicitly set `no_sensor_heat_connection=true`, `no_thermal_performance_claim=true` and `engineering_release_ready=false`.

| Item | V0.3 data | Interpretation |
| --- | --- | --- |
| Public chip XY minimum | 41.071 × 30.815 mm | Independently sourced chip dimensions, not package dimensions or a land pattern |
| Guard XY | 45.071 × 34.815 mm | Chip XY plus 2 mm per side, a provisional routing allowance |
| Guard Z | −21 to 0 mm | Chosen routing bay; not published chip/package thickness |
| Nominal collector-to-landing axial span | 19 mm, landing front Z=−2 mm | A geometric route, not sensor-to-cooler contact or heat transfer |
| Modeled slot hard endpoints | Landing front Z=−5 and −1 mm; spans 16–20 mm | Conditional geometry with a retained shaft reservation |
| Candidate usable settings | Landing front Z=−4.5 to −1.5 mm; spans 16.5–19.5 mm | Provisional endpoint allowance, not a tolerance-qualified operating range |

The new `keepouts/` STEP/STL files are exclusion volumes for this routing concept. They are not an IMX410 package model, and the real package may require more XY/Z space and additional PCB/FPC clearance. The old 40 × 28 mm sensor placeholder and carrier remain in the assembly for comparison, still smaller than the public chip dimensions. No sensor seating or thermal contact is approved by adding a larger exclusion box.

The supplied feasibility report lists 32 groups, 435 part-pair checks per E/EF variant and five service paths. Source inspection confirms 30 groups per variant, consistent with 435 unordered pairs. The fifth path removes the whole nominal thermal assembly rearward using conservative continuous sweeps of its construction subparts, after removing the stated rear and electronics parts. The sensor-bridge removal now explicitly requires removal of the whole thermal module. Cables, actual mount hardware and tool access remain outside these modeled checks.

The supplied thermal audit tests five discrete landing positions (Z=−5, −4, −3, −2, −1), positive modeled optical-part clearances, nominal bus/slider surface contact and conditional 0.2 mm overtravel interference at both slot ends. These are supplied geometry results, not a continuously verified adjustment sweep or proof that every intermediate setting is clear. The reported zero distance at the bus/slider joint establishes only nominal modeled surface contact. Retained fastener strength, preload, material/process choice, surface finish, TIM, contact resistance, shared-chassis thermoelastic movement, temperature and reliability remain unvalidated. The screw/nut exports have no released threads or manufacturing definition.

Local static checks inspected all 32 exported mesh groups, compared their bounds with the supplied manifest, checked both keepout dimensions, confirmed the unchanged control-board STEP and reconciled the supplied report counts/status fields. Nominal landing and clamp bounding boxes are separated from the guard; this limited exported-envelope check is recorded separately and does not prove whole-assembly or actual-package clearance. No CadQuery generator, intersection/service solver, parameter-regression case or thermal/contact analysis was rerun here. The supplied new thermal render was visually inspected.

A supplier-approved sensor/module heat interface, complete package/optical datum drawing and measured operating power/temperature limits are still needed before a physical heat path can be designed and validated. No contact with or pressure on bare die, optical cover or an unapproved package surface is authorized by this concept. Functional lens geometry, real populated electronics/cables, fastening, tolerances, thermal performance and manufacturing release remain open.

## Control board interface reconciliation

Static inspection confirms 15 electrical components, four mechanical mounting holes, 35 connected pin/net assignments, 272 routed track segments and five vias. The PCB pad/net assignments agree with the supplied exported schematic netlist. J1 is a 2.54 mm six-pin header with this accessory-local assignment (KiCad names carry a leading `/`):

| J1 pin | Net | Local function |
| --- | --- | --- |
| 1 | V3V3 | External regulated 3.3 V input |
| 2 | GND | Ground |
| 3 | HALF_N | Active-low half-button signal |
| 4 | FULL_N | Active-low full-button signal |
| 5 | RESET_N | Open-drain local supply-supervisor output, pulled up to local 3.3 V |
| 6 | GND | Ground |

This fixes the imported accessory's net names, not a system connector or electrical protocol. There is no MCU, sensor initializer, frame receiver, high-speed sensor interface, battery regulator or charger on this board. The two independent tactile buttons simulate half/full inputs; they are not a validated dual-stage shutter actuator. D1 indicates local supply only.

The supplied [engineering notes](imports/control-board-a0-library-v0/extracted/hasslefree-ff1-pcb/docs/ENGINEERING_NOTES.md) disclose 10 kΩ pull-ups, 1 kΩ series resistance and 100 nF button filters. They require a compatible Schmitt input and stable-state firmware debounce, with 10–20 ms as a provisional bench starting range. Firmware event semantics, host connector/pins, input thresholds, bank supply, startup/down behavior, back-power/clamp currents, grounding, ESD/cable protection and current budget remain unvalidated. `RESET_N` is not approved as Zynq PS_POR_B, system reset or sensor reset. A local supply flag cannot substitute for the sensor power-sequence design.

**Host voltage blocker:** AMD UG1182 documents ZCU102 VADJ_FMC choices of 1.2, 1.5 and 1.8 V, not 3.3 V. The accessory's pull-ups therefore do not justify connecting J1 to FMC pins or other unverified Zynq inputs. Define and independently review a real host adapter with the chosen bank voltage and required conditioning/translation before connection. The sensor study's candidate Schmitt buffer is not a released host adapter.

## Sensor/receiver study reconciliation

A separate primary-document review in this session checked all eight imported J5 HPC0 receive P/N mappings against official AMD UG1182 v1.7 Tables 3-44 and 3-45, pages 96–97. They match as host-side references. See [primary-source-review.json](imports/primary-source-review.json) and the [imported CSV](imports/sensor-integration-a0-library-v0/extracted/ff1-sensor-integration/design/zcu102_hpc0_rx.csv). CSV and XDC entries also agree with each other.

This does not assign IMX410 sensor lane order, sensor package pins, electrical PHY, sensor control pins, link baud, GT reference frequency, complete timing constraints or a usable receiver. The XDC is an illustrative pin fragment, not a synthesis/implementation result. Match the exact physical ZCU102 revision and receiver integration guidance. UG1182 also documents the carrier's series AC coupling; do not infer a complete sensor coupling/termination design from that fact.

The official Sony flyer independently supports the study's eight-lane SLVS-EC description, 72 MHz input clock, 294-pin LGA and 6064 × 4040 active-pixel 14-bit mode at 19.2 fps. It does not replace full authorized electrical, mode-register and mechanical documents. Sensor availability, supported reduced-lane modes, complete pinout, operating rails, sequencing, driver/IP access and redistribution rights remain pending in the supplier register.

Independent arithmetic reproduces the supplied active-pixel lower bounds:

| Study mode | Packed payload | 16-bit-container write stream | One write + one read in 16-bit containers |
| --- | ---: | ---: | ---: |
| 6064 × 4040, 14-bit, 19.2 fps | 823.152 MB/s | 940.745 MB/s | 1.881 GB/s |
| 6064 × 4040, 12-bit, 40.58 fps | 1491.227 MB/s | 1988.303 MB/s | 3.977 GB/s |
| 4160 × 2160, 12-bit, 97.57 fps | 1315.087 MB/s | 1753.450 MB/s | 3.507 GB/s |

These are decimal MB/GB. For the full 14-bit mode a packed active frame is 42,872,480 bytes; a 16-bit-container frame is 48,997,120 bytes; four such buffers require 195,988,480 bytes. All numbers exclude optical black/blanking, embedded data, encoding/packet overhead, memory inefficiency, extra ISP passes and storage stalls. They are not measured link, DDR, storage or camera throughput. The 12-bit mode input tuples remain sourced from the supplied study rather than an independent mode-programming demonstration.

The study's seven gates remain open; these are the sensor study's labels, separate from product milestone numbering:

| Gate | Remaining evidence |
| --- | --- |
| G1 Sensor documents | Authorized pinout, package/datum, limits, rails, mode/control information and publishing rights |
| G2 Receiver/IP | Exact sensor mode/baud/RAW14 support, build/evaluation access, acceptable license and redistribution boundary |
| G3 Host hardware | Selected board/module revision, schematic, connector and complete constraints; reference mapping alone is insufficient |
| G4 Power | Mode-specific rail/current/startup/down sequence and thermal/battery budget |
| G5 SI and assembly | Electrical limits, coupling/termination, connectors, approved stackup, SI/DFM and independent schematic/layout review |
| G6 Digital bring-up | Known test-pattern capture to memory with packing/order/counters/errors checked |
| G7 Optical validation | Real-frame dark/flat/color and thermal measurements, then mount alignment and reliability tests |

## Verification performed and limits

Run `python -B hardware/imports/check_imports.py` from the workspace to reproduce the local static checks. [integration-validation.json](imports/integration-validation.json) records the result. Archive CRC checks, safe extraction path checks and SHA-256 verification passed for five archives and 313 extracted files; the original v0.1 and v0.2 files remain hash-identical. The control and sensor packages' own checksum manifests also matched, covering 35 and 12 listed files respectively.

The check parses imported Python syntax and KiCad S-expressions, compares PCB pad assignments with the supplied netlist, inspects selected binary STL bounds, confirms the shared board STEP bytes, recomputes all budget numbers and checks CSV/XDC consistency. The supplied datum drawing, PCB preview, v0.2 rear-service structure render and v0.3 thermal concept render were visually inspected. Imported reports claiming CadQuery geometry checks and KiCad ERC/DRC passes remain supplied evidence; this executor has no CadQuery/pcbnew/KiCad CLI, so those tool checks were not rerun.

No imported CAD generator was executed or silently regenerated. No assembly/fit, electrical bench test, FPGA synthesis/implementation/timing, sensor capture, optical calibration, supplier approval or fabrication was performed. The source imports unblock inspection and a measured integration revision; they do not close the existing first-light or manufacturing gates.
