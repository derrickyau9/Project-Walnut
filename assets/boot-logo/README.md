# DaOS Boot Logo

The supplied JPEG is preserved byte-for-byte. `DaOS-logo.png` is a lossless
encoding of its decoded pixels, with an opaque alpha channel. It remains
743x554: no resizing, crop, background removal or generated content was used.
Its RGB pixels are unchanged from the decoded JPEG. `provenance.json` binds both
files and records the pixel-level comparison.

For the pinned FreeBSD commit, install the PNG at
`/boot/images/DaOS-logo.png` and use these loader settings:

```conf
boot_mute="YES"
splash="/boot/images/DaOS-logo.png"
kern.vt.splash_cpu="0"
```

The ARM64 kernel must also include `device splash`. The current project DaOS
profile only includes ARM64 GENERIC, whose included `std.dev`/`std.arm64` do
not enable that device. The kernel needs a separate verified rebuild with
the splash device before claiming a kernel Boot Logo works.

This is the modern vt/UEFI PNG path. In the pinned `vt_core.c`, splash requires
the graphics console, the mute flag and four-byte pixels. The PNG therefore
uses RGBA, since a three-byte RGB PNG would be loaded but rejected by vt.
The loader transfers splash metadata through its common EFI `bi_load` path,
including the ARM64 EFI loader. A framebuffer of at least 743x554 is required;
the kernel centers the image without scaling it. Set a suitable board HDMI
mode independently. A physical Pi display must still be checked.

`splash_bmp_load` and `bitmap_load` belong to the older syscons bitmap path and
are not needed here. Do not call an X/Qt application startup image a kernel
Boot Logo. Asset conversion and source inspection do not constitute a boot test.

The exact source references are retained in `provenance.json`. No new rights
grant is asserted for the user-provided image.
