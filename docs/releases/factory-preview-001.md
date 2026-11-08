# Factory preview 001: bilingual FreeBSD image

This is a complete five-partition SD image for the Raspberry Pi 4 and the
800×480 DSI display/touch configuration. It combines the stable FreeBSD kernel,
the bilingual `camera-experience-005` application, the 2 KiB USB transmit fix,
and authenticated early USB startup. The camera backend is **HF-Sim**.

It is a **private developer preview**, not a newly verified physical-board
factory release. The EN/ZH gallery shows the same application sources running
in the desktop simulator. Filesystem checks, static ARM64 dependency checks,
and virtual ARM64 startup are separate evidence; they do not establish physical
DSI, USB enumeration, sensor, lens, or Wi-Fi operation for this rebuilt image.

The image does not include automatic FAT failover enrollment, a hardware
watchdog enable policy, card-bound flashing identity, or the experimental
Wi-Fi/OpenBSD kernels. The separately tested recovery configuration on the
development card is not copied into this generic image.

## Published image identity

The local build output is `DaOS-Factory-Preview-001-arm64.img.xz`; a Release may
rename it while retaining the same bytes and SHA256.

| Item | Value |
| --- | --- |
| Compressed bytes | 359,999,448 |
| Compressed SHA256 | `d3f9272d04c37993eaf2e53a54dd09ce2c4e0b8f8b5bb10174106bd12e249ee6` |
| Expanded image bytes | 3,254,820,864 |
| Expanded SHA256 | `3d094085f22e119586e46f80cff9b992f0c02c6296fa39b99155efc3282e2dd4` |
| Application | `camera-experience-005` |
| Application archive SHA256 | `fa6851185d5fa0e91152fddf997b03f3593ec91f74f2aacc42af79235d28a92b` |

The expanded image contains EFI, root, state, calibration, and photos partitions.
State is the pristine factory scaffold; calibration and photos are empty.
No live-card database, captures, credentials, or private OpenBSD trial overlay
was imported. The original factory partition sizes are retained; automatic
partition growth on larger cards is not supplied by this build.

## Authentication and machine identity

The inherited `/etc/daos/adb_keys` contains **two pre-enrolled developer public
keys**, not their private keys. This is deliberate for the private preview.
There is no new-host pairing or first-boot key enrollment flow. Before wider
distribution, provision the recipient's ADB public key into an offline image
copy, remove inherited authorizations as appropriate, then rebuild its hashes
and QA receipts. Client private keys must stay on the client computer.

Root and camera passwords remain locked; SSH is disabled and no SSH host keys
are included. The USB VID/PID and serial are development identities.

The preview-specific `factory_early_init.sh` mounts a separate public mode-0755
`/DaOS-identity` tmpfs, then executes the unchanged early-USB base hook. `/etc/hostid`
and `/etc/machine-id` point into that public volatile filesystem. The original
FreeBSD `hostid`/`hostid_save` services derive and store the boot identity there.
The USB supervisor's private `/DaOS-recovery-ram` tmpfs remains mode 0700.

## Build inputs: cloning alone is insufficient

The repository contains the derivation scripts and application sources. It
does **not** currently host the historical complete base image or a complete
source-world/curated-package SDK. Do not describe a fresh clone as a reproducible
full firmware build without obtaining these separately recorded inputs.

The required historical base is:

```text
DaOS-BSD-RPi4-DSI43-System-ARM64.img.xz
331771440 bytes
SHA256 9ab7335f1752331270fe9cf451e31085f89063e58460adb5627d88f2f6f3a859
expanded: 3254820864 bytes
expanded SHA256 1788ed68190e78bfe76b1f2ca56d6904883dfada800064312101dae386e160db
```

It is an existing build-workspace artifact, not a URL supplied by this document.
Obtain an authenticated original from the project maintainer, or separately
rebuild and validate the base using the source-world/runtime pipeline described
in [the image builder documentation](../../system/freebsd/image/README.md).
Do not substitute a physical-card backup. The base includes mixed third-party
licenses and source/provenance inventories under `/usr/share/DaOS`; retain them.

The small generic USB input archive additionally requires these two pinned
FreeBSD ARM64 executables:

| Input | SHA256 |
| --- | --- |
| Patched static init | `7a10779068a259de11ea0c0e97c2bdb87cfedfc7729ecd146fe3be9739605427` |
| USB helper | `d6ca440605b3c1718f1144dd8eda6ae2e34f8fd53893c712a7c2dfcdf33fb2aa` |

The init patch is [init-early-usb.patch](../../system/adb/recovery/init-early-usb.patch).
The helper build uses [the native USB source and matching-world instructions](../../system/adb/freebsd_usb/README.md).
Both use the recorded FreeBSD source commit
`95719d8a895bf8170851e9844d9b4092b1d2b6fe`. The stable kernel SHA256 is
`5ac681d051cb623b23a724f91e6ecb818a883f034c8aa88eab678162221f2dec`.
The binary build receipts and hashes are inputs to this preview; these documents
do not claim that an arbitrary compiler produces byte-identical executables.

## Offline derivation workflow

Run the builder in a private native FreeBSD development VM as root. Its only
image devices are autoallocated vnode devices backed by newly created ordinary
files. Do not attach the camera's SD card to this build VM. Use a dedicated
scratch filesystem mounted at `/var/DaOS004-work`; the scripts neither format
nor create that scratch filesystem.

On the source host, create the hash-bound generic USB input archive and the
application archive. Supply the pinned ARM64 binaries explicitly:

```sh
python3 tools/factory_preview_inputs.py --output generic-core.tar.gz \
  --init /trusted/patched-init --helper /trusted/daos-usb-adb
python3 tools/camera_experience_bundle.py --version camera-experience-005 \
  --native-dir /trusted/current-source-arm64-workers --output camera-experience-005.tar.gz
```

The generic archive contains only init, the factory identity wrapper, the
unchanged early hook, supervisor/entry/config, normal-service deferral files,
and the helper. It contains no authentication key, disk identity, FAT selector,
flashing tool, or watchdog policy. Its deterministic archive SHA256 is
`ba984873d9dfa2f7d1af1a929873f90087dbea38b2703fb3e79125e0bdda6335`
for the preview's frozen sources. The historical build used a gzip container
with a different header; its complete decompressed tar is byte-identical.

Copy the verified archives and the current builder, application installer, and
ELF inspector into the private FreeBSD VM. Record their SHA256 values. The
following command pattern uses ordinary scratch paths; replace each `*_SHA`
with the hash of the exact supplied file:

```sh
B=/var/DaOS004-work/factory-source.img.xz
W=/var/DaOS004-work/factory-preview-001
python3 build_factory_preview.py prepare --source "$B" --work "$W"
python3 build_factory_preview.py core --source "$B" --work "$W" \
  --core-inputs generic-core.tar.gz --core-sha CORE_ARCHIVE_SHA
python3 build_factory_preview.py app --source "$B" --work "$W" \
  --app-archive camera-experience-005.tar.gz --app-sha APP_ARCHIVE_SHA \
  --app-installer deploy_camera_app.py --app-installer-sha INSTALLER_SHA \
  --elf-inspector elf_dependencies.py --elf-inspector-sha ELF_INSPECTOR_SHA
python3 build_factory_preview.py finalize --source "$B" --work "$W"
```

Use [build_factory_preview.py](../../system/freebsd/raspberrypi/build_factory_preview.py),
[deploy_camera_app.py](../../system/freebsd/raspberrypi/deploy_camera_app.py), and
[elf_dependencies.py](../../system/freebsd/image/elf_dependencies.py) from the
same source revision. Every work directory and output must be new. A failed
derivation is retained for inspection; never silently treat it as a completed
release or restart a partially installed app in the same version directory.

The builder verifies the original compressed and expanded base, native filesystem
checks on all five partitions, exact installed app hashes, recursive ARM64
DT_NEEDED resolution, init flags, and unchanged bytes outside root. Finalization
fully decompresses the XZ output and verifies its expanded SHA256. Historical
factory input receipts remain historical; `/usr/share/DaOS/factory-preview.json`
and the versioned app manifest identify the current overlay.

For isolated ARM64 startup QA, `qa-final` creates an additional copy with only
virtual-console/single-user loader settings changed. Copy its JSON receipt and
gzip image to a non-root Linux QA host with QEMU/AAVMF already available, then:

```sh
python3 tools/factory_boot_qa.py --inputs /private/qa-inputs \
  --record final-boot-qa.json --out /private/new-qa-output
```

This VM has no NIC or physical USB device. The QA exercises the real rc identity
services, unprivileged machine-id access, authenticated-supervisor startup,
native Qt catalog loading, and synthetic native worker identities. Physical
USB authentication, DSI rendering/touch, and Wi-Fi remain separate tests.

## 中文说明

这是完整的五分区工厂镜像私有预览，包含同源中英文界面、稳定 FreeBSD 内核、
2 KiB USB 修复和早期认证 USB 启动。当前相机后端为 HF-Sim；Wi-Fi 不可用。
新版镜像尚未重新写卡进行实机启动验收，桌面图库、虚拟机启动和实机验收的范围不同。

镜像未复制开发卡上的自动急救注册、硬件看门狗策略、刷写身份或用户照片/数据库。
保留的两个 ADB 开发者公钥需要在扩大分发前换成接收者自己的授权；私钥不进入镜像。
仓库当前不包含历史完整基础镜像，因此不能宣称仅克隆仓库即可重现完整固件。
请保留第三方许可、构建输入摘要和对应版本的验收记录。
