# Final R9 source: actual bound-data A/B rollback

PASS. The unchanged frozen update model ran three actual full-system ARM64
boots against new copies of `build/target-e6ac70179d285645aab4226b`. The parent
main orchestration003 was completed before use. This is the final R9 image with
the final Python SDK/reference fixes, rather than the earlier target001 image.
Original images, prior model runs, product code and existing helpers were not
changed. No additional tests or repeated boot sequence were run.

| Actual selected slot | Artificial release version | Healthy | Verified boot seconds | Durable attempt UUID |
| --- | --- | --- | --- | --- |
| A | 1 | true | 13.407 | b14cb8a5-86f5-4148-9007-44db05368ffb |
| B | 2 | false | 13.642 | 9dd38510-b634-4038-89fc-9feead9dd5de |
| A | 1 | true | 13.741 | 941ed79e-870f-4d00-a998-3354f785b6c0 |

The B trial's one-attempt budget was committed before launch. Its explicit
unhealthy result followed successful real application/state checks; it could
not be confirmed. The next boot selected and explicitly confirmed the original
A image. Durable control state ends confirmed=A, trial=NULL, pending=NULL,
remaining=0, with attempt statuses confirmed/unhealthy/confirmed. A clean guest
lifecycle PASS is distinct from healthy=true. This is same-application code
with artificial release markers, not a cross-version firmware update.

All three guests ran normal ARM64 Python/native code as UID 1000, required
capabilities API 1.1 and record versions 1 and 2, and queried the original
version-2 capture at `/data/ui-r7/offline/basic`. No health check acquired
control or submitted a capture. Exactly one unchanged complete row remained.

- Original capture/frame UUID: `374ff3b0-e4ed-47bf-92b3-a81c90f5c344`.
- Persistent exposure: 11700 us; the full settings object matched the pinned baseline.
- Full pinned binding: `cb1-a4d0d90ce30689a7202a88b28945bb6bca37fba91745b99323687213d2d5be17`; the complete binding and state calibration descriptor matched before and after health checks.
- Original DNG SHA256: `993b7048080266f9e76c0d0c9612dca9eb14ade355ae29240df4094cb4b27b82`.
- Hardware calibration and applied pixel corrections stayed false.

Six protected hashes stayed identical across all three boots: actual persistent
device settings, the full capture binding, original DNG, logical capture rows,
and two separately labeled configuration/calibration preservation fixtures.
The fixtures were not substituted for actual bound-state checks. Data-image
before/after hashes form a contiguous chain; normal startup epochs and reports
changed without changing the protected state. All native children closed.

Both selected slot root hashes remained unchanged during their boots. The A
root hash was `abd519880249f9618aa73460279bea6da95325a5048acb1d471266199bde07e6`; the B root hash was
`fefb9536c41183646efed5051c21c9a53f10a306c37e5cc19d8eb4a9e5bfdb9f`. Every guest cleanly unmounted its data
filesystem and passed read-only fsck. Independent read-only review recomputed
slot/kernel/bootstrap/final-data hashes, report/serial commitments, durable
SQLite state, and current source/runtime bindings; it found no mismatch.

Original upstream root/kernel/bootstrap/data hashes were recomputed after the
model run and matched the pinned policy, including root SHA256
`3358206b5f5ed7eaeaccc16ee7fa97a2bb607ee036e8b91e3983ed2b001f282a`. `verification.json` records exact hashes,
source fingerprints, durations and all copied-file provenance. `preflight.json`
and `base-run-state.json` retain the completed orchestration and disk-space
check (32.33 GB initially free, 2.23 GB estimated with reserve). All frozen model
source snapshots still matched after execution.

Original reports, all three serial logs, extracted guest reports, immutable slot
manifests, runtime evidence, completed base build/boot evidence, final durable
control database and the original command output are preserved byte-for-byte
beside this report. Prior legacy and earlier bound evidence remain separate.

No host mounts, block devices, root privilege, physical flash writes, external
NICs or host filesystem shares were used. Hashes are integrity checks, not
authentication. Boot-ROM/bootloader A/B environment handling, secure anti-rollback,
trusted update distribution, mixed application-version migration, watchdog/reset
behavior and physical storage power-loss safety remain unverified. There were
no execution blockers or product changes in this final run.
