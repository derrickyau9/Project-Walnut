# DaOS BSD Unix: ARM64 source base build

This profile builds a real ARM64 FreeBSD world and a `DaOS` kernel from pinned
15.1 source. Its default target is `arm64`/`aarch64`, including when the actual
FreeBSD build host is amd64. It is separate from earlier Linux/Buildroot work and
does not use a Linux compatibility jail. The existing VM is a build/bootstrap
environment; renaming that VM is not counted as rebuilding the base.

The [original-release ARM64 lock](source-arm64.lock.json) pins the official253039000-byte `src.txz`
and its SHA256 from the release MANIFEST. Official `REVISION` is96841ea08dcf,
matching release tag `release/15.1.0` commit
`96841ea08dcfa84b954a32dc5ae1a26c28966cf4`. The moving `releng/15.1` branch has
later changes; this original release profile does not claim current security
patch status. The source MANIFEST was read over official HTTPS; independent
PGP verification of that manifest is not claimed.

Run inside an actual amd64/arm64 FreeBSD15.1 guest with Python3.12+, its base compiler,
at least12GiB free and enough RAM for12 compiler jobs. The parent workflow
downloads and verifies the source once. This recipe itself never downloads.

```sh
/usr/local/bin/python3.12 -B /path/to/project/system/freebsd/base/build_base.py \
  --source-lock /path/to/project/system/freebsd/base/source-p6-arm64.lock.json \
  --source-archive /path/to/verified/src-p6.txz --architecture arm64 \
  --work-dir /var/daos-builds/daos-base-arm64-p6-001 --jobs 12
```

The [patched ARM64 profile](source-p6-arm64.lock.json) uses commit
`95719d8a895bf8170851e9844d9b4092b1d2b6fe`, release15.1-RELEASE-p6, with its
actual285589524-byte archive and SHA256
`62616e1381734098f3351a0dc97dd153fe78d5cdd74cea41c771c1c6e2e3c9b6`.
It matches the separately bootstrapped build VM's patch level. This pin is not
a promise of future security currency. The complete source archive contains all
architectures; reusing its exact bytes for an ARM64 target does not reuse amd64
output binaries. Original amd64 source locks and earlier build workspaces remain
historical. An amd64 development build requires both `--architecture amd64` and
its matching original lock, separate from the ARM64 product target.

A lock must name its exact release/patch level, target architecture,
40-character commit, archive byte count and SHA256. `source_prefix` remains
`usr/src`. For a locally generated `git archive --prefix=usr/src/ COMMIT` tar
compressed with xz, set `archive_git_commit_comment_required: true` to require
the archive's global PAX commit comment as well. The acquisition step must
independently verify that checkout's official remote and exact commit; a header
comment alone is not authentication. The recipe also checks `newvers.sh` against
the selected release/patch level and copies the exact lock into the work record.

The work directory must be new, explicitly named `daos-base*` or `daos-build*`,
and outside live system trees. A nonroot builder can instead use an owned
`/home/camera/daos-base-001`. The script verifies the actual FreeBSD host/version and refuses
jails before creating anything. It safely extracts only `usr/src` into that
private tree. Source, objects, configuration, stage and bounded logs remain
there; no `/usr/src`, `/usr/obj`, host `/etc` or live root install is used.
Existing work is refused rather than cleaned, overwritten or resumed.

The actual steps are `make -j12 buildworld`, `make -j12 buildkernel`, then
`installworld`, `distribution` and `installkernel`, all using an explicit
`SRCCONF`, `KERNCONFDIR`, and environment `MAKEOBJDIRPREFIX`. The ARM64 profile
sets `TARGET=arm64 TARGET_ARCH=aarch64 KERNCONF=DaOS`; host architecture does not
select or relabel the output. Install stages
always have private `DESTDIR=.../stage` and `NO_ROOT=yes`; `METALOG` records the
intended ownership/modes for subsequent image creation. Preserve and apply
that metadata when making an image, particularly after a nonroot staging run.
The script does not overwrite the running guest's kernel or userland.

The `DaOS` configuration includes the pinned target's `GENERIC`, with a distinct
kernel identifier and no new sensor/display drivers. The [src.conf](src.conf)
omits target toolchain/debug/test/manual/example/32-bit outputs to fit a camera
appliance. It keeps the shell/Unix utilities, rc system, networking/SSH, storage
including UFS/ZFS, devfs, and Capsicum. The build guest's compiler is unchanged;
developer-toolchain/SDK target images require another explicitly tested profile.
Upstream's normal compatible-system-compiler/linker checks remain enabled; no
forced cross-toolchain shortcut or compiler compatibility bypass is used.

`build-state.json` records the pinned source/configuration, actual commands,
stage results/timings and log hashes. Version2 state records actual host and
target separately, plus compiler targets/linker information. Completion requires
the installed kernel, `/bin/sh` and libc to be64-bit little-endian ELF machine183
for ARM64 (62 only for an explicitly selected amd64 development target).
Limits apply to each stage; failures stop
only the owned process group and retain evidence. There is no automatic retry.
A completed source build is not a boot test: disk/bootloader assembly, safe
persistent-data provisioning and booting this newly installed base are separate.

Qt, Python bindings, fonts, the camera authority and DaOS Camera application are
application packages layered above this base. Their installation or screenshots
on the bootstrap VM do not prove that the newly built kernel/world boots.
No physical camera hardware support follows from generic FreeBSD/QEMU success.

ARM64 cross-build and boot validation are **pending actual execution** until the
corresponding build-state and boot evidence are retained. A configuration file,
planned command or source checksum is not reported as a successful build.

Primary references:

- [FreeBSD15.1 source release directory](https://download.freebsd.org/releases/amd64/15.1-RELEASE/)
- [Exact official release commit](https://cgit.freebsd.org/src/commit/?id=96841ea08dcfa84b954a32dc5ae1a26c28966cf4)
- [FreeBSD build(7)](https://man.freebsd.org/cgi/man.cgi?query=build&sektion=7&format=html)
- [FreeBSD src.conf(5)](https://man.freebsd.org/cgi/man.cgi?query=src.conf&sektion=5&format=html)
