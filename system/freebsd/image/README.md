# DaOS BSD Unix appliance image

The default target is **FreeBSD ARM64**, UEFI, native BSD userland and the
project's source-built `DaOS` kernel/world. The optional amd64 target is a
separate development image. Neither target uses Linux compatibility, Linuxfb,
a Linux rootfs or a renamed official VM as its source-built base.

Image003 has actual assembly, filesystem and visible ARM64 GUI boot evidence.
See the [R10 report](../../../docs/testing/round10-freebsd-arm64-verification.md)
for exact identities and the separate capture/restart acceptance. These results
do not validate arbitrary later outputs. Pure guard tests and planned commands
are not an OS boot. The official guest used to collect Qt packages remains a
bootstrap environment, not the delivered source-built base.

## Inputs and boundaries

`collect_runtime.py` runs in an actual FreeBSD guest of the selected target
architecture. It queries installed package names/versions/origins/ABIs,
dependencies, checksums, licenses and declared files; verifies `pkg check -s`;
and copies only declared files selected by an explicit runtime profile under
`/usr/local`. It does
not install packages, execute scripts/triggers, download, copy `/etc`, clone
the bootstrap VM or include its home, SSH keys, runtime cookies or build tree.
Native `FreeBSD:15:aarch64` packages and matching-major `FreeBSD:15:*` noarch
packages are allowed for ARM64; amd64 native packages are rejected. The collected
file hashes are explicit. Independent repository/archive signature verification
is not claimed by this collector; the recorded installed-package repository
and internal checksum metadata are not a substitute for that verification.

Install/verify the required runtime packages in the private ARM64 bootstrap
guest first. Use their **actual installed names** when selecting the closure.
The runtime needs Python3.12/PyQt5, Qt Quick/Controls2, a font package, Xorg,
SCFB, xauth and libinput. `xf86-input-libinput` is separate from xorg-server.
An example selector list, to be checked against `pkg query -a '%n'`, is:

```sh
python3.12 -B collect_runtime.py --architecture arm64 --profile camera-runtime-v1 \
  --package python312 --package py312-qt5-pyqt --package py312-sqlite3 \
  --package qt5-quickcontrols2 --package dejavu \
  --package xorg-server --package xf86-video-scfb \
  --package xf86-input-libinput --package xauth \
  --output /var/DaOS-runtime-arm64-002
```

`camera-runtime-v1` separates the appliance from the compiler/developer SDK.
It omits declared SDK include directories, static archives, documentation/man,
CMake metadata, llvm19 compiler/bin tools and its external versioned wrappers.
Aliases to omitted SDK targets are also recorded and omitted. All shared
libraries remain; Qt QML/plugins/resources, Python runtime files, fonts and
LLVM builtin/JIT resources outside the SDK include directory remain. Named
license/COPYING/NOTICE/copyright files and license trees always remain;
copyright-bearing documentation is conservatively retained by a text scan.
Each omission records its package, declared checksum, byte count, policy and
reason. `--profile full-package-v1` is a separate full package/SDK collection.
`--measure-only` verifies installed packages and reports exact policy counts/
bytes without creating output or copying files. The canceled original001 input
and its source/payload remain unchanged by a002 collection.

Runtime-v2 metadata includes all decisions, loader-hint file hashes, explicit
base-library metadata and a bounded static ELF DT_NEEDED graph. Every retained
ELF dependency must resolve through its declared RPATH/RUNPATH, standard BSD
library directories or recorded package ldconfig hints; arbitrary basename
matches are not accepted. Assembly repeats that check against the **actual
source-built staged world**, including the packaged native camera, and stores
the result plus complete curation manifest under `/usr/share/DaOS`. This static
check is not proof of dlopen-only modules, all symbol/ABI compatibility or a
successful target boot. Actual ARM UI and custom-image boot remain required.

The output must be new. A partial failed collection is preserved and is not
usable without `runtime.json`. Transfer that exact directory, preserving
symlink entries, to the private FreeBSD build guest if assembly runs there.
An amd64 FreeBSD builder can assemble an ARM64 image, but it cannot supply an
amd64 runtime in place of ARM64 collection.

`assemble_image.py` requires a completed `DaOS-freebsd-base-build-v2` record
from `system/freebsd/base/build_base.py`, its private staged world and METALOG,
the collected runtime, current project sources, and a target-native camera
record from `tools/freebsd_build.py`, or the genuine ARM64 cross-build plus
`DaOS-freebsd-arm64-target-validation-v1` receipt from
`tools/freebsd_target_validate.py`. Evidence must identify actual
target FreeBSD execution, the four expected CTest targets, a successful Python
run, source hashes and the exact camera ELF. Cross-building on amd64 and then
executing those tests on actual ARM64 FreeBSD is supported; compiling on ARM64
TCG is not required. A cross-compilation alone does not satisfy execution
validation. Kernel, shell and camera require matching
FreeBSD ELF64; ARM64 loader.efi must be PE machine0xaa64.

```sh
python3.12 -B assemble_image.py plan --architecture arm64 \
  --base-state /var/DaOS-builds/DaOS-base-arm64-001/build-state.json \
  --runtime /var/DaOS-runtime-arm64-002 --project /path/to/current/project \
  --native-command /path/to/tested/arm64/hf-camerad \
  --native-evidence /path/to/tested/arm64/freebsd-build-evidence.json \
  --work-dir /var/DaOS-image-arm64-001 --root-mib 2560 --compress
```

After reviewing the plan, use the same options with `build` instead of `plan`.
`plan` writes nothing. `build` refuses an existing or redirected work path and
never removes previous results. It runs only staged password-database creation,
makefs, mkimg and optional xz inside the native FreeBSD guest. There is no pkg
installation, chroot, gpart, mdconfig or image mount. The existing source stage
is copied and remains unmodified. Source inode aliases such as `/rescue/*` are
preserved only **within the new staging tree**: the first inode is copied, then
its staged aliases link to that new copy, never to the input base. Intended
ownership/mode/flags must agree across aliases. Image budgets count unique
inodes per final filesystem. Every overlay replacement unlinks its staged name
before writing/chmod, so aliases—including any account-file aliases—retain
their original bytes. Native makefs testing separately verifies that positional
mtree input retains these staged hardlinks in UFS.

### Separate source metadata and offline verification

For an existing transported payload, source metadata can be exported without
repeating its copy or CPU-heavy byte hashing on an emulated ARM CPU:

```sh
# Run on the actual FreeBSD ARM source with the same requested package seeds.
python3.12 -B export_runtime_metadata.py --architecture arm64 \
  --profile camera-runtime-v1 --package python312 --package other-installed-seed \
  --payload /var/DaOS-runtime-arm64-r10-003/payload \
  --output /var/DaOS-runtime-metadata-arm64-r10-004

# Run against its exact payload transport on the faster FreeBSD build host.
python3.12 -B verify_runtime_offline.py \
  --provisional /path/to/source/provisional.json \
  --payload /var/DaOS-runtime-payload-r10-003/payload \
  --output /var/DaOS-runtime-offline-r10-004.json \
  --runtime-output /var/DaOS-runtime-arm64-r10-004
```

Replace package seeds with the actual runtime selection; the names shown are
command-shape examples. The exporter performs a fresh source `pkg check -s`
over the selected closure and records declared checksums, package metadata,
profile, literal links, loader hints and the ARM base-library manifest. Its
`provisional.json` explicitly has pending payload verification and is never a
completed runtime. It uses bounded selected-package queries, not a whole-base
file query, and disables core dumps in child tools.

The offline verifier freshly matches **every retained regular file** to its
declared pkg checksum, computes SHA256, validates the exact tree/links/modes,
and checks ARM ELF dependencies against the recorded source base. The confirmed
pkg2.8.4 `2$` format is BLAKE2b-512 with pkg's LSB-first z-base32, verified against
official upstream vectors and two actual ARM-installed Python entry points.
Unverified metadata, mismatched bytes or unresolved dependencies cannot produce
a completed runtime. Its new runtime directory uses immutable hardlinks on the
same filesystem; it never writes/chmods source files or promotes a failed run.

`host`/`source_host` retain the actual ARM source identity, while
`verification_host` records the actual machine that ran static verification.
The image payload and image receipt retain this distinction and bind the source
provisional hash and source/verifier module hashes. Offline validation executes
no target binaries; actual ARM native tests, visible boot and hardware tests are
separate evidence. Future full collections also fsync `payload-checkpoint.json`
and then `closure-inputs.json` before the fallible closure phase, preserving
already-computed hashes without calling an incomplete run successful.

If a collection copied its payload but failed later, a **new** collection may
use `collect_runtime.py ... --output /var/DaOS-runtime-arm64-003
--reuse-payload /var/DaOS-runtime-arm64-002/payload`. This does not promote the
failed run. It repeats installed-package checks and fresh profile generation,
requires the exact same file/link set in an owned private payload, hashes every
installed and prior regular file, and validates sizes, modes, identities and
literal symlinks. It then creates hardlinks only in the new payload, without
chmod/chown/writing either inode. Both payloads must be treated as immutable;
link counts change, while bytes, modes and modification times do not. Different
filesystems, changed inputs and extra/missing entries fail; there is no silent
recopy fallback. A fresh base and ELF-closure check must complete before the new
runtime receipt is written. Its `resumed_from` explicitly disclaims reliance on
the prior run. The base dependency manifest excludes the known separate-debug
tree `/usr/lib/debug` and `.debug` symbol files; genuine loader objects retain
strict ELF architecture validation.

The default logical sizes total about2GiB: root1536MiB, var128MiB,
calibration32MiB, photos256MiB and EFI64MiB. The measured full package closure
was about3.1GB, dominated by LLVM SDK files. The curated camera profile is
expected to fit `--root-mib 2560` with the source world; actual measurements and
preflight determine this, rather than the expectation being a promise.
Actual file/metadata headroom is checked; the builder
fails rather than deleting package files to force a fit. UFS images are sparse.
`--compress` additionally writes `.img.xz`, records compressed and raw sizes/
hashes and retains the raw image. A transfer helper must bound decompression
by the recorded raw length and verify its raw SHA256 before booting.

## Ownership, layout and boot

METALOG is parsed as intended ownership, rather than replacing root/wheel with
the UID that performed `NO_ROOT` staging. Package and project overlays have
explicit metadata. A unique, parent-before-child mtree is the **positional**
makefs input; `-F` is not used as if it were manifest mode. Manifest descendants
through links and redirected copy parents are rejected. All image outputs are
new regular files; `makefs -Z` and mkimg preserve sparse output where supported.

| GPT payload | Mount | Purpose |
| --- | --- | --- |
| EFI FAT32 | firmware | Source-stage loader.efi at `EFI/BOOT/BOOTAA64.EFI`; amd64 development uses BOOTX64.EFI. |
| UFS2 `DaOS-root` | `/`, read-only | Source-built kernel/world, package-file runtime, application and defaults. |
| UFS2 `DaOS-state` | `/var` | Persistent app configuration/journal/UI state/cache/home/logs. |
| UFS2 `DaOS-calibration` | `/var/DaOS/calibration` | Independent persistent calibration area. |
| UFS2 `DaOS-photos` | `/var/DaOS/camera/photos` | Independent photo storage, mounted before authority startup. |
| tmpfs | `/tmp` | Transient X socket and temporary files. |

The kernel selects `ufs:/dev/gpt/DaOS-root`; rc does not remount root writable.
The source-built UEFI loader is used, not an amd64 gptboot in an ARM image.
The ESP uses explicit512-byte sectors, one sector/cluster,32 reserved sectors
and two FATs. The table size includes the two reserved FAT entries:
`ceil((total_sectors - 32 + 2) / (128 + 2))`; the64MiB profile requires1009
sectors per FAT. Merely selecting FAT32 at64MiB allowed the native formatter
to reject its default cluster geometry while returning zero; selecting cluster1
without the table override produced an undersized FAT. Both outcomes were
rejected during actual independent validation and preserved as failed evidence.

Before mkimg or compression, the builder checks FAT32 BPB/volume/table capacity,
reserved-sector bounds, backup boot sector and FSInfo signatures, then runs
full read-only `/sbin/fsck_msdosfs -M -n` and all four `/sbin/fsck_ffs -n` checks.
It records checker exit status, logs and before/after image hashes. Any nonzero
status, crash, changed bytes or structural failure aborts before GPT assembly;
format command returncode and file length alone never establish success.
`-M` is the documented non-mmap I/O path and retains the native checker phases.
The corrected64MiB/1009-sector candidate separately passed native default and
non-mmap fsck as well as independent Linux fsck.fat. This is filesystem-format
evidence, not UEFI boot or physical device validation.
No network interface is configured, sshd is disabled, root/camera passwords are
locked, and the camera account has UID/GID1000. No shared host keys, authorized
keys or entropy seed is copied into the pristine image. Package metadata and
licenses remain in `/usr/share/DaOS/payload.json` and the copied package license
files. Project license status/inventory and provenance, the actual nlohmann MIT
text and the unmodified first SQLite upstream header comment are additionally
stored under `/usr/share/DaOS/notices`, with source hashes. These notices do not
grant the whole image a BSD license or imply existing DaOS code was imported.
The curated runtime is not registered as an on-device pkg installation;
live pkg upgrades and source rebuilding on the appliance are not supplied.

The virtual-target loader loads the source-built `virtio_random` module. The
isolated image runner supplies fresh host CSPRNG bytes through a virtual RNG;
it does not bake a shared seed into the image. Actual device attachment and
first-boot operation remain target observations. A physical board needs its
own verified entropy source rather than assuming this QEMU device exists.

## Normal visible camera session

The rc.d service starts a root supervisor and the real Xorg binary on reserved
`vt9` (`ttyv8` getty disabled). SCFB reads the firmware framebuffer geometry;
there is no Linuxfb path or forced Xorg modeline. Root Xorg is needed for this
first console/SCFB profile; the Qt application and its native camera child run
as UID/GID1000. No broad permissions are applied to console or memory devices.

Each service start creates a new128-bit MIT-MAGIC-COOKIE through xauth's stdin.
The root-owned authority is group-readable only by camera, with a separate
camera-owned0700 XDG runtime directory. It is never placed in the image or
printed in logs. Xorg has `-nolisten tcp`. Qt uses the actual xcb display,
software Quick renderer and `--kiosk`, not offscreen for the normal service.
The supervisor does not restart or resubmit captures automatically.

TERM reaches an unprivileged entry wrapper, which quits Qt through its normal
finally/shutdown path. The supervisor waits15s for the application, then stops
Xorg, removes its generated authority file and records any forced termination.
`DaOS_GUI_EVENT` records go to the service log and best-effort `/dev/console`:
base identity/hashes, Xorg started, camera process started and session stopped.
A start marker does **not** establish Qt readiness or a usable framebuffer.

ARM64 normal-display acceptance still requires an actual AAVMF/QEMU GOP device,
`vt_efifb`, successful SCFB dimensions, working input, Qt/xcb, UID1000 app/native
processes, authenticated X access and no TCP6000 listener. Whole-guest QMP
screenshots must show the kiosk UI with its truthful FreeBSD/architecture footer.
An offscreen QML content grab is separate evidence and cannot satisfy this
normal visible boot gate. Source-built `kern.ident=DaOS`, kernel/shell hashes,
storage mounts, clean service stop and restart/same-capture behavior must also
be verified on the assembled image. Arbitrary power-cut/media behavior, real
camera hardware and physical display support are not established here.

Primary implementation references:

- [Pinned FreeBSD release image tools](https://raw.githubusercontent.com/freebsd/freebsd-src/96841ea08dcfa84b954a32dc5ae1a26c28966cf4/release/tools/vmimage.subr)
- [makefs manifest semantics](https://raw.githubusercontent.com/freebsd/freebsd-src/96841ea08dcfa84b954a32dc5ae1a26c28966cf4/usr.sbin/makefs/makefs.8)
- [Pinned FAT32 format limits](https://raw.githubusercontent.com/freebsd/freebsd-src/95719d8a895bf8170851e9844d9b4092b1d2b6fe/sbin/newfs_msdos/mkfs_msdos.c)
- [Pinned native FAT checker I/O options](https://raw.githubusercontent.com/freebsd/freebsd-src/95719d8a895bf8170851e9844d9b4092b1d2b6fe/sbin/fsck_msdosfs/main.c)
- [mkimg partition syntax](https://raw.githubusercontent.com/freebsd/freebsd-src/96841ea08dcfa84b954a32dc5ae1a26c28966cf4/usr.bin/mkimg/mkimg.c)
- [FreeBSD SCFB configuration](https://docs.freebsd.org/en/books/handbook/x11/)
- [Minimal X input dependencies](https://raw.githubusercontent.com/freebsd/freebsd-ports/main/x11/xorg-minimal/Makefile)
- [pkg file/dependency metadata](https://raw.githubusercontent.com/freebsd/pkg/master/docs/pkg-query.8)
- [daemon supervision](https://raw.githubusercontent.com/freebsd/freebsd-src/96841ea08dcfa84b954a32dc5ae1a26c28966cf4/usr.sbin/daemon/daemon.8)
