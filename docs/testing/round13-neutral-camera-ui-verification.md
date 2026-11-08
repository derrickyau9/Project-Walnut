# R13 neutral camera UI

Project snapshot label: 2026-11-05; implementation and execution: 2026-11-06 UTC.

The user's requirement is: **UI元素背景要纯黑或者纯白或者纯灰**.
R13 uses opaque black, white and neutral gray UI surfaces. Active color roles
have equal red, green and blue channels. It removes tinted blue/slate surfaces,
red selected backgrounds and translucent colored backplanes. Viewfinder and
retained photograph pixels keep their source colors.

The camera opens into the same viewfinder, with Q, grouped MENU, single-image
playback and tools inside MENU. This is a color/style change, with unchanged
capture authority, request IDs, configuration confirmation and recovery flow.

| Role | Dark mode | Light mode |
| --- | --- | --- |
| Main background | `#000000` | `#ffffff` |
| Surface | `#111111` | `#ffffff` |
| Raised control | `#222222` | `#eeeeee` |
| Layer | `#333333` | `#dddddd` |
| Selected control | `#666666` with white text | `#666666` with white text |

The original Retail colors remain only in the theme's `referencePalette`
provenance record. `cameraAppearance.policy=achromatic-opaque-ui` explicitly
records the user's override; the active theme is not represented as Retail's
original computed palette. The screenshot-review page also uses neutral chrome.

## Verification

Focused Windows checks: **6/6 passed in5.677s**, zero skips, warnings or capture
rows. Actual rendered flat UI regions are required to have equal R/G/B and
opaque alpha, while photographic content is excluded. The tests cover all six
MENU groups in both modes, Q controls, all four tools, empty playback, shooting
bar, top badges and shutter backgrounds. A separate live-image check requires
visible color, preventing an accidental whole-window grayscale fix.

The first focused attempt exposed transparent gaps in `contentItem` grabs of
the recipe page and a test that sampled the image before Qt marked it ready.
R13 adds an explicit opaque base plane behind every route; the image check
waits for `Image.Ready` and painted dimensions. Original failure evidence is
preserved rather than relabeled. The final no-shutter run saves13 unchanged
800x600 PNGs with runtime/source hashes in
`build/DaOS_Camera_UI_R13_neutral_proof_final/renders.json`.

Final Windows combined regression: **79 cases,78 passed, one explicit skip,
zero failures/errors,35.157s**. Only the POSIX real-FIFO regression is skipped
on Windows. Existing73 cases and the six new neutral tests share this one run;
overlapping earlier checks are not added to that total. The UI model, capture
authority and source image generators retain their preceding implementations.

The [Windows receipt](artifacts/r13-ui-windows/verification.json) binds the
combined run and one planned native review capture,
`3b40df7f-c8f3-4e0d-81fe-7197169ef932`, at requested/actual8000us and1.25x gain.
Original and exported DNG SHA256 both equal
`46d326fc94a15d2eaa07ca565430e53d1185cb14b7cbaf7fd05c749e23500f39`.
The native process closed with exit0, zero QML warnings and unchanged sources.
Review the [eight camera images](../../build/DaOS_Camera_UI_R13_verified/UI-review.html)
and the separately labeled [13 no-shutter reference renders](../../build/DaOS_Camera_UI_R13_neutral_proof_final/UI-backgrounds.html).

Actual ARM64 combined regression: **79/79 passed, zero skips/failures/errors,
118.216s**; the encompassing harness took121.708s. Runtime was FreeBSD
15.1-RELEASE-p6, arm64, UID1000, Python3.12.15, Qt5.15.19/PyQt5.15.11,
`qpa=offscreen`. All44 staged source files remained unchanged. The actual
POSIX FIFO case executed rather than being counted as a Windows pass.

The [13 actual ARM64 reference renders](../../build/DaOS_Camera_UI_R13_ARM64_neutral_proof/UI-backgrounds.html)
have five zero-capture fixture records and source hashes unchanged before/after.
They show both MENU/Q modes, all four tools in both modes, and colored live
preview. Their metadata SHA256 is
`ea54f03a2d868cf1f5a95e2d248853aea72e33a72aec8752f07386f5a4a3c907`.
They use the Python reference authority and are kept distinct from the native
capture review. Original PNG hashes and dimensions were checked before copying
and embedding; no image was retouched or converted to grayscale.

The [eight native ARM64 review images](../../build/DaOS_Camera_UI_R13_ARM64_final_001/UI-review.html)
record one planned completed request, `589e74a4-d66a-4f0c-98f4-12a8a48f0893`,
at requested/actual8000us and1.25x gain. Original/export DNG hashes both equal
`c2c939663b4e1a4534534a72b7dd5102a63a0b3cfcb44166e628f15d848b47c5`.
Dark QML background is `#000000`, recreated preferences agree, the native
process exited0, zero QML warnings occurred and19 review source hashes stayed
unchanged. Review JSON SHA256 is
`f87815b2f47aa46928ddfd1c7cb980a7aa4d1cc8458b15c7378b857a018deb33`.
The Q screenshot may show a truthful paused-preview state during a setting
transition; continuous preview is not claimed by that screenshot.

The neutral gallery chrome separately passed the existing20 gallery tests;
only CSS changed, while input/PNG/runtime/security behavior stayed unchanged.
The existing source packager retains its21-test boundary verification. These
overlapping infrastructure checks are not added to the79 camera/UI cases.

The [final ARM64 receipt](artifacts/r13-ui-arm64/final-001/verification.json),
SHA256 `18a62f4830876619b9e319f8e4710f9a5f726abe0374d72dd93bfc19093eadbb`,
binds the original test logs,13 reference images,8 native review images and
closure observations. The guest synchronized its buffers and shut down;
owned QEMU PID403 exited0 and was absent afterward, as was the owned hidden
WSL holder148140. Base, seed and firmware/code hashes stayed unchanged.
The task used standard QEMU networking with egress available; it is not
represented as a network-isolated appliance boot. No packages were installed
or downloaded for this validation, and no authentication, photo or database
runtime files enter the source handoff.

The two review galleries are separate local files. Native ARM64 review HTML
SHA256 is `e1f02a92b30fb04b82d7ad4fd16ef01adf55ce7d39aa258ae25d881e1364443d`
(315,744bytes); the ARM64 dark/light/tool reference HTML SHA256 is
`593f5f90f02a4898ad5b5410f4fc8edff88bc75905cf002310dc4f1ee38c74ed`
(848,913bytes). Embedded PNG bytes are unchanged.

The frozen [R12 verification](round12-camera-first-verification.md), its actual
screenshots and source ZIP remain unchanged. Its167,139,108-byte source ZIP
SHA256 is `0267ea430924ec1f95ac6e886b9641932ec5eecc3535d707f7bf9dbb081b4479`.
It is historical evidence, not a screenshot of the R13 appearance.

## Limits

The live preview is a separate Python synthetic Bayer scene. Captured DNG is
generated by native C++ HF-Sim. Reference-guest offscreen Qt execution is
distinct from physical hardware, a physical display or an updated appliance
image. The R10 ARM64 BSD boot image remains unchanged. Real sensor/receiver,
drivers, MCU signals, lens electronics, measured calibration and mechanical
validation retain the [existing hardware boundaries](round12-camera-first-verification.md).
