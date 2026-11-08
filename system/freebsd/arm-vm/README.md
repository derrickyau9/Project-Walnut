# DaOS ARM64 FreeBSD virtual target

This target runs actual FreeBSD ARM64 under QEMU TCG on a nonroot Linux host.
The separate amd64 KVM guest is a build host; its test results do not establish
ARM64 execution. This target uses AAVMF UEFI, a private sparse 32 GiB qcow2
overlay, private UEFI variables and a task-only NoCloud seed. No host mount,
filesystem share, host package install or real disk operation is used.

After separately obtaining the official FreeBSD 15.1 ARM64 BASIC-CLOUDINIT UFS
archive and its verified SHA256:

```sh
python3 -m tools.freebsd_arm_vm prepare \
  --archive /home/user/task/downloads/FreeBSD-15.1-RELEASE-arm64-aarch64-BASIC-CLOUDINIT-ufs.qcow2.xz \
  --sha256 VERIFIED_ARCHIVE_SHA256 --work-dir /home/user/task/arm-vm-001
python3 -m tools.freebsd_arm_vm serve --work-dir /home/user/task/arm-vm-001 \
  --boot-timeout 1200 --lifetime 28800
```

The default is four Cortex-A72 virtual CPUs, 4 GiB RAM and multithreaded TCG;
`--cpus 8` is also bounded and supported. There is no KVM acceleration claim.
The existing 64 MiB AAVMF nonsecure code and variables templates are copied into
the private task directory. Code remains read only; UEFI variables may change.
Secure boot has not been validated. The ARM seed keeps the EFI-provided console.
New seeds disable both nuageinit package upgrades and the official image's
separate `firstboot_pkg_upgrade` rc service using an early rc configuration file.

Readiness requires actual remote `FreeBSD`, `arm64` and `aarch64` identities,
root key transport, camera UID1000, disabled password/interactive authentication,
and the guest `/bin/sh` header being ELF64 little endian, AArch64 machine183,
FreeBSD OSABI9. This is system-executable proof; the installed camera executable
and Qt runtime still require their own ARM64 tests. Each boot retains serial,
launch/ready/close JSON and copies/hashes of the three host helper sources.

SSH binds only `127.0.0.1:22242`. Root access uses an ephemeral task key for guest
package/build installation; the camera and Qt application run as UID1000. Guest
egress is enabled during explicit provisioning; a subsequent `serve --restricted`
boot disables it. No external SSH, VNC or graphical listener is created.

```sh
python3 -m tools.freebsd_arm_vm ssh --work-dir /home/user/task/arm-vm-001 \
  --user camera --timeout 120 --command 'uname -a; id'
python3 -m tools.freebsd_arm_vm put --work-dir /home/user/task/arm-vm-001 \
  --source /home/user/task/app.tar --destination /tmp/app.tar --timeout 1200
python3 -m tools.freebsd_arm_vm get --work-dir /home/user/task/arm-vm-001 \
  --source /data/review/first.json --destination /home/user/task/first-arm.json
python3 -m tools.freebsd_arm_vm status --work-dir /home/user/task/arm-vm-001
python3 -m tools.freebsd_arm_vm stop --work-dir /home/user/task/arm-vm-001
```

Transfers have finite time/size limits and compare byte hashes. Host downloads
require new files. There is no automatic capture retry. A failed boot retains its
partial overlay and receipt; inspect it before any deliberate new boot. The
supervisor owns QEMU, a lifecycle flock, serial/boot/lifetime bounds and bounded
shutdown. Immutable base, seed and UEFI code hashes are checked before and after.

Do not distribute private task keys or a bootstrap-authorized overlay. A final
image must remove task keys and disable bootstrap root SSH, then undergo its own
source/build and boot checks. These virtual tests do not establish physical
camera/display operation, signed firmware authenticity, real power-loss safety,
or production readiness.

The first retained ARM boot was launched before the independent rc updater was
identified. Its cloud-config false flags did not stop that separate service, so
the preserved first-boot package upgrade and requested reboot must be disclosed
in its evidence. Do not describe that run as pristine unpatched 15.1 userspace.
