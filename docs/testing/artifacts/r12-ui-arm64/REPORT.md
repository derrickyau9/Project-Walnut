# R12 camera-first UI on actual FreeBSD ARM64

Final source stage003 passed all73 tests with no skips on the existing FreeBSD 15.1-RELEASE-p6 ARM64 QEMU reference guest as UID1000. Python, QtCore, QtTest and the native daemon were actual FreeBSD AArch64 ELFs. Test time: 95.335seconds; bounded harness: 98.330seconds. Exact43-file source snapshots and source hashes are retained.

One separately authorized camera-first review passed with eight original Qt PNGs: seven primary800×600 views and one secondary1280×800 view. Original request UUID: `3c3d1529-ae62-410f-8cfc-a2a7180a661a`. Exactly one review shutter produced one complete native HF-Sim journal record at 8000us/gain1.25; exported DNG bytes had the same SHA256. Dark theme was checked in QML and in a recreated preference model before capture. The native process closed with exit0 and no QML warnings. Live preview is the separate Python synthetic Bayer PreviewStream; retained playback is derived from the saved native DNG.

The original failed70-case gate and no-shutter diagnostics remain under `failed-001`. They identified a FreeBSD ctime-only drift after the store's own atomic replacement. The intermediate72-pass source is retained under `tested-002`; a separate bounded FIFO-swap reproducer is under `fifo-reproduction`. Final source adds bounded content fencing and nonblocking leaf reads, with actual regression tests. None of the failed gates invoked the review runner or triggered a review capture.

The single standard-NAT VM boot used4CPUs/4GiB with guest egress enabled and only a loopback SSH forward. No package installation or download was performed. The guest later shut down cleanly, QEMU exited0, and original base/seed/firmware-code hashes stayed unchanged. No private keys, DNG bytes or databases are included here.

This is offscreen/software-rendered ARM64 reference-guest UI validation. It does not establish a rebuilt R12 appliance-image boot, physical display/sensor operation, or publisher authenticity. SHA256 receipts establish byte consistency. The rejected R11 desktop-style layout remains separate historical evidence.
