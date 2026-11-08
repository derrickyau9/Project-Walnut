# DaOS R14 UI on NetBSD

This layer uses the source-built NetBSD 11.1 world and X11 plus **upstream
NetBSD pkgsrc binary Qt/PyQt packages**. It is not an all-source Qt build. The
native DaOS camera is compiled separately from source and the current QML/Python
application sources are installed by `daos-camera --with-ui-source`.

The pinned repository is
https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/aarch64/11.0_2026Q3/ . Its
packages declare NetBSD 11.0/aarch64; the image targets the same NetBSD 11 stable
ABI at 11.1. Actual operation on 11.1 still requires a target boot test. The
official repository SHA512 index and summary were downloaded into `metadata/`;
their hashes are in `runtime.lock.json`. No independent binary-package signature
verification is claimed.

The selected roots are Python 3.12.14, PyQt 5.15.11nb12, Qt Quick Controls 2 and
Graphical Effects 5.15.19nb1, and DejaVu 2.37. Qt base is 5.15.19nb2. The full
recursive package closure contains 101 packages, 573,138,805 archive bytes and
2,077,245,371 declared installed bytes. Python 3.13 is an indirect dependency;
the camera explicitly runs `/usr/pkg/bin/python3.12`. The repository PyQt
version is separately pinned from the desktop pip requirements file.

`audit_runtime.py` verified the actual downloaded archives, package ABI fields,
36,244 payload files with no duplicates, and 995 ELF files. The 993 programs and
DSOs have NetBSD version notes. Two Python development `python.o` files are
AArch64 relocatable objects with no program headers or OS notes; their manifest
entries bind the exact archive SHA512, member, and checked `+BUILD_INFO` hash.
No target program was run by the audit.

## Build-host commands

Run from the project root using the F-backed Debian WSL filesystem. The
download cache below has already been filled and verified in this session.

```sh
python3 system/bsd-buildroot/packages/daos-ui/install.py resolve
python3 system/bsd-buildroot/packages/daos-ui/install.py fetch \
  --cache /home/dy09/daos-bsd-buildroot-ui/ui-packages --jobs 4
python3 system/bsd-buildroot/packages/daos-ui/install.py stage \
  --cache /home/dy09/daos-bsd-buildroot-ui/ui-packages \
  --base /home/dy09/daos-bsd-buildroot-ui/destdir \
  --destdir /home/dy09/daos-bsd-buildroot-ui/packages/overlay \
  --manifest /home/dy09/daos-bsd-buildroot-ui/packages/manifests/daos-ui.json
```

Use camera installation first, this layer second, and daos-core's aggregate
manifest last. `stage` is create-only for its payload and manifest: do not
re-run it into a partly populated UI overlay. It refuses absent source-world
libraries, Xorg, wsfb/keyboard/mouse modules, xauth or either legitimate NetBSD
dynamic-loader path. It then verifies every archive again and every installed
ELF, checks the full `REQUIRES` file closure, and emits the standard component
manifest. It never executes a target binary or package script on Linux.

## First boot and session

All 101 exact archives are also installed at `/usr/share/daos/ui-packages` for
offline native `pkg_add`. This adds approximately 547 MiB to the root image; use
the UI profile's enlarged root partition. `rc.d/daos_ui` requires mountall,
LOGIN and `daos_image_check`. The image's original file check runs before any
native package install scripts can modify state. `ui-finalize.py` verifies the
offline archive hashes, runs NetBSD `pkg_add`, checks all 101 package database
entries and saves `/var/db/daos-ui/finalized.json`. A matching completed
generation is skipped on later boots. Package post-install effects are
declared in the component manifest, not represented as a host-side success.

The supervisor requires root NetBSD/aarch64, the `daos-camera` account at
uid/gid 1000, a separately mounted `/var`, and a distinct photos filesystem at
`/var/DaOS/camera/photos` owned by uid/gid 1000 with mode 0700. It starts the
source-built Xorg as root and the camera app as uid 1000, using a fresh Xauthority
cookie and no X TCP listener. The app explicitly selects the source-built
`/usr/libexec/daos/hf-camerad`; it cannot silently select a Linux/FreeBSD daemon.

The source Xorg `wsfb` driver uses `/dev/ttyE0` and the preconfigured 32 bpp
firmware framebuffer (X depth 24). Keyboard and pointer input use wscons's
`wskbd` and `wsmouse` protocols and mux devices. Qt uses xcb and software Qt
Quick rendering. The native X input mouse driver handles absolute wscons
events, but actual QEMU USB tablet attachment and physical Pi USB support must
be checked during boot acceptance. A relative USB mouse is a valid emulator
fallback if the kernel does not attach the tablet.

Useful logs are `/var/log/daos-ui-packages.log`, `/var/log/daos-ui.log`, and
`/var/log/DaOS-Xorg.log`. Stop with `/etc/rc.d/daos_ui stop`; the supervisor lets
Qt shut down the native authority before terminating Xorg. Boot Logo decoding,
package staging and ELF validation do not constitute GUI, input, capture,
persistence or physical-board validation.

Primary interface references:

- https://man.netbsd.org/pkg_add.1
- https://man.netbsd.org/wsfb.4
- https://wiki.netbsd.org/tutorials/x11/how_to_use_wsfb_uefi_bios_framebuffer/
- https://raw.githubusercontent.com/NetBSD/xsrc/trunk/external/mit/xf86-input-keyboard/dist/src/bsd_kbd.c
- https://raw.githubusercontent.com/NetBSD/xsrc/trunk/external/mit/xf86-input-mouse/dist/src/bsd_mouse.c
- https://raw.githubusercontent.com/NetBSD/pkgsrc/trunk/pkgtools/pkg_install/files/lib/dewey.c
