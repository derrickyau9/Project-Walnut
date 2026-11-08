#!/bin/sh
# Called by the matching source tree's `make ... buildenv`.
set -eu
: "${DAOS_USB_SOURCE:?set the absolute freebsd_usb source directory}"
: "${DAOS_USB_OUTPUT:?set an empty/private output directory}"
: "${DAOS_KERNEL_OBJECT:?set the matching ARM64 kernel object directory}"
: "${DAOS_FREEBSD_SOURCE:?set the pinned FreeBSD source directory}"
test "$(uname -s)" = FreeBSD
test -r "$DAOS_KERNEL_OBJECT/opt_global.h"
test -r "$DAOS_FREEBSD_SOURCE/sys/dev/usb/template/usb_template.h"
mkdir -p "$DAOS_USB_OUTPUT/helper" "$DAOS_USB_OUTPUT/kmod"
# Private copies keep generated .o/.depend files out of tracked sources.
cp "$DAOS_USB_SOURCE/Makefile" "$DAOS_USB_SOURCE/daos_usb_adb.c" "$DAOS_USB_SOURCE/adb_packet.h" "$DAOS_USB_OUTPUT/helper/"
cp "$DAOS_USB_SOURCE/kmod/Makefile" "$DAOS_USB_SOURCE/kmod/daos_adb_template.c" "$DAOS_USB_OUTPUT/kmod/"
cp "$DAOS_USB_SOURCE/daos_ms_os.h" "$DAOS_USB_OUTPUT/daos_ms_os.h"
make -C "$DAOS_USB_OUTPUT/helper" all
make -C "$DAOS_USB_OUTPUT/kmod" SYSDIR="$DAOS_FREEBSD_SOURCE/sys" KERNBUILDDIR="$DAOS_KERNEL_OBJECT" all
file "$DAOS_USB_OUTPUT/helper/daos-usb-adb" "$DAOS_USB_OUTPUT/kmod/daos_adb_template.ko"
sha256 "$DAOS_USB_OUTPUT/helper/daos-usb-adb" "$DAOS_USB_OUTPUT/kmod/daos_adb_template.ko"
