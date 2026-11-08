# R14 icon Taskbar and proportional viewfinder

Project snapshot label:2026-11-05; implementation/checks:2026-11-06 UTC.

The requested Taskbar is open by default on the left,72px wide, with seven
icon-only routes: Camera, Photos, Recipes, Settings, Devices, Diagnostics and
System. Accessible names and hover tooltips provide labels without permanent
text in the rail. All surfaces retain R13's opaque black/white/neutral gray.

Swipe left on the rail or an icon to hide it; swipe right from the24px left
edge to reveal it. A horizontal gesture needs32px and1.5 times its vertical
distance. Vertical movement and an out-and-back drag do not become navigation.
Icon arrow controls provide a tap/keyboard alternative. The reveal handle stays
available during closing, so a rapid reversal starts from the current position.

One220ms cubic animation drives rail position and content geometry together.
For progress `p`, inset is `72*p`, content width is `W-inset`, and viewfinder
height is `(H-66)*(W-inset)/W`. The preview frame is centered vertically above
the fixed66px control strip. `Image.PreserveAspectFit` preserves the complete
source image and its aspect ratio; controls retain their normal touch sizes.
At800x600, opening changes the preview frame from800x534 to728x485.94. For
the current4:3 synthetic source, its painted image scales from712x534 to
647.92x485.94, a uniform0.91 factor. This changes display geometry, not RAW.

Taskbar gestures/navigation do not submit camera requests, change exposure,
acknowledge an unknown capture or release its original ID. The on-screen
shutter is held during a gesture/animation; focused rail keys activate only
navigation. Camera parameter acknowledgment and unknown-result barriers remain.
MENU's grouped settings are still available alongside the direct icon routes.

## Actual verification

Eight focused Windows Taskbar cases passed in9.720s, with zero photographs
and QML warnings. They cover real left/right input, drag-versus-tap behavior,
keyboard isolation, intermediate geometry, preserved image aspect, rapid
reversal, resize and an unknown original request.

Final Windows combined regression: **87 cases,86 passed, one explicit POSIX
FIFO skip, zero failures/errors,47.835s**. This includes the preceding79
camera/app/neutral checks plus eight Taskbar cases; overlapping runs are not
added to that total. An earlier combined run exposed a legacy Console fixture
clicking before parameter acknowledgment. It now waits for `canCapture` and
the enabled button before its single existing shutter press. No capture retry
or safety assertion was removed. Original logs remain separate evidence.

Visual review also found the narrow reveal Button stretching its Canvas icon.
The final Main wraps a16x16 icon in a content item with zero button padding;
the final combined regression above includes this correction.

The first ARM64 run passed86 of87 cases but failed to observe an intermediate
animation value through its Python polling timer. It is preserved as failed
in [failed001](artifacts/r14-ui-arm64/failed-001/verification.json). A bounded
no-shutter diagnostic measured observer callbacks lasting1814ms and1477ms:
the repeated Python object lookups themselves blocked the GUI event loop,
and that phase's native QML trace also lacked middle frames. A later cached
phase had0.144-0.242ms callbacks and a real rendered middle image at progress
0.876593. That phase was warmer, so it does not establish a sole cause or
prove the earlier phase was smooth. The complete trace is preserved.

The final test pins stable visual-item handles before timing and requires one
real non-null initial rendered frame. It retains the8ms observer, nominal220ms
animation, intermediate-state/proportion assertions and shutter/Space guards.
The recorder likewise pins five stable items before timing and continues to
read their live properties. It does not force progress, extend animation
duration, interpolate images or turn a failed run into a pass. Fresh final
ARM64 validation is separate from the failed001 and diagnostic observations.
No ARM64 native review photograph was taken after that failed gate.

Fresh final ARM64 regression: **87/87 passed, zero skips/failures/errors,
162.290s**; the encompassing harness took165.770s. Runtime was FreeBSD
15.1-RELEASE-p6, arm64, UID1000, Qt5.15.19/PyQt5.15.11, Python3.12.15,
`qpa=offscreen`. All47 staged test/source files remained unchanged.
This independent run includes actual intermediate geometry and shutter guards
under the unchanged nominal220ms animation, after a real initial rendered frame.

The [Windows receipt](artifacts/r14-ui-windows/verification.json) binds its
87-case run and one separate native review request,
`5b78329e-2498-4393-8ca2-fe63dc5b9e30`. Requested/actual exposure8000us and
gain1.25x agreed; original/export DNG SHA256 both equal
`f93cdc4100b19ee5cea93e26f8773e76c02d28d110926dd16c4118f497f43e2b`.
The native process exited0 and UI sources stayed unchanged, with no QML warnings.

The [native ARM64 camera review](../../build/DaOS_Camera_UI_R14_ARM64_final_002/UI-review.html)
records exactly one planned native review request,
`03a12fc4-334b-4f15-9c20-11e427ed6729`. Its requested/actual8000us and1.25x gain
agree; original/export DNG SHA256 both equal
`52003748b6f644f284f602ddb5a6049b2376508f9f8b65954ecd627e308bf740`.
The native daemon closed with exit0 and no QML warnings. Original/source hashes
are bound by review JSON SHA256
`6d97f6aa2df2ee7bbd19a61227b1138f430eeedd40f5bbbee6a38fe5975a9120`.
Its eight PNGs are embedded unchanged in the separate342,624-byte gallery.

## Review and limits

`tools/review_taskbar_motion.py` records five static images and a bounded
variable number of actual Qt grabs across collapse/expand, with request/ready
observations, progress and geometry. Its32ms one-inflight timer starts before
release dispatch. It holds actual QImages in RAM and defers PNG encoding and
hashing until the transition has settled; no pixels are interpolated.
The actual render instant lies within each observation interval; recording
cadence is not a claimed UI refresh rate. This proof uses the Python reference
authority, requests zero photographs, checks journal/settings/request identity
and source equality, and marks success only after clean fixture teardown.
Use its90s internal deadline with an external120s process limit. V2 records
the sampling-start origin and a separate release-call interval. Playback uses
the actual ready-observation differences, rather than a requested cadence.

The earlier cached V1 recorder is retained as historical instrumentation. On
ARM64 its first collapse grab spanned progress1.0 to0.965768 at57.609ms;
synchronous PNG encoding/hashing then delayed the next request until191.335ms,
whose interval ended at progress0. No interval was bounded by two middle
observations, so the recorder correctly failed. This was not relabeled as
success. V2 changes the recorder's I/O scheduling, with production UI and all
87 test inputs unchanged. Its additional source identity/execution is recorded
separately from the47 files covered by the preceding target regression.

The final V2 inspection tool, SHA256
`3117271b12246652c1987e7159fe83288eb53db6431d143de9e0b409ff1fdd98`,
was staged as an additional48th inspection file, not retroactively included
in the87-case regression. [Windows V2 evidence](artifacts/r14-ui-windows/motion-v2/verification.json)
records five static images plus14 real animation grabs, with four collapse
and three expand intervals bounded by middle observations, zero journal rows,
unchanged camera settings/request identity and clean fixture teardown.

The [actual ARM64 V2 animation proof](../../build/DaOS_Camera_UI_R14_ARM64_taskbar_motion_v2/UI-taskbar.html)
passed once in11.593s: five static images plus **nine actual animation grabs**,
with one strict middle interval per direction, zero photographs, unchanged
source/settings/request and clean teardown. Its report SHA256 is
`ec1bb3ee1c00af22bb6e97ae78840d1394fc85e02b4d1ed66c23c2858be8f160`.
The728,071-byte gallery SHA256 is
`ab9ca3fa483b1e131812e59fca73ed45bba3b742d62fbee93fc07ce722c44464`.
It preserves actual ready-time differences and all raw PNG bytes. No frames
were invented to match a fixed frame count or refresh rate.

The gallery separately passed six standard-library tests covering both clock
formats, raw image identity, metadata origins/ordering, failed-input refusal,
bounds and exclusive publication; its JavaScript syntax check also passed.
These infrastructure checks are separate from the87 camera/UI cases.

The [final target receipt](artifacts/r14-ui-arm64/final-002/verification.json),
SHA256 `901f4798dab5bd0b814699d4ea26e1517a64903876437cd5f6242ca847646056`,
binds the47 regression inputs and the separately executed48th inspection tool.
It includes the neutral renders, motion proof, native review, original failures
and clean-stop observations. The final guest synchronized buffers, QEMU402
exited0 and was absent, and its hidden holder131444 was also absent. The earlier
diagnostic boot likewise stopped cleanly; immutable base/seed/firmware/code
hashes stayed unchanged. Only owned task processes were controlled.

The reference VM used standard NAT with egress available, not a claimed
network-isolated appliance boot. It installed/downloaded no new runtime
packages. Runtime databases, authentication and DNG files are excluded from
the source archive; screenshot galleries are delivered separately with their
original PNGs. The R13 source ZIP and review evidence remain unchanged.

The native camera review separately takes one planned C++ HF-Sim photograph,
checks original/export DNG equality and never retries an uncertain shutter.
Reference animation/neutral renders and native capture evidence remain distinct.
The [R13 limits](round13-neutral-camera-ui-verification.md) continue to apply:
synthetic pixels, reference-guest offscreen UI, unchanged R10 appliance image,
and unvalidated physical sensor/display/MCU/optics. No physical FPS is claimed.
