# Pinned ARM64 Python and Qt userland

This profile stages an ARM64 Debian trixie Python/Qt5 software runtime for generic QEMU Linux guests. It does not install packages on the host, execute package maintainer scripts, register binfmt handlers, mount filesystems, or configure services. It supplies no camera hardware driver, board support package, display configuration, or physical-device evidence. The R6 static initramfs and its evidence remain separate.

The pinned closure contains 170 `arm64`/`all` packages and 618 selected dependency edges: 106,458,804 archive bytes and 457,305 KiB of declared Installed-Size. Runtime versions actually executed under explicit QEMU user emulation are Python 3.13.5, PyQt 5.15.11 and Qt 5.15.15. This Debian profile differs from the Windows PyQt 5.15.10 profile.

`package-lock.json` pins every selected version, archive URL, size and SHA256, along with dependency choices. `metadata/InRelease` and `metadata/Packages.xz` preserve the exact official signed index needed to reproduce those choices. The script verifies the release using `gpgv` and the installed Debian archive public keyring, checks the requested codename, architecture and component, and verifies the compressed index against the signed SHA256. Fresh unpinned acquisition rejects declared expiry and releases older than 370 days; explicit pinned replay permits historical metadata after verifying the same pinned signatures and hashes. A current public keyring capable of verifying those signatures is required. Preserved metadata avoids depending on the retention of an old moving-mirror index. Package archives are fetched only as hash-checked data and are not included in the source archive.

The resolver follows every selected package's declared Depends and Pre-Depends, including versioned virtual providers and deterministic alternatives. It checks supported conflicts and fails on unsupported dependency grammar. Recommends and implicit Debian Essential packages are excluded. This is an extracted runtime profile, not a general Debian installation or an apt replacement. Merged `/usr` aliases are created explicitly; package configuration scripts, triggers, alternatives generation and cache generation are not run. The actual import and guest UI tests establish the narrower runtime sufficiency.

Use a non-root Linux host with Python 3.13, `gpgv`, the Debian archive keyring, `dpkg`, and `dpkg-deb`. No root privileges or package installation are needed. The closure resolver uses `dpkg --compare-versions`; staging uses only `dpkg-deb --fsys-tarfile`. QEMU user emulation is an optional, separate probe dependency.

From the repository root:

```sh
python3 -m tools.rootfs_build prepare \
  --profile system/arm64-rootfs/debian-trixie-qt5.json \
  --lock system/arm64-rootfs/package-lock.json \
  --inputs-dir build/rootfs-inputs

python3 -m tools.rootfs_build stage \
  --lock build/rootfs-inputs/package-lock.json \
  --inputs-dir build/rootfs-inputs \
  --output /home/your-user/hf-rootfs-stage

python3 -m tools.rootfs_build validate \
  --rootfs /home/your-user/hf-rootfs-stage/rootfs \
  --manifest /home/your-user/hf-rootfs-stage/stage-manifest.json
```

The input cache must be new or carry this tool's marker. Add `--offline` to `prepare` to require all 170 archives already cached; pinned signed metadata can be seeded from the preserved source files without network access. Existing package locks and staging outputs are never overwritten. Stage output must be separate from the input cache; output within this repository is permitted only under `build/`. Prefer a persistent Linux filesystem for symlink and mode semantics, such as a new task directory under the current user's Linux home. In the observed WSL environment `/tmp` is tmpfs and trees disappeared between separate invocations. The unified recipe therefore rejects tmpfs/ramfs work directories; the exact cause of the observed WSL lifecycle change was not established.

The extractor rejects absolute/traversing member paths and special device entries, confines parent symlinks to the guest tree, converts absolute guest symlinks to equivalent relative links, strips setuid/setgid bits, and rejects conflicting package file contents. Archive bytes are bounded, individual expanded files are limited to 512 MiB, and aggregate expanded regular-file data to 3 GiB. Only data archive contents are read; `DEBIAN` control scripts are never extracted or invoked. Hardlink contents, modes, all file hashes and all relative symlink targets enter the deterministic tree inventory. Ownership and timestamps are deliberately outside that tree contract; the image builder normalizes those separately.

`stage-manifest.json`, format `hf-arm64-qt-rootfs-v1`, records the lock hash, verified acquisition metadata, selected packages, transformations, every relative entry and the canonical tree digest. `verify_stage(rootfs, manifest)` checks the full inventory, modes, link containment and code inventory before image consumption. Each ELF file must be little-endian ELF64 AArch64 (machine 183); dynamic ELF files are expected here. Python sources under `usr/lib/python3*` are compiled in memory without execution or bytecode writes. The manifest is an integrity report, not a cryptographic signature of an arbitrary third-party tree: obtain it from the authenticated staging recipe or replay that recipe against the pinned inputs.

The optional executable probe uses a clean explicit-QEMU environment and verifies the actual Python executable and Qt extension ELF identities and paths, imports QtCore/Gui/Qml/Quick/Test, creates a QML component importing Controls/Layouts/Window, and checks the staged tree remains unchanged:

```sh
python3 -m tools.rootfs_probe \
  --rootfs /home/your-user/hf-rootfs-stage/rootfs \
  --manifest /home/your-user/hf-rootfs-stage/stage-manifest.json \
  --output build/rootfs-probe.json
python3 -m unittest tests.test_rootfs_deps tests.test_rootfs_build -v
```

QEMU user mode is emulation and is not a chroot or security boundary. The probe sets `PYTHONHOME`, the Debian dist-packages path, Qt plugin/QML paths and a temporary cache/runtime directory explicitly. It does not use host Python as evidence for ARM Python. Full guest image and first/resume UI evidence belong to `system/arm64-image` and the UI report; the base rootfs contains no application overlay or PID1.

Preserved results are under `evidence/`: two fresh final stages have identical manifests and tree digest `c7e360551ef0f39fa453fd4c7c4cf99854656dc517744545ca61dc5631823295`, containing 5,765 entries, 562 AArch64 ELF files and 627 checked Python sources. Linux rootfs/dependency tests passed 24/24 with zero skips. Debian package license notices remain in each extracted package's `usr/share/doc` content and the lock identifies their source packages; this profile does not grant rights or resolve the project's pending rights status.
