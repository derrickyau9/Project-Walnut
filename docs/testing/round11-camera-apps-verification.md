# R11 native app workspace: preserved verification

R11 added a DaOS Retail-informed native Qt application workspace with a
permanent left dock, launcher, command search, grouped settings, retained
photos, portable recipe editing/export and read-only tools. The user rejected
that desktop-style layout for a camera body. R12 replaces the default with a
camera-first interface; the R11 workspace survives as `Console.qml`, explicitly
selected with `--interface console`. R11 evidence is preserved rather than
relabeled as the redesigned UI. Read the
[R12 report](round12-camera-first-verification.md) for current results.

## Windows release review

The preserved release directory is `build/DaOS_Camera_UI_R11_release`.
Its `review.json` records `passed: true`, 13 actual Qt screenshots, one shutter
action and one completed native HF-Sim request:
`5663d156-5597-4293-872c-584ae685d581`. Requested/actual exposure is10100us,
gain1.0. Retained DNG and export share SHA256
`9f24d1eebae5ee0d48e377b84a9563299f2d9f14940c436629b102e2522fbaa2`.
The native process closed with exit0 and no QML warnings were recorded.

The runtime was Windows11/AMD64, Python3.12.7, PyQt5.15.10 and Qt5.15.2,
using offscreen software rendering. Native executable SHA256:
`1c7d8bac9427ee71fa8645ba78359add17cc9e813cde76ffe40f2e27bf3a9d8d`.
The 12 app-shell interactions passed. The separate 23-case historical UI suite
passed its confirmed rerun in20.699s; `build/r11-device-ui-release-confirmed.log`
preserves that result. An earlier transient short-lease test failure and its
diagnosis were retained, not erased. The final passing rerun did not weaken the
0.6-second lease assertion or change capture authority behavior.

The earlier `build/DaOS_Camera_UI_R11` attempt stopped before any capture when
a stale executable did not supply persistent-settings confirmation. That was
a held shutter, not a successful review. Subsequent review directories are
separate, explicitly created sessions; no uncertain capture was blindly retried.

## ARM64 reference guest: test pass, review failure

Actual FreeBSD ARM64 reference-guest execution passed all70 selected tests:
19 shell-model,16 app-controller,12 app-shell and23 historical UI cases.
That test pass is distinct from the later review result. Canonical receipts:
[ARM report](artifacts/r11-ui-arm64/REPORT.md) and
[verification record](artifacts/r11-ui-arm64/verification.json).

The ARM review dispatched one capture, retained one complete request
`bb435cce-d84b-4d2c-8eed-ef41dc580638` and closed its native process normally.
However, the intended dark-theme action was not reflected in saved preferences;
the saved theme remained light. The review therefore remains **failed**.
Read-only evidence did not distinguish missed automation input from UI handling.
`13-compact-camera-dark.png` is a preserved filename, not proof of a dark screen.
No second review capture was taken to replace the failed result.

This was a network-enabled standard reference VM with loopback SSH, not the
offline R10 appliance image boot. Its source/native identities, transport and
shutdown receipts remain in the verification record. No physical camera or
display was involved.

## Provenance and limits

The inspected Retail reference is `derrickyau9/DaOS-iHOMI` revision
`10d233e316e1c258e8aa365f6a6de4d82d10c7f5`. Selected colors, dimensions and
interaction conventions were independently adapted to native Qt. Raw Retail
CSS/JS modules, business modules, third-party font/icon/glass assets and the
original TypeScript runtime were not imported. See the
[adapter record](../architecture/retail-camera-ui-adapter.md).

Python generated a separate synthetic live-preview scene; native C++ HF-Sim
generated/saved the capture. An older screenshot description saying native
preview must not be read as proof of a native or sensor live pipeline.
R11 did not rebuild R10 image003, integrate real hardware, complete the DaOS
framework port or obtain product acceptance. R10 image/source hashes and all
earlier delivery identities remain unchanged.
