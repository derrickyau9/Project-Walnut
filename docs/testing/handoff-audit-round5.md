# Source handoff audit round 5

This audit reviewed the current documentation, UI boundary, retained evidence,
hardware-import inventory, and root-owned source-bundle writer/verifier. It did
not change UI behavior, imported hardware bytes/identities, screenshots, or the
original validation hashes. Packaging implementation and the rights inventory
remain owned by the root task. No completed round 4 broad test suite was rerun.

## Privacy and inclusion findings

| Content | Handoff treatment and evidence |
| --- | --- |
| Browser screenshot `docs/testing/artifacts/console-mobile-390.png` | Excluded. No screenshot delivery was requested. G1 now calls it workspace-only rather than linking it as an included handoff artifact. |
| Six native Qt renders under `ui/on-device/evidence/` | Excluded from the minimal source handoff. These show synthetic sessions, but some display a local user/temp path and UUID. The UI README and round 4 audit now use textual paths and explicitly describe their workspace-only status. |
| `system/linux/evidence/build-evidence.json` | Contains 58 command/path strings tied to the original local workspace. The packager transforms the handoff copy to `<SOURCE_ROOT>` while retaining the workspace original. |
| `fpga/testbench/rtl-simulation-evidence.json` | Contains 54 tool, command and stdout path strings tied to local workspace/Temp prefixes. The handoff copy uses `<SOURCE_ROOT>`/`<TEMP>`; the original stays unchanged. |
| Runtime state, journals and partial captures | Exclude `device-ui.json`, `device-ui-unknown-*.bak`, service/workflow locks, databases and their journal/WAL/SHM files, `.dng.partial`, captures, build outputs and caches. Policy probes confirmed these exclusions, including when runtime files are placed inside an otherwise included source root. |
| Imported hardware archives/assets | Preserve their original bytes and provenance. Read-only SHA-256/length checks passed for all five ZIP archives and 313 extracted files; ZIP CRC checks passed. No private/configuration/key/cache/database/log entry names were found in the five archive member lists. No imported file was rewritten. |
| Dependency author contacts and examples | Email matches were upstream attribution/license text. SQLite documentation contains illustrative user paths; test token/password matches are explicit fixtures. These are not discovered user credentials and should not be blindly redacted from preserved dependencies or tests. |

No high-confidence live credential, private-key, signed-download-URL or unrelated
personal-file finding was identified in the inspected readable text and entry
names. This is a bounded static/heuristic audit, not an exhaustive forensic scan
of binary CAD/Blender metadata or a determination of publication rights. The
separate rights inventory governs the handoff's permission statements.

An in-memory audit of the then-current packager selection covered 469 selected
files before this report was added. It found zero selected non-import raster
images, zero remaining actual workspace-prefix matches and zero credential-
signature matches, with two redacted evidence copies. Final member counts come
from the completed source manifest, not this point-in-time inventory. Generic
`$HOME`, `/tmp` examples, operating-system font paths and synthetic fixture URLs
are not personal workspace disclosures.

Imported Library identifiers are intentional provenance, not authentication
credentials. Archive hashes and the recorded IDs remain unchanged. A portable
ZIP preserves archive bytes and manifest provenance; it should not be described
as preserving native NTFS extended attributes on every recipient filesystem.

## Independent source-bundle review

Two disposable probes initially found defects, which were sent to the packaging
owner without editing the writer or its tests:

1. A JSON dictionary key containing an absolute Windows workspace path remained
   private because evidence normalization transformed only values. JSON escaping
   also bypassed the raw prefix check. The creator returned verified success
   with zero redacted copies. The owner added key normalization, collision
   refusal and escaped-prefix handling. A focused retest now removes the private
   key prefix and records one redacted copy.
2. The verifier accepted a self-consistent manifest listing
   `camera/.aws/credentials`, and did not require per-file origin/rights fields.
   Checksums alone did not enforce the source policy. The owner added allowed-
   root/exclusion checks, portable Windows filename checks, required origin/rights
   fields and credential-signature rejection. A focused retest now refuses the
   same private path even when its digest and origin/rights fields are valid.

The owner reports seven source-bundle regressions passing. Inspection also
confirmed exclusive destination publication, deterministic ZIP metadata,
per-member digest/length verification, duplicate/traversal/link rejection,
output refusal inside included source roots and bounded archive verification.
No required current source omission was identified by this tree/policy review.
Final extraction, clean build/test and the finished ZIP verification remain the
packaging owner's release checks; this audit does not claim those pending steps
were already executed.

Historical evidence hashes describe workspace originals, including excluded
images. Redacted evidence copies must keep a clear transformation record. The
handoff's `SOURCE-MANIFEST.json` is the inventory of actual bundled bytes; changing
local prefixes in an evidence copy is not a rerun of the associated test.

## UI and claim review

One new focused QML probe checked the authority-provided DNG path display:
`textFormat=0` (plain text), `readOnly=True`, and all 17 characters of
`<b>INERT_TEXT</b>` remained literal. The controller uses fixed status/error text,
canonical UUIDs and validated capture fields; it does not open a path from capture
metadata. No new verified UI defect was found, so no UI code or regression was
changed and the nineteen round 4 Qt tests were not repeated.

The stale sentence in `camera/native/README.md` claiming no Linux execution was
corrected to the actual Debian x86_64/WSL GCC evidence. Windows/MSVC, Linux host
simulation, Windows Qt, logical Icarus RTL and physical hardware remain distinct.
The project overview, G1 evidence, release matrix, FPGA guide, Linux host guide,
supervisor reference and hardware overlay retain their scope limits: no working
camera/sensor path, ARM body validation, SLVS-EC receiver/CDC/DMA/bitstream,
manufacturing release, measured calibration or completed thermal interface is
established. Imported CAD checks remain supplied evidence; local static checks
are identified separately. The v0.3 sensor placeholder, datum, lens geometry,
host voltage and thermal-contact blockers remain explicit.

A Markdown scan after the documentation edits found no non-import raster links.
Workspace screenshot files and their old validation hashes were left unchanged.
There is no remaining audit blocker identified after the packaging fixes; final
bundle verification and rights decisions remain with the packaging owner.
