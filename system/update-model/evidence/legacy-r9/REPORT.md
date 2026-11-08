# R9 legacy-data A/B update model: actual execution

The complete five-boot sequence passed on real full-system virtual ARM64. A
subsequent confirmed-B boot passed with the final evidence-directory sync guard.
The final confirmed slot is B, model version 2. All original R8 image files
remain byte-identical. This is same-application code with artificial on-root
release versions, not an authenticated firmware or physical bootloader update.

| Boot | Selected slot | Version | Healthy | Trial budget remaining | Durable attempt UUID |
| --- | --- | --- | --- | --- | --- |
| 1 | A | 1 | true | 0 | 57acae39-9b63-4f12-b395-51db9afb91fd |
| 2 | B | 2 | true | 1 | c3fc9370-9fdd-469d-adcd-c9aeff755f08 |
| 3 | A | 3 | false | 1 | 2ec47223-6424-4fa7-803c-f85c274bd2aa |
| 4 | A | 3 | false | 0 | 86fb635f-9a98-476e-90e8-5bc461eb337b |
| 5 | B | 2 | true | 0 | bdcb1bb0-803b-412b-bad5-b5c280121ed5 |

The unhealthy A/version-3 candidate ran the real application checks before an
explicit unhealthy result. It could not be confirmed. Its budget was committed
before each VM launch, reopened across coordinator instances, and exhausted
before the actual confirmed-B fallback boot. Each guest proved UID 1000, the
selected on-root version, the same native binary, clean data unmount/fsck, and
an unchanged read-only root. A clean harness PASS is distinct from healthy=true.

Original retained capture UUID: `b86572e6-c679-4079-8f3d-2d2c5c1239bb`. Its DNG SHA256 stayed
`13381de928a4406b242274306dfa599a9c0c4336ea7f6af0e91c339620290fa5`. Exactly one complete capture row remained; no health
check acquired control or submitted a capture. Configuration and calibration
files in this profile are explicitly synthetic preservation fixtures, not
actual persistent device settings or bound calibration. Their hashes and the
logical journal-row hash stayed unchanged across all six boots. Whole data-image
hashes changed as expected for startup epochs and reports, with an explicit
recorded before/after chain.

Original R8 root SHA256: `675c2212ef7a893ef7525c3137848c752bf23837ccabe5e1823b438159ab5882`.
`demo-evidence.json` and `final-verification.json` both record upstream_unchanged=true.
The latter pins the exact sources for the extra final confirmed-B boot, attempt
`770a4e94-276c-44c3-ad70-fb94e6721e77`. `copies.json` records byte-for-byte provenance for original
reports, serial logs, runtime evidence, manifests, the 19/19 Linux test log and
source snapshots. Current code later gained a separate explicit bound-v2
profile; this immutable legacy evidence does not claim ABI-2 support.

Only new regular images under build/update-model-r9-002 were used. No host
mounts, block devices, root access, external NICs or filesystem shares occurred.
Real boot-ROM/bootloader selection, authenticated distribution, secure
anti-rollback, watchdog/reset, physical flash writes and camera power-loss
durability remain unverified. The earlier failed model001 marker-permission
probe is retained under build; the builder now creates a guest-readable marker
and has a real ext4 regression refusing unreadable markers.
