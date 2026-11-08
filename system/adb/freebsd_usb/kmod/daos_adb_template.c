/*-
 * SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 DaOS contributors.
 *
 * The template API/layout follows FreeBSD usb_template_mtp.c by Hans Petter
 * Selasky and the FreeBSD Foundation, under its BSD-2-Clause license.
 */
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/kernel.h>
#include <sys/module.h>
#include <sys/errno.h>
#include <sys/sysctl.h>
#include <sys/lock.h>
#include <sys/mutex.h>
#include <sys/condvar.h>
#include <sys/sx.h>
#include <sys/bus.h>
#include <dev/usb/usb.h>
#include <dev/usb/usbdi.h>
#include <dev/usb/usb_core.h>
#include <dev/usb/usb_dynamic.h>
#include <dev/usb/usb_util.h>
#include <dev/usb/template/usb_template.h>
#include "daos_ms_os.h"

#define DAOS_ADB_TEMPLATE_INDEX 0xDA05
/* Deliberately unallocated development identity, never a Google/RPi VID. */
static int vendor_id = 0xffff;
static int product_id = 0xda05;
static char serial_number[64] = "DaOS-USB-ADB-DEVELOPMENT";
TUNABLE_INT("hw.usb.daos_adb.vendor_id", &vendor_id);
TUNABLE_INT("hw.usb.daos_adb.product_id", &product_id);
TUNABLE_STR("hw.usb.daos_adb.serial", serial_number, sizeof(serial_number));

enum { STR_LANG, STR_MANUFACTURER, STR_PRODUCT, STR_SERIAL, STR_INTERFACE };
static struct usb_string_descriptor manufacturer, product, serial, interface;
static usb_temp_setup_by_index_t *previous_setup;

static const struct usb_temp_packet_size bulk_mps = {
    .mps[USB_SPEED_FULL] = 64, .mps[USB_SPEED_HIGH] = 512,
};
static const struct usb_temp_endpoint_desc adb_in = {
    .pPacketSize = &bulk_mps, .bEndpointAddress = UE_DIR_IN | 1, .bmAttributes = UE_BULK,
};
static const struct usb_temp_endpoint_desc adb_out = {
    .pPacketSize = &bulk_mps, .bEndpointAddress = UE_DIR_OUT | 1, .bmAttributes = UE_BULK,
};
static const struct usb_temp_endpoint_desc *adb_endpoints[] = { &adb_in, &adb_out, NULL };
static const struct usb_temp_interface_desc adb_interface = {
    .ppEndpoints = adb_endpoints,
    .bInterfaceClass = 0xff, .bInterfaceSubClass = 0x42, .bInterfaceProtocol = 1,
    .iInterface = STR_INTERFACE,
};
static const struct usb_temp_interface_desc *adb_interfaces[] = { &adb_interface, NULL };
static const struct usb_temp_config_desc adb_config = {
    .ppIfaceDesc = adb_interfaces, .bmAttributes = UC_SELF_POWERED, .bMaxPower = 0,
};
static const struct usb_temp_config_desc *adb_configs[] = { &adb_config, NULL };

static const void *
adb_get_string(uint16_t language, uint8_t index)
{
    static const uint8_t languages[] = { 4, UDESC_STRING, 9, 4 };
    if (index == 0xee) return (daos_ms_os_string);
    if (index == STR_LANG) return (languages);
    if (language != 0x0409) return (NULL);
    switch (index) {
    case STR_MANUFACTURER: return (&manufacturer);
    case STR_PRODUCT: return (&product);
    case STR_SERIAL: return (&serial);
    case STR_INTERFACE: return (&interface);
    default: return (NULL);
    }
}

static const void *
adb_get_vendor(const struct usb_device_request *request, uint16_t *length)
{
    return (daos_ms_vendor_descriptor(request->bmRequestType, request->bRequest,
        UGETW(request->wIndex), UGETW(request->wValue), length));
}

static struct usb_temp_device_desc adb_device = {
    .getStringDesc = adb_get_string, .getVendorDesc = adb_get_vendor,
    .ppConfigDesc = adb_configs, .bcdDevice = 0x0100,
    .bDeviceClass = 0, .bDeviceSubClass = 0, .bDeviceProtocol = 0,
    .iManufacturer = STR_MANUFACTURER, .iProduct = STR_PRODUCT, .iSerialNumber = STR_SERIAL,
};

static usb_error_t
adb_setup(struct usb_device *device, uint16_t index)
{
    if (index == DAOS_ADB_TEMPLATE_INDEX) return (usb_temp_setup(device, &adb_device));
    return (previous_setup(device, index));
}

static int
adb_event(module_t module __unused, int event, void *argument __unused)
{
    switch (event) {
    case MOD_LOAD:
        if (vendor_id <= 0 || vendor_id > 0xffff || product_id <= 0 || product_id > 0xffff ||
            serial_number[0] == '\0' || usb_temp_setup_by_index_p == NULL) return (EINVAL);
        adb_device.idVendor = vendor_id; adb_device.idProduct = product_id;
        usb_make_str_desc(&manufacturer, sizeof(manufacturer), "DaOS Development");
        usb_make_str_desc(&product, sizeof(product), "DaOS BSD USB ADB (development identity)");
        usb_make_str_desc(&serial, sizeof(serial), serial_number);
        usb_make_str_desc(&interface, sizeof(interface), "Android ADB Interface");
        previous_setup = usb_temp_setup_by_index_p;
        usb_temp_setup_by_index_p = adb_setup;
        printf("DaOS USB ADB template registered at %u, development VID:PID %04x:%04x; hardware enumeration unverified\n",
            DAOS_ADB_TEMPLATE_INDEX, vendor_id, product_id);
        return (0);
    case MOD_UNLOAD:
        /* Descriptor pointers can outlive control requests. Reboot to remove. */
        return (EBUSY);
    default: return (EOPNOTSUPP);
    }
}
static moduledata_t adb_module = { "daos_adb_template", adb_event, NULL };
DECLARE_MODULE(daos_adb_template, adb_module, SI_SUB_KLD, SI_ORDER_ANY);
MODULE_DEPEND(daos_adb_template, usb, 1, 1, 1);
MODULE_DEPEND(daos_adb_template, usb_template, 1, 1, 1);
MODULE_VERSION(daos_adb_template, 1);
