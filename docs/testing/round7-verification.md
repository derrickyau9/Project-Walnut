# Round-seven ARM64 image and UI verification

Date: 2026-11-05. R7 extends the preserved R6 source snapshot with an executable
generic ARM64 Linux disk-image recipe, ordinary ARM Python/Qt/QML and the native
synthetic camera authority. It supplies no physical camera driver or board BSP.

## Actual execution

The final unified recipe completed a fresh native cross-build, fresh pinned
kernel acquisition, runtime-cache authentication, fresh POSIX rootfs staging,
new image generation, first boot and resume boot. Only runtime package archives
were reused via `--inputs-dir`; native, kernel, staging and image outputs were
new. A separate completed offline run reused verified native/kernel/package
inputs but created its own rootfs and images. See the
[unified instructions](../../system/targets/README.md).

| Final check | Actual result |
| --- | --- |
| Linux rootfs/image/coordinator/ARM guard tests | 57 passed, zero skips, 5.348 s |
| Source handoff checks | 15 passed on Windows and Debian Linux, zero skips; original combined log in target evidence |
| Fresh AArch64 native programs | 89 core + 82 authority assertions, 20 RPC tests and 4 ELF/tool tests passed |
| Runtime closure | 170 official ARM64/all packages; 618 edges; 106,458,804 archive bytes |
| Actual guest runtime | ARM Python 3.13.5, PyQt 5.15.11, Qt 5.15.15; UID/GID 1000 |
| First/resume guest boots | 14.550 s / 14.227 s; both passed |
| Capture persistence | Same UUID/frame, one completed journal row; resume invokes no shutter |
| DNG/export | 25,662 identical bytes across both boots and exports |
| Independent DNG decoding | tifffile 2023.4.12 and rawpy 0.28.0/LibRaw 0.22.2 each matched all 12,288 samples to the Python oracle; RGB 96x128x3 rendered |
| Repeated root-image builds | Four independent same-input builds produced identical SHA256 |

Final request: `791f049f-2125-42b1-9796-9699c93d390b`. Its DNG SHA256:
`1f4cd58b54d9d3a5241409904075eeaa88e004fe2363ea4eda197532ce8fb8d6`.
This is synthetic 128x96 Bayer at an actual simulated exposure of 10,100 us,
not a full-frame sensor measurement or throughput benchmark.

The root-image SHA256, unchanged through both boots and matching three earlier
constructions, is
`40acc21e909b4ab326ec0f8c1390b2662d5e8e7b2c372c9b0aac967fba04a777`.
Base tree digest:
`c7e360551ef0f39fa453fd4c7c4cf99854656dc517744545ca61dc5631823295`;
5,765 entries, 562 ARM64 ELFs and 627 syntax-checked Python sources. Two fresh
base stages also had identical manifests. This is same-input/recorded-tool
repeatability, not hermetic cross-host proof. Debian/Qt binary packages were
authenticated and extracted, not rebuilt from upstream sources.

## Verification boundaries and fixes

The root disk is read-only; separate private ext4 data persists through guest
shutdown/reboot. Actual ARM QML controls change exposure, capture through the
native authority, review a thumbnail decoded from the committed DNG and export
original bytes. ELF machine 183 is checked for Python, five Qt extension modules
and the authority. Clean native shutdown, data unmount and read-only filesystem
checks are required. No host mount, root permission, package installation,
service change, external guest NIC, host display socket or shared directory was
used. Qt is offscreen/software rendered; physical display behavior is untested.
Four earlier ARM-rendered screenshots were visually inspected; the
[UI report](arm64-ui-round7.md) preserves those separate run identities.

An initial prototype boot failed because ext4's dynamically requested CRC32C
crypto module was missing. The verified module closure was corrected before
the successful boots. Initial coordinator runs exposed an output-directory
guard conflict and kernel-path variable shadow; both failed before a shutter
invocation. Their state and tracebacks remain preserved. The final coordinator
uses persistent POSIX staging and separate guarded regular images in `build/`.

Independent review reproduced fast-exit log-limit bypass and surviving
descendants. Final tests check the log after exit and always clean the owned
process group. Guards also reject protected/source/camera/release/staged-rootfs
work paths, cache/output overlap, volatile/Windows POSIX staging filesystems
and inadequate space on either staging or image filesystem. Home expansion is
consistent. The final run records 81 source inputs, compares the full set again
and verifies the image's deployed application against it. Failed builds/boots
or uncertain captures are never automatically replayed.

Original final/failure evidence is under
[target evidence](../../system/targets/evidence/), with
[rootfs results](../../system/arm64-rootfs/evidence/) and
[independent image results](../../system/arm64-image/evidence/independent/).
The final 57-test log is captured directly. An earlier 25-test image run was
observed in tool stdout rather than saved as an original log; its provenance
record says so. Source-ZIP evidence copies may redact private task path prefixes,
with original hashes in its manifest. Source/import bytes are unchanged.
Generated images, caches, photographs and non-import screenshots are excluded.

## Open physical and production gates

This virtual runtime is a development profile, not production Buildroot,
U-Boot, a device tree, A/B target updates or secure boot. Choosing and validating
those needs the actual SoM/board and support terms. Physical display/GPU/input,
MCU power supervision, sensor register/power timing, open RAW14 SLVS-EC,
FPGA CDC/DMA, V4L2/libcamera, real-card power cuts and endurance remain
unimplemented/unvalidated. Authorized sensor implementation data, package and
optical stack, real E/EF bayonet geometry, HUMI datum, thermal closure and measured
calibration still gate the physical camera. See
[hardware review](../../hardware/interface-review.md) and
[release matrix](release-matrix.md). No unavailable physical test counts as passed.

R6 is byte-identical: source ZIP SHA256
`49ae3ed5eb6426505f0ef978522dee8f3615b2eab7458b0a95c84e353a76c010`,
Library `libfile_8b9acaf9fe048191ba7ed30e29cd1cd0`. R7 is a separate private
handoff, not an overwrite, publication or blanket license grant.
