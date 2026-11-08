# R12 camera operating UI verification

Project snapshot date: 2026-11-05; final checks run on 2026-11-06 UTC.

R12 changes the default Qt application into a camera operating interface.
The image occupies the shooting screen; MENU opens grouped settings, Q opens
exposure/gain controls, and playback opens a single retained photograph.
Optional apps are reached through MENU > Tools. The R11 application rail and
workspace remain in `Console.qml` for development, behind `--interface console`.

## Actual implementation

| Operation | Implementation and boundary |
| --- | --- |
| Start shooting | `ui/on-device/Main.qml` defaults to 800x600 and automatically starts the independent Python synthetic preview. Starting preview never submits a photograph. |
| Change exposure/gain | Q and the exposure menu use the existing controller/native authority. Pending or unconfirmed settings hold the shutter. Displayed gain is analogue gain, not an invented calibrated ISO value. |
| Open settings | `CameraMenu.qml` has Exposure, Image and RAW, Display, Storage, System and Tools groups. Its category column exists only inside MENU. Local grid/theme/preview brightness do not change RAW exposure. |
| Review a photograph | `CameraPlayback.qml` displays one decoded retained DNG, with previous/next, INFO and explicit export. Request IDs and paths are confined to INFO/tools rather than the shooting face. |
| Recover uncertainty | The original unknown request remains held and queryable. Navigation does not resubmit it. Explicit acknowledgement permits a new photograph while preserving the unknown record. |
| Keyboard operation | Space takes a photograph only on the shooting screen, with auto-repeat disabled. Q/MENU, popup and playback inputs cannot become shutter commands. Escape closes the current overlay before leaving playback. |
| Optional apps | Recipes are editable/exportable saved workflow documents; the UI does not pretend that an unconnected Run button executes them. Device, diagnostic and system pages report available facts. |

Preview is `Python PreviewStream`, a separate synthetic scene, **not** pixels
read from a sensor or the native capture buffer. Capture/DNG generation uses
the existing C++ `hf-camerad` HF-Sim adapter. The small review mode is 128x96
Bayer with 14-bit sample values in a 16-bit DNG container. A preview image is
not evidence of a physical sensor, lens, screen or FPGA receiver.

## Preserved first results and defect diagnosis

The first Windows R12 interaction run passed all12 cases in7.694s, with no
QML warnings. Its eight screenshots and one completed native review capture
remain in `build/DaOS_Camera_UI_R12_release`; request
`52148e57-4f00-47ba-ac30-e4546457b2da` used requested/actual8000us and1.25x gain.
Original and exported DNG SHA256 both equal
`c25c1fd6af3cea4ec273063d5be469b0410d902d1f8daa2ba63fc44805aa3c5a`.
That report is unchanged, SHA256
`ab863486d6181af1928fcfe2db8d5d3c6547dd94758cb668c75293f279ab2468`.

The first actual FreeBSD ARM64 combined run executed70 cases:69 passed,
one failed, none skipped. The failing case clicked the framing-grid switch
but the preference store rejected the write as an external change. This was
not a lost mouse event: a separate no-shutter diagnostic recorded a visible,
enabled62x44 control at scene point755,116, with pressed/down true. Keyboard
activation was rejected by the same guard.

The metadata trace found that only `ctime_ns` advanced after the application's
own atomic replacement. Device, inode, size, `mtime_ns` and the full98-byte
file content stayed unchanged. Expected ctime1793923865026774000 became
1793923865304513000 while mtime stayed1793923865023602000. All sampled content
SHA256 values were
`d5b8490711835aea9f90c3e3c0c54d994a836a5cb994c9dd309188c64a177a7b`.
The original failed suite and diagnostics are preserved; they are not relabeled
as passes. No review capture was dispatched after that failed gate.

## Final verification

The preference store now combines stable device/inode/size/mtime identity with
a bounded no-follow content SHA256 read at both pre-write checks. A ctime-only
change is accepted; an in-place edit with the same size and restored mtime is
still rejected. Existing parent/reparse/symlink/corrupt-file protections remain.

A separate controlled POSIX probe reproduced a special-file race: replacing
the checked regular leaf with a FIFO before `open` blocked the old reader.
Its two-second watchdog killed and reaped that isolated child. POSIX reads
now open nonblocking and reject nonregular descriptors with `fstat` before
reading. The regression uses a real FIFO in a bounded subprocess, rather than
a mocked pass. No physical hardware, VM capture or unrelated file was used.

The first combined Windows run passed71 of72 cases and failed a legacy
short-lease fixture before its initial capture: background acquisition could
occur during cold QML loading and spend its0.6s lease before the test began.
The fixture now gates only that initial factory until a post-load Qt callback.
It retains the0.6s lease,1.3s blocked export and unchanged no-retry capture path.
Monotonic acquisition ordering is asserted and logged. The original failure
is preserved in [Windows intermediate evidence](artifacts/r12-ui-windows/intermediate-verification.json).

Final Windows combined regression: **73 cases,72 passed, one explicit skip,
zero failures/errors,30.918s**. The skipped case requires POSIX `os.mkfifo`,
which Windows does not provide. All12 camera-first cases passed, including
actual touch increase/decrease gain buttons with authority readback. This
suite also covers the22 model,16 app and23 retained Console cases. These
counts describe one execution; earlier overlapping checks are not added.

The final [Windows receipt](artifacts/r12-ui-windows/final/verification.json)
binds that run and the current-source [eight-image review](../../build/DaOS_Camera_UI_R12_verified/UI-review.html).
Exactly one planned review shutter completed request
`c7e65142-3a00-4557-8952-8bff1be0c0b8`, at requested/actual8000us and1.25x gain.
Its original and exported DNG hashes both equal
`d9a322b7ecb5d5a30114570ed33aa2d27fe5d944c2fea2b5b8041fef749521e7`.
The native process closed with exit0, all19 recorded source hashes still
matched, preferences recreated as dark, and no QML warnings were recorded.
Review JSON SHA256 is
`f6fae50360f9df899983fa65f105bb555fe4e7b6c8d49c09bdfe83d5be51513d`;
the self-contained gallery is310,962bytes, SHA256
`987b9497fd854b74c0deaa6392c99fd4b224c068eea99bdd5a7db30020e63607`.
Screenshots are real Qt renders, with unchanged PNG bytes embedded in the
gallery; the displayed image data is explicitly synthetic.

Final actual ARM64 regression: **73/73 passed, zero skips/failures/errors,
95.335s**; the encompassing verification harness took98.330s. The real-FIFO
case ran on this POSIX target. Runtime was FreeBSD15.1-RELEASE-p6, arm64,
UID1000, Python3.12.15, Qt5.15.19 and PyQt5.15.11, with `qpa=offscreen`.
The [target receipt](artifacts/r12-ui-arm64/final-003/verification.json)
binds all43 staged source files, actual ARM QtTest/native binary identities,
test output and later review. Its SHA256 is
`f30ad645eb78bbac7c684221e4dc964b80ea975c0c4b6ed6a8697a2e9ce2fb2f`.

The [eight actual ARM64 screenshots](../../build/DaOS_Camera_UI_R12_ARM64_final_003/UI-review.html)
come from one planned review capture, request
`3c3d1529-ae62-410f-8cfc-a2a7180a661a`, at requested/actual8000us and1.25x gain.
Original/export DNG hashes both equal
`6d28e892bc4ef68479a7253f0c58c251c9eb0c95350c76f6aa373671204069ca`.
The native process exited0, all19 review source hashes remained unchanged,
and recreated preferences/dark QML theme agreed, with zero QML warnings.
The Q screenshot honestly shows **Live view paused** during a parameter
transition; seamless preview while changing settings is not claimed.
Review JSON SHA256 is
`facf6b682705d587afde34b9aa7c782817827f34cd88e24722872176be1b3898`.
The gallery is328,647bytes, SHA256
`d93a631f356672024490a5bee4663ee726f9fb6765918b39af91db1f477096c3`.

The reference guest shut down cleanly with buffers synchronized. Its owned
QEMU PID410 exited0 and was absent afterward; hidden WSL holder16296 was also
absent. The closure receipt is included in the target evidence. This stopped
only the task VM, without a global WSL shutdown. The original VM base, seed
and source identities were unchanged. Failed001, intermediate passing002 and
the controlled FIFO reproduction remain separately preserved in
[the ARM evidence overview](artifacts/r12-ui-arm64/REPORT.md).

The gallery generator separately passed20/20 tests on Windows and Linux,
including create-only publication, runtime attribution, bounded PNG integrity
and rejection of failed/misattributed reports. This is not another camera UI
suite and is not added to the73 target cases. UI source and its target test files were
frozen before final runs; no screenshot was retouched.

## Source handoff

The first packaging attempt refused a diagnostic `.log` containing a personal
workspace prefix and published no archive. Text logs inside the already
declared evidence directories now receive the same copy-only prefix redaction
as evidence text/JSON. Source files, original evidence and imported hardware
bytes remain unchanged. The bundle manifest records both original and delivered
hashes for transformed copies. Secret detection and refusal of private paths
outside the declared evidence scope remain active.

All21 source-bundle tests passed in6.436s, including original-byte retention,
log redaction, outside-evidence/secret refusal, exclusive publication, bounds
and tamper detection. This packaging suite is separate from the73 target UI
cases. The source-only ZIP excludes screenshots, DNGs, databases, credentials
and generated binaries; the two self-contained screenshot galleries are
separate deliverables. Runtime/build/package locks and retained provenance
describe dependencies rather than implying every third-party binary is present
or rebuilt from source. Packaging receipts are in
[the source-bundle evidence](artifacts/r12-source-bundle/verification.json).

## Build and runtime boundary

The explicit fresh Windows native executable is
`build/native-r11/Release/hf-camerad.exe`, SHA256
`1c7d8bac9427ee71fa8645ba78359add17cc9e813cde76ffe40f2e27bf3a9d8d`.
Its four native CTests passed before UI validation; no native C++ change is
part of this UI revision. The existing default `build/native` directory may
contain an older executable and is not silently substituted in this review.

The target native FreeBSD ELF is AArch64 machine183, OSABI9, SHA256
`be752fd42c631e763682fa96a34b929a2bd80b5ae9ab3eb1b6f17653bc31bc28`.
Reference-guest Qt checks use the offscreen platform. They do not establish a
physical display or a newly assembled image boot. The preserved R10 image003
contains its earlier UI; R12 has not been inserted into that frozen image.

Retail integration remains a source-traceable adaptation of selected colors
and interaction conventions, with independently authored Qt components.
The original React App Shell and TypeScript capability engine/control code
have not been imported. See the [architecture assessment](../architecture/daos-relationship.md)
and [pinned Retail adaptation](../architecture/retail-camera-ui-adapter.md).

Real IMX410 initialization, a public SLVS-EC receiver, board-specific drivers,
verified MCU signals, lens electronics, measured sensor calibration, HUMI
tolerances and thermal closure remain hardware/vendor-data work. FreeBSD A/B
boot/update recovery is also not implemented by the earlier Linux update model.
