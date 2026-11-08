# License and source intake decision (proposed)

The [source/rights inventory](INVENTORY.json) identifies bundled dependencies,
supplied prototype provenance, scoped notices and external runtime requirements.
The source ZIP adds per-file origins and hashes in `SOURCE-MANIFEST.json`.

Status: pending project owner/rightsholder confirmation. The requested license choices are proposals, not proof that existing files are licensed. No license is granted by this document for third-party work.

| Material | Proposed future license | Before release |
| --- | --- | --- |
| Original hardware CAD, PCB and FPGA design | CERN-OHL-S-2.0 | Confirm rightsholders; provide exact license text and notices; record dependencies and tool restrictions. |
| Original application/native service | GPL-3.0-or-later | Confirm all contributors and dependencies; include full license text and notices. |
| Independent interface definitions / SDK | Apache-2.0 | Confirm boundaries and rights; include exact notices, license and patent considerations. |
| Original documentation | CC BY-SA 4.0 | Confirm authors and third-party content and attribution. |
| Third-party work | Original terms | Track origin, revision, copyright, license text, modifications and obligations. |

DaOS runtime and framework source must not be imported merely because their owner requested the project. The upstream framework provenance states the export had no license file and does not assign a license. Record written rights clearance per component first. Do not copy private business system code, settings or customer data. A project-level license cannot relicense third-party content.

Before public release, reconcile dependencies and transitive license obligations, publish source notices and corresponding license texts, and verify the hardware's editable source and disclosed scope meet the [OSHWA definition](https://oshwa.org/resources/open-source-hardware-definition/). Avoid claiming certification or completed open hardware before evidence exists.
