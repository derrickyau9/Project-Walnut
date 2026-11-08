# Private FreeBSD development VM

This boots the actual official FreeBSD amd64 UFS virtual-machine image using an
existing nonroot Linux host and KVM. It does not use a Linux userspace to stand in
for FreeBSD. No host mount, package installation, host filesystem share, or real
disk device is used. The official qcow2 stays unchanged; a separate sparse 32 GiB
overlay holds guest packages, builds and `/data`. This is a development VM, not a
production camera image or a physical-camera validation.

Use the release URL and SHA256 recorded by `../release-lock.json`; obtain and
verify that archive separately. The helper does not download another copy.

```sh
python3 -m tools.freebsd_vm prepare \
  --archive /home/user/task/downloads/FreeBSD-15.1-RELEASE-amd64-BASIC-CLOUDINIT-ufs.qcow2.xz \
  --sha256 VERIFIED_ARCHIVE_SHA256 --work-dir /home/user/task/vm-001
python3 -m tools.freebsd_vm serve --work-dir /home/user/task/vm-001 \
  --boot-timeout 240 --lifetime 14400 --memory-mib 12288 --cpus 8
```

The existing defaults remain 16 CPUs and 16384 MiB. `--cpus` accepts integers 1..16;
`--memory-mib` accepts integers 2048..16384. These per-launch settings change QEMU
arguments and launch/ready receipts, without rewriting `vm.json`, the seed, base
image or overlay. The example uses 8 CPUs and 12288 MiB to leave host resources for
other owned tasks.

`prepare` requires a NEW directory beneath an existing canonical parent and at
least 28 GiB free. It compares the archive hash, bounds XZ expansion, checks the
standalone qcow2, creates the sparse overlay, and generates an ephemeral Ed25519
task key. The NoCloud `CIDATA` seed uses FreeBSD's native
[nuageinit(7)](https://man.freebsd.org/cgi/man.cgi?query=nuageinit&sektion=7&manpath=FreeBSD+15.1-RELEASE+and+Ports.quarterly).
The seed disables password and interactive authentication before SSH starts.
`camera` has UID1000; root key access is solely for guest installation and build
administration. The generic seed disables nuageinit's package-upgrade flags.
The official image also enables an independent `firstboot_pkg_upgrade` rc service;
those cloud-config flags do not suppress it. Use the separate ARM runner's early
rc override when that independent service must be disabled before the first boot.

`serve` owns the foreground QEMU child, holds one lifecycle flock, bounds boot
and total lifetime, and retains serial/launch/ready/close receipts under a new
`boot-UUID` directory. Its ready receipt requires real FreeBSD amd64 SSH execution,
camera UID1000 and disabled password authentication. QMP uses a private Unix
socket. SSH forwards only `127.0.0.1:22241`; no display or VNC listener is enabled.
Provisioning enables guest user-network egress. Start a subsequent clean boot
with `--restricted` when egress is unnecessary. Do not run two owners on the same
overlay or port.

Use a second terminal while `serve` remains active:

```sh
python3 -m tools.freebsd_vm ssh --work-dir /home/user/task/vm-001 \
  --user root --timeout 600 --command 'uname -a; freebsd-version -kru'
python3 -m tools.freebsd_vm put --work-dir /home/user/task/vm-001 \
  --source /home/user/task/source.tar --destination /tmp/source.tar
python3 -m tools.freebsd_vm get --work-dir /home/user/task/vm-001 \
  --source /data/review/first.json --destination /home/user/task/first.json
python3 -m tools.freebsd_vm status --work-dir /home/user/task/vm-001
python3 -m tools.freebsd_vm stop --work-dir /home/user/task/vm-001
```

Transfers compare the received bytes' SHA256, have finite time/size bounds, and
never retry automatically. Downloads require a new host file. An explicit upload
may replace its requested guest destination. The SSH helper runs only the supplied
guest command; it never substitutes another shutter or retries capture work.

The temporary private key, known-hosts file, seed and writable overlay live only
in the private Linux task directory. Never bundle that directory, private key or
bootstrap-authorized image as a distributable. A final deployment copy must first
remove task authorized keys, disable bootstrap root SSH, and undergo its own boot
and package/source provenance checks. Loopback transport and matching hashes are
not claims of signed firmware authenticity, hostile same-user isolation, or
production security.

The first retained `vm-001` experiment predated the explicit upgrade suppression:
nuageinit upgraded base packages and requested a clean reboot. The preserved
second boot consequently runs FreeBSD 15.1-RELEASE-p6, not pristine unpatched
15.1 userspace. Its original base qcow2 remained unchanged. This distinction must
remain in evidence describing that run.
