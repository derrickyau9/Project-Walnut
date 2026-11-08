# DaOS BSD Unix ARM64 R10 delivery

This delivery is a **FreeBSD-derived ARM64 camera appliance prototype**. Its
source-built world/kernel are FreeBSD15.1-RELEASE-p6 with kernel ident `DaOS`.
The camera authority is native C++; QtQuick/PyQt5 is the local body interface.
HF-Sim supplies synthetic Bayer pixels. This is not an IMX410 driver, physical
camera release, new-from-scratch Unix kernel or completed original DaOS source
integration. The [verification report](../../docs/testing/round10-freebsd-arm64-verification.md)
distinguishes builds, reference-target suites and actual image boots.

## Local deliverables

The source-only ZIP and UI companion are separate from the compressed OS
image. Build outputs are intentionally excluded from the source archive.

| File in the workspace | Purpose |
| --- | --- |
| `build/HassleFree_FF1_R10_DaOS_BSD_ARM64_Source_2026-11-05.zip` | Clean project source, tests, editable prototype imports, provenance and canonical evidence. |
| `build/DaOS_BSD_Unix_ARM64_R10/DaOS-arm64.img.xz` | Frozen, actually booted source-built ARM64 image003. |
| `build/DaOS_BSD_Unix_ARM64_R10/image-evidence.json` | Original assembly receipt binding the exact raw image and inputs. |
| `build/DaOS_BSD_Unix_ARM64_R10/UI-review.html` | Local review page with actual unchanged800x600 framebuffer PNGs. |
| `build/DaOS_BSD_Unix_ARM64_R10/SHA256SUMS.txt` | Final delivered file identities; hashes are not publisher signatures. |

The raw image is **3,187,712,000 bytes**, SHA256
`69c824a3aa1f992fcb38e285145e4549c76107cab95a2a26e2e391e401b4942a`.
The XZ is **362,424,748 bytes**, SHA256
`e06c24c415894e9e396b7b9af147fb25953c585e01e1ec44fd71994f51dd05a4`.
Decompression and a full hash pass were executed, rather than inferred from an
XZ filename. Allow space for both the compressed file and3.19GB raw output.

## Boot the delivered image

The tested host runner is Linux/WSL2 with Python3, QEMU AArch64 tools,
`qemu-img` and AAVMF code/variables at the paths checked by
`tools/freebsd_image_boot.py`. It does not run directly in Windows Python.
The firmware paths are `/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd` and
`/usr/share/AAVMF/AAVMF_VARS.fd`, both regular64MiB inputs. The runner locates
`qemu-system-aarch64` and `qemu-img` on PATH. Source build-host/reference-target
bootstrap additionally uses the documented task-local SSH transport; the
delivered camera-image runner itself uses no SSH or injected key.
It requires four virtual ARM Cortex-A72 CPUs,4GiB guest RAM and space for a
private copy-on-write disk. A64-bit ARM host is not required: the tested
Windows/WSL host used x86_64 and TCG emulation. This is emulation, not board
validation. No root mount, host share or installed host camera service is used.

Extract the source ZIP, change to its `HassleFree-FF1` directory, and place the
XZ/assembly receipt in a separate owned artifact directory. With verified
copies and an existing canonical parent directory:

```sh
# Run in the artifact directory; xz refuses an existing output by default.
xz -dk DaOS-arm64.img.xz
sha256sum DaOS-arm64.img

# Run from the extracted HassleFree-FF1 source directory.
python3 -B -m tools.freebsd_image_boot serve \
  --image /absolute/artifacts/DaOS-arm64.img \
  --sha256 69c824a3aa1f992fcb38e285145e4549c76107cab95a2a26e2e391e401b4942a \
  --provenance /absolute/artifacts/image-evidence.json \
  --work-dir /absolute/owned-parent/image-boot-001 --timeout 900
```

This is a **headless QMP review runner**, not a host desktop window. From a
second terminal, use its [screenshot/input commands](image-boot/README.md).
The actual reviewed framebuffer was800x600; use observed coordinates, never
assume the examples' geometry. The first boot can spend over a minute in
firmware/rc before the camera appears. Nonblack firmware or console pixels do
not establish application readiness. No command automatically presses shutter.

Stop via `python3 -m tools.freebsd_image_boot stop --work-dir WORK` and wait
for a completed observation with exit0 and `all_buffers_synced_marker_seen`.
Resume that state only in a **new** child work directory with the documented
`resume` command. The pristine raw image remains read only; deleting or
recreating a private overlay starts a new camera state. Preserve the complete
backing chain and immutable source file while a child state is needed.
Use persistent disk for your own retained state. The development task used a
RAM-backed raw-image cache to work around local disk pressure; losing and
recreating it changes its sealed file identity and old states correctly refuse
automatic resume even if a new file has identical bytes. Do not bypass that
check or describe cache restoration as a previously accepted resume.

## What was actually accepted

Image003 booted native Qt/xcb through FreeBSD efifb/SCFB at800x600. All manual
controls and the runtime-derived `DaOS BSD Unix`/FreeBSD/arm64 footer were
visible. One shutter action saved request
`afb981a3-b924-4d55-88be-6402189c2242`, synthetic128x96 Bayer, requested
10100us and gain1.0. A clean full kernel restart recovered that same completed
request and persistent exposure. Retained review and GUI export to a new local
file completed with **zero additional shutter actions**. Both runs had no NIC,
SSH, NoCloud, injected key or host share. The delivered pristine image contains
no user's captured photo, journal or copied runtime authentication cookie.

The [human-acceptance receipt](image-boot/evidence/image003-ui-acceptance.json)
is separate from unchanged automated observations that intentionally retain
`UI_readiness_claimed:false`. Original assembly evidence also correctly retains
`boot_validated:false`: later observations do not rewrite an earlier receipt.

Independent Windows tifffile and LibRaw decoding matched all12,288 Bayer
samples to a separate Python oracle. Read-only SQLite inspection found exactly
one complete capture, requested and actual10100us/gain1.0, frame_sequence1.
After restart the full capture row was unchanged; original and GUI-exported
files were byte-identical25,662-byte DNGs, SHA256
`34526aaace0136e38935822d974fa8d548060d0d5ea8ab8b726894b31f9529e0`.
See the [independent evidence](image-boot/evidence/image003-capture/).

## Rebuild and scope limits

Follow the [pipeline](README.md), [base source recipe](base/README.md),
[native cross-build guide](../../docs/testing/freebsd-arm64-cross-build.md),
[runtime collection/assembly recipe](image/README.md) and
[image boot guide](image-boot/README.md). The delivered source ZIP is **not** a
self-contained offline SDK. A clean rebuild additionally needs the pinned
FreeBSD source archive, actual FreeBSD build host/Clang, matching ARM64 package
closure or verified runtime cache, CMake/native tool inputs and QEMU/AAVMF.
Private VM disks, task SSH keys and the multi-gigabyte runtime/build cache are
not part of the source ZIP. Receipt paths identify historical private outputs,
not bundled downloadable payloads. Exact source locks and package inventories
are preserved; bit-for-bit image rebuild has not been demonstrated.

Qt/Python/Xorg runtime files include declared binary packages. Collection
checks pkg metadata/checksums and ELF dependencies; it does not independently
attest package repository signatures or build every dependency from source.
Licenses and notices are per component. The project's proposed open-source
licenses are not a blanket grant for all third-party or prior DaOS code.

Source-only changes after frozen image003 are: the legacy web console subtitle
now spells `DaOS`, and the explicit QMP operator click helper now holds each
press150ms and releases once in a finally path for USB polling. No press is
automatically retried. The click helper passed34 Linux tests and23 Windows
tests with11 explicit platform skips. Neither changes the native camera binary or the tested body
UI payload. The frozen image still contains its earlier legacy console HTML;
its normal native Qt body UI already displays `DaOS` correctly. Future image
builds consume the corrected source. Historical paths/import names/receipts are
not mass-renamed, because they are technical identities and evidence.

The source packager also passed20 targeted tests after admitting only the two
exact canonical28.4MB FreeBSD runtime inventories under a32MiB inspection cap.
General JSON retains its8MiB cap. Format/architecture/list-shape, parsing,
secret/path inspection and evidence redaction checks remain enforced. The ZIP
is independently re-opened and every delivered member hash checked.

Read-only `/etc` hostid/machine-id and entropy-save warnings remain in boot
output; a fresh virtual RNG was attached on both runs. No shared entropy seed
is baked into the pristine image. This virtual profile does not supply a real
board's entropy, secure boot/update trust or independent MCU recovery.

Real sensor supply and publishable initialization/SLVS-EC data, receiver PHY,
DMA/board drivers, validated display/power/supervisor MCU signals, HUMI lens
geometry, measured optical/thermal/calibration performance and second-person
manufacturing remain open. FreeBSD A/B update/boot health integration is also
not implemented; historical Linux update-model tests do not fill that gap.
