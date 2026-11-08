# Project Walnut preview 2

**They made the leaf. We cracked the nut.**

This private engineering image targets Raspberry Pi 4 ARM64 and the specified
800×480 DSI development configuration. It derives from the validated first
preview, preserves the stable kernel and early USB integration, and installs
`camera-experience-006` with the Project Walnut display name and DNG Make.
Technical identifiers, recipe formats, existing RAW namespaces and
`/opt/hasslefree` paths remain compatible.

## Photographic UI and electronic level

The application includes three user-provided JPEG samples. Explicit
`--demo-image ui/on-device/demo-images/coast.jpg` mode displays the selected
photograph in the viewfinder, marked as a sample. Actual capture remains the
synthetic HF-Sim authority. The gallery imports the three JPEGs through the
real photo library and native image-processing workflow for playback,
comparison, rating and nondestructive editing. Original file bytes are retained.
See [the sample source record](../../ui/on-device/demo-images/SOURCES.json).

The dual-axis electronic level has a Display-menu switch and independent local
preferences. It is off by default. Missing, invalid or older-than-500ms attitude
data produces unavailable readings, never a measured zero. Valid roll and pitch
within ±0.5° show the aligned indicator in green. `--demo-attitude ROLL PITCH`
is explicitly labeled DEMO and does not persist the demonstration preference.
`--attitude-file PATH` accepts the bounded local-provider format; the two input
modes are mutually exclusive. There is no verified physical IMU driver or
calibration in this preview. See [the provider contract](../../ui/on-device/LEVEL-PROVIDER.md).

本预览统一使用 Project Walnut 品牌，保留既有技术标识与旧照片兼容性。三张
用户提供的 JPEG 用于明确标记的样片预览与真实照片库、比较、评分和编辑演示；
底层 RAW 拍摄仍为 HF-Sim。水平仪支持双轴显示、开关和有新鲜度校验的外部姿态
输入，示例角度标记 DEMO；当前没有经过实机验证的 IMU 接入或校准。

## Workflow handoff

Run, Resume and Cancel use the controller's actual handoff readiness. A pending
state request keeps the affected buttons unavailable. A refused handoff retains
the paused job and its original request IDs; it never queues a later exposure.
Unknown capture results are reconciled only with the original request ID.

工作流按钮等待控制权真正就绪；忙状态拒绝不会丢失暂停记录，也不会自动重试
拍摄。结果不明的请求继续使用原请求编号核实，避免重复曝光。

Sibling helpers reuse the already loaded local module across supported import
orders. This keeps catalog exception identities consistent so a newly captured,
not-yet-indexed photo can use the existing sidecar fallback instead of remaining
uneditable after a failed read.

修复了模块重复加载导致异常类型不一致的问题：新拍摄照片尚未索引时，旁车
读取能够正确使用回退路径，不会因导入顺序而一直无法编辑。

## Bound derivation inputs

The base is downloadable from the
[historical first preview](https://github.com/derrickyau9/Project-Walnut/releases/tag/v0.1.0-preview.1):

```text
Project-Walnut-DaOS-RPi4-DSI43-arm64-v0.1.0-preview.1.img.xz
359999448 bytes
SHA256 d3f9272d04c37993eaf2e53a54dd09ce2c4e0b8f8b5bb10174106bd12e249ee6
expanded: 3254820864 bytes
expanded SHA256 3d094085f22e119586e46f80cff9b992f0c02c6296fa39b99155efc3282e2dd4
```

Use the hash-bound
[input profile](../../system/freebsd/raspberrypi/factory-walnut-preview-002.profile.json)
with the [native FreeBSD builder](../../system/freebsd/raspberrypi/build_factory_preview.py).
Every action receives the same `--input-profile` and its exact
`--input-profile-sha256`. `prepare` checks the inherited core without rewriting
it. `app` accepts the bound 006 archive, installer and ELF inspector; `finalize`
checks/compresses the result. `qa-final` exports a separate serial/single-user
QA copy. All vnode devices refer to owned scratch images, never a physical card.
The prior [factory build guide](factory-preview-001.md) describes the native
builder prerequisites and pinned OS sources. The new application can be packed
with `tools/camera_experience_bundle.py --version camera-experience-006`, using
the current-source FreeBSD ARM64 native workers.

The image retains two developer public-key authorizations and requires explicit
recipient enrollment before wider distribution. It imports no live-card photo
database or private recovery overlay. The photos/calibration partitions remain
the factory scaffold; the three demonstration JPEGs are application assets.
Automatic recovery enrollment, a hardware-watchdog policy and experimental
Wi-Fi/OpenBSD kernels are excluded. Mixed runtime licenses and their notices
remain in force; this is not a source-only, bit-reproducible OS-build claim.

## Release validation

The Release manifest and QA summary bind the exact compressed/raw image hashes,
application archive, source revision and bilingual gallery. Native filesystem,
ELF dependency and isolated ARM runtime checks are separate from physical-board
acceptance. This exact rebuilt image has not been newly flashed and accepted on
the physical board. Wi-Fi, physical sensor/lens behavior and a real IMU remain
unverified. No claim of physical USB re-enumeration or DSI/touch acceptance is
made from virtual-machine tests.
