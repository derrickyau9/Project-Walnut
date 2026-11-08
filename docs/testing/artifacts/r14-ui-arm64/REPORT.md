# R14 Taskbar validation on actual FreeBSD ARM64

Final source passed all 87 tests with zero skips as UID1000 on the existing FreeBSD 15.1-RELEASE-p6 ARM64 reference guest. Unittest took 162.290 seconds; the bounded harness took 165.770 seconds. Tests cover the default-open seven-icon Taskbar, left swipe to hide, right swipe from the left edge to reveal, shared 220 ms inset animation, proportional resizing in both dimensions, preserved image aspect, and transition/unknown-capture guards.

The first gate failure and diagnostic attempts remain preserved. The observer fix caches item handles and requires one real initial render; no duration, progress or geometry is injected. The first recorder also remains failed: PNG encoding between requests prevented a strict intermediate interval. It is not promoted to a pass.

The separate v2 inspection tool retained actual Qt images in RAM and encoded PNGs after each transition. It passed with five static screenshots and 9 real animation frames, with strict intermediate request/ready intervals in both directions: {'collapse': 1, 'expand': 1}. It recorded zero capture rows, unchanged settings/UUID and clean teardown. These are observation intervals, not exact render instants, a normalized frame sequence or an FPS guarantee.

One separately authorized native review passed with eight screenshots and exactly one complete HF-Sim DNG, UUID `03a12fc4-334b-4f15-9c20-11e427ed6729`, 8000 us and gain 1.25. DNG and export SHA256 match: `52003748b6f644f284f602ddb5a6049b2376508f9f8b65954ecd627e308bf740`. The native process exited0 with no QML warnings. Thirteen separate neutral-theme renders also recorded zero capture rows. Live preview is the independent Python synthetic Bayer stream; saved playback comes from the native DNG.

The tested 47-file source snapshot is retained. The new motion tool is a separately identified 48th inspection file, not retroactively claimed to have been in that test package. Both owned VM boots stopped cleanly; final PID402 and hidden holder PID131444 exited, with original base/seed/firmware-code hashes unchanged. R13 artifacts were preserved. No private keys, DNG bytes or databases are included here.

This validates offscreen/software Qt in an ARM64 reference VM. It does not establish a new appliance-image boot, physical camera/display operation or exact display-frame timing. SHA256 receipts establish byte consistency, not publisher authenticity.
