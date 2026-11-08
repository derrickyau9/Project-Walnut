# R9 bound-data A/B rollback: actual execution

The three-boot bound-v2 integration passed on actual full-system virtual ARM64.
A healthy confirmed A boot was followed by an explicitly unhealthy B trial with
a one-attempt budget, then the actual still-confirmed A fallback. The unhealthy
trial could not be confirmed. Current state has confirmed=A, no pending trial,
and no remaining budget.

| Actual selected slot | Model version | Healthy | Trial budget remaining | Attempt UUID |
| --- | --- | --- | --- | --- |
| A | 1 | true | 0 | fa5ed16d-2898-46a2-b50d-16280edf47a3 |
| B | 2 | false | 0 | f17e503a-c7d6-4522-9e6a-4be2801e8e3b |
| A | 1 | true | 0 | 4363fb20-79ba-48c2-9cae-746118ee7847 |

The tested source image is `build/target-9fe924461465880cc2e67da2`, the parent
main R9 target001 image before later Python SDK/reference hardening. Its native
C++ and ABI-2 implementation were already frozen. This report is scoped to that
exact image revision; it is not evidence of a later source snapshot. The model
uses unchanged R9 application code with artificial on-root release versions.

All guests ran ARM64 Python and native code as UID 1000, required capabilities
API 1.1 and support for capture records 1 and 2, and queried the existing
`/data/ui-r7/offline/basic` authority without acquiring control or
submitting a capture. The original request/capture/frame UUID remained
`36749d78-afb4-4115-8be3-04758d23b420`, with exactly one unchanged complete version-2 row.
Its DNG SHA256 stayed `d0182035edeaf0637284c5a637fd625ff5d6c66b0484c22a2811ed8bd0432cb8`.

The actual persistent exposure stayed 11700 us.
The actual full pinned binding stayed `cb1-a4d0d90ce30689a7202a88b28945bb6bca37fba91745b99323687213d2d5be17`.
The host pinned copied read-only journal settings/binding metadata and the
original binding artifact against the tested offline report. Each guest compared
its real get_state settings/calibration descriptor and full queried capture
binding against that baseline before and after query-only health checks.
Hardware calibration and pixel corrections remain false. The bound selection is
real persistent software state, not measured physical calibration.

Six protected hashes were identical across all three boots: actual device
settings, full capture binding, original DNG, logical capture rows, and the two
separately labeled configuration/calibration preservation fixtures. The fixtures
are not substituted for actual state checks. All root images stayed unchanged;
all data filesystems unmounted cleanly and passed read-only fsck. Data-image
hashes form a contiguous before/after chain; normal epochs/reports changed while
protected capture/settings/binding data stayed identical. Native children closed.

`bound-demo-evidence.json` records upstream_unchanged=true for original root,
kernel, bootstrap and data images. Their root SHA256 was
`1400b9bc29c063597460491f54ee2631b13a1e86ec8a44e98a1c7619dd499d98`. Copies and exact executed source
snapshots are mapped by SHA256 in `copies.json`; the original 29/29 focused Linux
test log is under `tests/`. It includes a real ext4/SQLite source-profile fixture,
legacy-ABI refusal, stale/mismatched receipt rejection, incompatible native-hash
refusal, committed attempt consumption across actual process exit, exclusive VM
locking and static ARM guest-init host refusal.

The original report's inherited informational initialized.baseline wording says
R8. Its source policy, native hash, full binding evidence and profile establish
R9; `copies.json` annotates this without rewriting any original report bytes.
Future coordinator output now says verified base image.

No original image, existing image/target helper, native camera implementation,
physical device or host mount was changed by this work. The coordinator is an
unsigned local consistency model: hashes are not authentication. It cannot prove
boot-ROM/bootloader selection, secure anti-rollback, update distribution trust,
physical partition writes, watchdog/reset behavior or camera power-loss safety.
Different application binaries are deliberately rejected at staging even if
they merely claim ABI 2. Those physical and cross-version compatibility gaps
remain explicit.
