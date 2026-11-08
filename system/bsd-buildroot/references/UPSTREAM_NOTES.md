# NetBSD source and target notes

The release inputs are the five immutable NetBSD 11.1 source sets. The
candidate lock contains actual SHA512 values from the official HTTPS checksum
file. `verify_sources.py` verified the official clear-signed release manifest
with the release announcement's Security Officer key and checked all five
source hashes against it. This check verifies metadata, not the source archive
bytes; the backend must hash every downloaded archive before extraction. The
key trust basis is the official HTTPS publication, not an independently
attested PGP trust chain. The source-set directory has no `SHA512.sig`; use the
release-wide signed manifest instead.

- Release: https://www.netbsd.org/releases/formal-11/NetBSD-11.1.html
- Source sets: https://cdn.netbsd.org/pub/NetBSD/NetBSD-11.1/source/sets/
- Checksums: https://cdn.netbsd.org/pub/NetBSD/NetBSD-11.1/source/sets/SHA512
- Signed manifest: https://cdn.netbsd.org/pub/NetBSD/security/hashes/NetBSD-11.1_hashes.asc
- Key: https://cdn.netbsd.org/pub/NetBSD/security/PGP/security-officer@netbsd.org.asc
- Build guide: https://www.netbsd.org/docs/guide/en/chap-build.html

The archive paths are rooted at `usr/src` for src/syssrc/gnusrc/sharesrc and
`usr/xsrc` for xsrc. Keep xsrc optional for the initial console/core image.
`build.sh -U -m evbarm -a aarch64` is the upstream cross-build entry point.
Private `MAKECONF`, object, tools, destination and release directories avoid
altering the host configuration. The two world configurations retain the C++
runtime, omit target compiler commands and documentation, and omit X11 pending
the separately tracked UI layer. `MKREPRO` and the fixed timestamp request
upstream reproducibility support; they are not proof of byte-identical builds.

The copied `netbsd-11/` files are research references from the moving upstream
release branch, not the pinned build inputs. Their original license headers are
retained. Compile only the archives in the actual source lock. Reference URLs
and file hashes are recorded in `research-source-files.json`.

## Pi 4

`DAOS_RPI4` initially inherits `GENERIC64` and adds the existing upstream
`bcm2838pcie` and `bcm2838rng` FDT attachments. Stock GENERIC64 has xHCI support
but does not explicitly select these Pi 4 host drivers. The PCIe driver is
needed for the onboard VL805 USB path when booting through the native FDT path.
Use a matching Pi firmware/DTB and test HDMI, USB and storage on physical Pi 4.
UEFI/ACPI and native Pi FDT boot paths must not be assumed interchangeable.

- Pi documentation: https://wiki.netbsd.org/ports/evbarm/raspberry_pi/
- Generic config: https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/arch/evbarm/conf/GENERIC64
- Driver declarations: https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/arch/arm/broadcom/files.bcm2835

## Native kernel Boot Logo

This uses NetBSD wscons/genfb, not FreeBSD vt or loader.conf. `GENERIC64`
provides `genfb* at fdt?` and `wsdisplay* at wsemuldisplaydev?`. `files.wsfb`
makes genfb depend on the splash attribute. Add `options SPLASHSCREEN` and
`makeoptions SPLASHSCREEN_IMAGE="${KERNCONFDIR}/daos-boot-logo.png"`, then copy
the reviewed asset to that private configuration directory before compiling.
No additional `device splash` line is used. STB_IMAGE is selected by the
SPLASHSCREEN dependency; GENFB_DISABLE_TEXT is defined in genfbvar.h.

`splash.mk` embeds the image with the AArch64 objcopy format, `init_main.c`
registers the bytes, and genfb renders them on attachment. Verbose, debug,
single-user and user-configuration boot flags preserve the diagnostic console.
The wsdisplay `WSDISPLAYIO_SSPLASH` ioctl can subsequently toggle the splash;
an X server takes ownership using the wsdisplay graphics-mode interface.

The supplied PNG is 743 x 554, 8-bit RGBA with opaque alpha. The host smoke test
executes upstream's unmodified STBI decoder, tests the exact STBI_rgb request
used by splash.c, and compares the decoded RGBA hash with the reviewed asset
provenance. It passes without conversion. NetBSD centers the image without
scaling and rejects a framebuffer smaller than the image. The renderer supports
8, 16 and 32 bpp; use a 32 bpp framebuffer of at least 743 x 554. A 24 bpp
framebuffer is unsupported by this splash renderer. This does not prove that
the target framebuffer attaches or that the splash boots on either target.

- https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/dev/wsfb/files.wsfb
- https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/dev/splash/files.splash
- https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/dev/splash/splash.mk
- https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/dev/splash/splash.c
- https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/dev/wsfb/genfb.c
- https://raw.githubusercontent.com/NetBSD/src/netbsd-11/sys/kern/init_main.c
