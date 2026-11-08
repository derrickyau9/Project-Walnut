# DaOS Raspberry Pi 4 ARM64 USB development target

This target uses the disclosed FreeBSD 15.1-RELEASE-p6 source lineage, pinned
at commit `95719d8a895bf8170851e9844d9b4092b1d2b6fe`. It combines a source-built
world and splash kernel, matching target SDK, native USB transport/module,
authenticated portable ADB daemon, and the FreeBSD UI/native-camera layers.
It does not rename a previously assembled image or claim an independent kernel.

The shared source lock pins the actual git archive SHA256 and size. Default
configuration verifies the completed base receipt, the archive, upstream source
anchors, staged world/kernel anchors, kernel/world configuration and SDK headers
through the native builder's SSH API. Its result is `reused:true, executed:false`;
the verification command is recorded separately. Source world compilation is
performed only with explicit `--rebuild-base` in a new workspace.

```powershell
python system/bsd-buildroot/daos.py plan
python system/bsd-buildroot/daos.py configure
python system/bsd-buildroot/daos.py build --stage base
python system/bsd-buildroot/daos.py build --stage usb
python system/bsd-buildroot/daos.py packages
python system/bsd-buildroot/daos.py image
```

`all` performs configure, base verification or explicit rebuilding, a real
source USB helper/module build, integration staging and new image assembly.
`plan` creates nothing and makes no SSH connection. NetBSD remains available
with the explicit `--profile rpi4-aarch64-ui` selector and its own workspace.
The default USB host workspace is
`/home/dy09/daos-bsd-buildroot-usb-scratchguard-002`; the earlier
`/home/dy09/daos-bsd-buildroot-usb` is retained as historical validation.

For a full source rebuild use an isolated workspace:

```powershell
python system/bsd-buildroot/daos.py all --rebuild-base --workspace /home/dy09/daos-bsd-full-rebuild-001
```

This copies the existing source build recipe and the private `DaOS` splash
configuration into an owned builder directory, then actually runs buildworld,
buildkernel, installworld, distribution and installkernel. The original recipe,
upstream source tree and prior source base remain untouched. A new derived
receipt binds the configuration actually used by that completed build.

The declared native FreeBSD builder, locked source archive and the profile's
runtime/firmware/project/native inputs must already be provisioned. Firmware and
UI package provenance and target ABI checks are retained by the existing stage
and assembler validators. The backend verifies that the guest execution recipes
and eight daemon files equal the selected local sources. It starts no VM and
does not install packages or a kernel into the builder's live root.

The builder SSH user is explicitly `root` inside the isolated FreeBSD build VM;
its project snapshots are private root-owned inputs. The backend does not start
the VM automatically. If the provisioned VM is stopped, start that existing
builder in a separate WSL terminal before `configure` or `all`:

```sh
python3 tools/freebsd_vm.py serve --work-dir /home/dy09/hf-task-freebsd-r10/vm-001 --memory-mib 16384 --cpus 12
```

The pure plan prints this prerequisite command and the SSH user. Connection
failures also print the command; they never masquerade as completed builds.

The current source world object cache has been pruned. Its installed matching
headers/libraries and the rebuilt splash kernel object directory remain; USB
compilation explicitly uses that SDK as `WORLDTMP` while retaining host tools
on PATH. Cache reuse is not reported as a new toolchain or world build.

The image API accepts an explicit `guest_work_dir` beneath `image_work_root` and
otherwise chooses a fresh name derived from this profile/workspace identity.
The current work root is a separately prepared 12 GiB builder tmpfs. Full base
rebuilding also requires the source builder's configured free-space minimum.
No physical disk is written. Previous runs are retained; failed owned output
directories require a new workspace rather than unreviewed cache adoption.

Before another `all` or `image`, inspect these commands **inside the existing
FreeBSD builder**:

```sh
mount -p
df -h /var/DaOS-pi4-usb-build-memory
```

The exact work-root mount must be listed as `tmpfs`, with enough free scratch
space for the prepared trees, partition files and raw image together. The last
assembly used a 12 GiB tmpfs with a 16 GiB builder VM. Rebooting the builder
removes that mount and its content; an existing directory alone is not proof
that the scratch filesystem is mounted. Do not proceed on the approximately
5.9 GiB remaining root disk. Before invoking the assembler, the backend now
requires this exact tmpfs mount and checks available bytes with `statvfs`.
The conservative requirement is three times total declared partition capacity
plus 32 MiB: 12032 MiB for this profile. Failure creates no image stage or raw
image. The backend does not mount scratch or delete old assembly directories.
Re-establish and verify the scratch setup before assembly. Exported `.img` and
`.img.xz` files on F are persistent and do not depend on this temporary mount.

`backends/freebsd_usb.py` exposes `plan`, `fetch`, `configure`, `build_base`,
`build_usb`, `packages` and `image`. Native stage receipts contain actual argv,
exit status, complete guest/host logs and read-back artifact hashes. Image
assembly verifies the raw image against its new evidence and requires the ADB
overlay; it leaves the image on the native builder for explicit export.

The USB identity uses the temporary, unassigned development VID/PID
`ffff:da05`, serial `DaOS-USB-ADB-DEVELOPMENT`. The device uses stdio transport,
preauthorized RSA2048 public keys and no network listener. `Connect-DaOS-USB.cmd`
uses its separate Windows adb server and only that USB serial.

Reference FreeBSD ARM64 tests have loaded the exact source-built module,
selected template 55813, and passed the official adb shell/PTY/sync/authentication
suite over an SSH-forwarded private loopback endpoint. Actual Raspberry Pi
USB-C enumeration, Windows binding and cable/physical-device behavior remain
hardware validation requirements. The profile does not claim they passed.

The [Chinese USB access guide](../../../adb/README.md) covers the USB-C data
cable, independent Pi4 power, preauthorized public keys and isolated port 5049.
The [release verification](../../../../build/DaOS_BSD_RPi4_USB_ADB_ARM64/VERIFICATION.md)
records the delivered image's actual boot/UI results. For this new coordinator,
actual acceptance so far covers Windows `plan`, native `configure` and verified
`build --stage base` reuse plus 60 host/mock tests. Its `usb`, `packages`,
`image` and `--rebuild-base` execution paths have not been run through the new
coordinator; the delivered image was assembled using the underlying native
recipes separately.

The original target validation remains in `evidence/freebsd-usb-target-001`.
After adding the image scratch gate, configure/base reuse was verified again
in a new workspace, `/home/dy09/daos-bsd-buildroot-usb-scratchguard-002`, without
overwriting the first receipts. The corresponding evidence is
`evidence/freebsd-usb-target-002`. Native read-only scratch verification correctly
refused the currently occupied tmpfs for insufficient space; no old image or
assembly tree was removed.
