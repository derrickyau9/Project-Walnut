# Architecture decisions and evidence

## Confirmed versus unconfirmed

Sony's IMX410CQK flyer establishes an eight-lane SLVS-EC interface, 72 MHz input clock, 294-pin LGA and the readout dimensions used in the budget. Its listed chip dimensions are not a complete package or optical-datum drawing. It does not supply the pinout, rails, lane baud rate, programming sequence or a proven reduced-lane operating mode. [S01]

## Lab receiver candidate

ZCU102 is a practical **candidate** because its HPC0 has eight accessible GTH receive channels and the ZU9EG is on FRAMOS's verified IP-device list. The accompanying CSV/XDC maps only host pins from AMD UG1182 Tables 3-44/45; no sensor lane order is assigned. FMC VADJ is not 3.3 V: the documented choices are 1.2/1.5/1.8 V. EEPROM/IPMI behavior and actual board revision must be checked before power. [S02,S03]

The receiver would recover SLVS-EC data, decode/align packets, extract RAW14, and DMA frames into DDR. Embedded metadata, error flags, overflow behavior and sensor-specific startup must be tested with known patterns before optical images. Reference-clock generation, sensor-clock electrical levels, coupling, termination and any needed polarity compensation remain unassigned pending source data and receiver integration guidance.

For initial bring-up, use short interconnects and an approved reference fixture rather than designing a camera-length flex cable first. Start with receiver test data and sensor test patterns. Record every error counter and prove full-frame pixel ordering. An image that looks plausible is not proof of correct bit depth, black-level handling or lane mapping.

## Receiver IP choices

| Candidate | Evidence-backed capabilities relevant here | Decision still needed |
|---|---|---|
| FRAMOS AMD/Xilinx RX IP | RAW14, eight lanes, SLVS-EC v1.2/v2.0; ZU9EG among verified targets | Confirm actual sensor mode/baud compatibility, license, evaluation limits and permitted redistribution. Encrypted RTL/source option is not an open-source license. [S03] |
| Lattice CertusPro-NX LFCPNX-100 | Eight-lane receiver using PMA/PCS; up to 5 Gbps/lane | IP license and bridge architecture. Hardware evaluation times out after roughly four hours without a production license. [S04] |
| Microchip PolarFire family | Free encrypted receiver core; published guide includes eight-lane support | RAW14 is not in the listed RAW8/10/12/16 formats, so ask the vendor instead of assuming full-quality IMX410 still compatibility. [S05] |

These figures describe IP capabilities, not the sensor's baud rate or selectable modes. We found no verified, maintained, openly licensed SLVS-EC RX implementation in this research. That does not prove none exists.

Sony offers a royalty-free essential-patent license for receivers. This is separate from access to an HDL implementation, the vendor's IP fee, source redistribution rights, and any agreement the user would need to review. No agreement has been accepted. [S06]

## SoM selection constraints

The name “Zynq” does not imply suitable transceivers. AMD's selection guide shows no GTP/GTX on XC7Z010/020. [S07] KR260's onboard SLVS-EC connector has only two lanes; an eight-lane sensor cannot be assumed compatible through an undocumented two-lane mode. [S08]

Trenz TE0803 can advertise eight high-speed channels while splitting them into four PS GTR and four PL GTH on applicable variants. TE0820 exposes four PS GTR in the reviewed revision. Neither is established here as an eight-channel PL receiver platform. TE0807 documentation describes sixteen PL GTH plus four PS GTR, so that family is a more credible small-form-factor candidate; the exact module variant/revision remains to be selected. [S09,S10,S11]

Carrier requirements are not solved by buying a SoM:

- Match the exact device package, speed grade, GT resources, exposed RX pins and reference-clock reachability. Do not count PS GTR as freely available custom-protocol PL GTH.
- Reconcile connector revisions and pin tables against the selected module schematic. A family manual cannot authorize a final pinout for every SKU.
- Provide the module input current and carrier bank voltages under startup and peak image-processing load, not just a nominal input-voltage label.
- Implement module enable/power-good/reset requirements, boot straps, JTAG/debug access and safe power-down. Never drive unknown power-good pins with arbitrary push-pull outputs.
- Allocate storage and display interfaces before committing GTs and MIO. Validate the sustained write path, frame buffering and worst-case stalls separately from theoretical DDR bandwidth.
- Obtain thermal limits and define heatsink/airflow/chassis paths. No camera battery runtime or passive-cooling claim is made without measured power.
- Select a fabricator stackup and solve differential geometry/loss/return paths using the eventual electrical constraints. No trace width, skew limit or layer count is frozen from the flyer.

The existing control board may ultimately enter a verified 3.3 V GPIO path. Its RC outputs require Schmitt conditioning and firmware debounce. The adapter is not a direct FMC connection; it must never drive 1.8 V FMC/MIO inputs without the right interface circuitry. A newly checked TI SN74LVC3G17 is a possible 3.3 V Schmitt buffer, but no new host adapter PCB is released because host connector/firmware/mechanical choices are not finalized. [S12]

## Evaluation and document access

RESTAR FRAMOS identifies itself as an authorized Sony imaging distributor and exposes technical-support/contact routes. Its public portfolio lists IMX410. An indexed Sony-EVB brochure describes a general raw-image evaluation environment, but the direct brochure URL returned 404 during this run; **an orderable IMX410-specific EVB and its compatibility were not verified**. Ask for the exact evaluation kit, sensor-driver board, interface board, software, supported modes and license before committing funds. Whether an NDA is mandatory remains unconfirmed. [S13,S14]

No supplier was contacted, no account created, no NDA accepted, no parts ordered, and no sensor documents obtained through an unauthorized route.
