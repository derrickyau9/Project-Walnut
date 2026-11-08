# Executable regular-image A/B update model

This Linux user-space model boots actual selected ARM64 root images on QEMU
`virt`. It exercises inactive-slot validation, durable trial consumption,
explicit health confirmation and fallback while keeping one shared data image.
It is a development model, not U-Boot, RAUC, signed firmware distribution or a
physical camera updater. The demonstration uses the **same R8 application code**
with different artificial on-root release markers and an explicit unhealthy
candidate. It does not rebuild or change the original R8 image or camera code.

Run as a non-root Linux user with the existing QEMU, ext4 tools and static
AArch64 C compiler. No packages are installed. All output must be in a **new**
directory beneath this workspace's `build/`; no host image mounts, block device
access, flash writes, external NICs or host filesystem sharing occur.

```sh
python3 -m tools.update_model demo \
  --base-build build/target-a46f8569e5d9146d26f42a6c \
  --state-dir build/my-update-model --timeout 120
```

The base must have successful original `first` and `resume` image evidence,
one matching retained capture, unchanged root/kernel/bootstrap hashes and the
recorded final data-image hash. The model copies those regular images. It adds
a guest-readable `/etc/hf-update-release.json` only to its new root clones.
SHA256 and compatibility fields detect mismatch and corruption; they do not
authenticate an untrusted package. The supported compatibility envelope is
generic QEMU ARM64, an explicitly selected data profile, native RPC ABI 1 and
the exact verified base kernel/module runtime and native binary. Kernel
replacement, mixed application versions and data migration are unsupported.
The default `legacy-v1` profile is data ABI 1; it refuses to label a bound
version-2 capture as legacy data.

The demo performs five real full-system boots:

| Selected slot | Model version | Health and control result |
| --- | --- | --- |
| A | 1 | Baseline health, then explicit confirmation |
| B | 2 | Validated inactive trial, then explicit confirmation |
| A | 3 | Intentional unhealthy result; one attempt remains |
| A | 3 | Intentional unhealthy result; no attempts remain |
| B | 2 | Still-confirmed fallback, then explicit confirmation |

Every guest runs normal ARM64 Python as UID 1000. Its health entrypoint checks
the actual on-root version marker and native binary hash, starts that root's
native authority, and queries the original R8 capture UUID. It never acquires
control, presses a shutter, migrates a journal or retakes a frame. It verifies
the same DNG bytes and exactly one unchanged complete capture row. Startup
epochs and filesystem bookkeeping may change, so whole data-image hashes are
chained separately from the protected logical capture-row hash.

`/data/update-model-persist/config.json` and `calibration.json` are explicitly
labeled **preservation fixtures**. They are created only during the initial
baseline health boot, then must remain byte-identical. They are not measured
calibration and do not replace the separately implemented calibration/settings
binding APIs. Missing or modified fixtures are refused, never silently repaired.

## Bound settings and calibration profile

The separate `bound-v2` profile uses data ABI 2 and defaults to
`/data/ui-r7/offline/basic` in a tested R9 image. Initialization reads copied,
read-only journal metadata and the pinned `binding.json` artifact, and checks
them against `guest_report.offline_camera.basic` in the original resume report.
The full validated binding and persistent device settings are included in the
new bootstrap's hashed health profile. Existing legacy model images, runtime
and evidence are left unchanged; their result is not ABI-2 evidence.

The guest requires capabilities API 1.1 and capture record versions 1 and 2.
It verifies the actual version-2 capture, its complete immutable binding, false
hardware-calibration/pixel-correction flags, and persistent state settings and
calibration descriptor against that pinned baseline. Hashes of the actual device
settings and full capture binding join the fixture, DNG and capture-row hashes.
No settings or bindings are activated or modified by update health checks.

```sh
python3 -m tools.update_model bound-demo --base-build TESTED_R9_IMAGE_DIR \
  --state-dir build/my-bound-update --timeout 120
# Or initialize for individual stage/boot/confirm commands:
python3 -m tools.update_model init --base-build TESTED_R9_IMAGE_DIR \
  --state-dir build/my-bound-model --data-profile bound-v2 \
  --capture-root ui-r7/offline/basic
```

`bound-demo` performs three actual boots: healthy confirmed A, an intentionally
unhealthy B with a one-attempt trial budget after real binding/settings checks,
and the still-confirmed A fallback. A different native binary cannot become
compatible merely by claiming ABI 2: the model also pins the exact native and
source-build hashes and rejects such a candidate before touching shared data.
This model does not prove compatibility between different application releases.

## Durable coordinator and manual commands

`control.sqlite3` uses WAL and `synchronous=FULL`. One process lock covers the
entire VM lifecycle, including image validation and health recording. Slot A/B
are logical pointers to immutable owned bundle directories; staging changes only
the inactive pointer. No confirmed image file is overwritten. Each selection
commits a fresh UUID, selected manifest hash and decremented trial budget before
launching QEMU. A lost coordinator leaves a consumed, unconfirmed attempt.
Reopening never confirms it automatically; stale and unhealthy confirmations
are rejected. Invalid inactive images fall back to a verified confirmed slot.
An invalid confirmed image or bootstrap fails closed.

```sh
python3 -m tools.update_model init --base-build PATH --state-dir build/my-model
python3 -m tools.update_model boot --state-dir build/my-model --timeout 120
python3 -m tools.update_model confirm --state-dir build/my-model --attempt-id UUID
python3 -m tools.update_model bundle --state-dir build/my-model --version 2
python3 -m tools.update_model stage --state-dir build/my-model --bundle PRINTED_PATH --attempts 2
python3 -m tools.update_model boot --state-dir build/my-model --timeout 120
python3 -m tools.update_model confirm --state-dir build/my-model --attempt-id NEW_UUID
python3 -m tools.update_model status --state-dir build/my-model
```

Confirmation consumes stored, matching host-verified guest evidence; the CLI
does not accept a caller-supplied health JSON. This is a consistency boundary,
not an authentication boundary against an owner who can rewrite both images and
control files. Actual bootstrap bytes are checked before any boot and before
confirmation. Each slot is validated by size/hash, target/ABI, clean read-only
filesystem inspection, its readable release marker and actual AArch64 native
binary hash. QEMU receives pinned regular file descriptors; root images are
read-only, and `/data` is attached separately read-write.

Trial budgets are 1–3. This deliberately bounded model retains at most four
candidate bundles and 32 boot attempts; it does not automatically delete old
evidence. A timed-out or interrupted guest is stopped, its attempt and partial
data are retained, and a new boot requires clean read-only filesystem checks.
There is no automatic repair or replay of the same attempt. Guest serial output
and metadata reads are bounded; QEMU execution has a finite 1–600 second budget.

`demo-evidence.json` records the sequence and final confirmed slot. Each
`attempts/<UUID>/` holds `serial.log`, the extracted guest report and
`boot-evidence.json`, including selected image hashes, clean unmount/fsck,
protected data hashes and the matching nonce. The original R8 image hashes are
checked again at the end. `runtime/runtime-evidence.json` records the new static
guest init, health source and verified module inputs. The existing image and
target helpers are reused without edits.

## Physical gaps

This model does not implement or verify boot-ROM behavior, bootloader A/B
environment storage, signed secure boot, authenticated update distribution,
secure anti-rollback, real partition writes, watchdog/reset behavior, or physical
storage power-loss durability. Explicit unhealthy results are injected after
real application checks; they are not a hardware failure test. SQLite/fsync and
process-exit tests cannot establish safety under a real camera power cut.
Physical board, sensor, lens and display support remain separate unverified
requirements.
