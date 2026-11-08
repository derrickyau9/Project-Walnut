/* SPDX-License-Identifier: BSD-2-Clause */
/* Deterministic event mock. It does not emulate a USB controller or an ABI. */
#include <sys/cdefs.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
#include <libusb20.h>
#include <libusb20_desc.h>
struct libusb20_transfer {
    void *priv, *buffer;
    uint32_t length, actual, maximum;
    uint8_t endpoint, status;
    int pending;
    libusb20_tr_callback_t *callback;
};
struct libusb20_device { struct libusb20_transfer transfers[2]; int event[2], rxcount; };
struct libusb20_backend { struct libusb20_device device; };
struct libusb20_backend *libusb20_be_alloc_default(void) {
    struct libusb20_backend *b = calloc(1, sizeof(*b));
    if (!b || pipe(b->device.event)) abort();
    if (write(b->device.event[1], "x", 1) != 1) abort();
    return b;
}
void libusb20_be_free(struct libusb20_backend *b) { close(b->device.event[0]); close(b->device.event[1]); free(b); }
struct libusb20_device *libusb20_be_device_foreach(struct libusb20_backend *b, struct libusb20_device *d) { return d ? NULL : &b->device; }
uint8_t libusb20_dev_get_mode(struct libusb20_device *d) { (void)d; return getenv("MOCK_HOST") ? LIBUSB20_MODE_HOST : LIBUSB20_MODE_DEVICE; }
uint8_t libusb20_dev_get_address(struct libusb20_device *d) { (void)d; return 2; }
uint8_t libusb20_dev_get_bus_number(struct libusb20_device *d) { (void)d; return 1; }
uint8_t libusb20_dev_get_config_index(struct libusb20_device *d) { (void)d; return getenv("MOCK_UNCONFIGURED") ? 0xff : 0; }
struct LIBUSB20_DEVICE_DESC_DECODED *libusb20_dev_get_device_desc(struct libusb20_device *d) {
    static struct LIBUSB20_DEVICE_DESC_DECODED desc;
    (void)d; desc.idVendor = 0xffff; desc.idProduct = 0xda05; return &desc;
}
struct libusb20_config *libusb20_dev_alloc_config(struct libusb20_device *d, uint8_t index) {
    struct allocation { struct libusb20_config config; struct libusb20_interface interface; struct libusb20_endpoint ep[2]; } *a;
    (void)d; (void)index;
    a = calloc(1, sizeof(*a)); if (!a) abort();
    a->config.interface = &a->interface; a->config.num_interface = 1;
    a->interface.endpoints = a->ep; a->interface.num_endpoints = 2;
    a->interface.desc.bInterfaceClass = getenv("MOCK_WRONG_INTERFACE") ? 8 : 255;
    a->interface.desc.bInterfaceSubClass = 0x42; a->interface.desc.bInterfaceProtocol = 1;
    a->ep[0].desc.bEndpointAddress = 0x81; a->ep[1].desc.bEndpointAddress = 1;
    a->ep[0].desc.bmAttributes = a->ep[1].desc.bmAttributes = 2;
    return &a->config;
}
int libusb20_dev_open(struct libusb20_device *d, uint16_t n) { (void)d; (void)n; return 0; }
int libusb20_dev_close(struct libusb20_device *d) { (void)d; return 0; }
int libusb20_dev_get_fd(struct libusb20_device *d) { return d->event[0]; }
struct libusb20_transfer *libusb20_tr_get_pointer(struct libusb20_device *d, uint16_t index) { return &d->transfers[index]; }
int libusb20_tr_open(struct libusb20_transfer *t, uint32_t max, uint32_t frames, uint8_t endpoint) {
    (void)frames; t->maximum = max; t->endpoint = endpoint; return 0;
}
uint32_t libusb20_tr_get_max_total_length(struct libusb20_transfer *t) { return t->maximum; }
uint32_t libusb20_tr_get_max_packet_length(struct libusb20_transfer *t) { (void)t; return 512; }
void libusb20_tr_set_callback(struct libusb20_transfer *t, libusb20_tr_callback_t *cb) { t->callback = cb; }
void libusb20_tr_set_priv_sc0(struct libusb20_transfer *t, void *p) { t->priv = p; }
void *libusb20_tr_get_priv_sc0(struct libusb20_transfer *t) { return t->priv; }
uint8_t libusb20_tr_get_status(struct libusb20_transfer *t) { return t->status; }
uint32_t libusb20_tr_get_actual_length(struct libusb20_transfer *t) { return t->actual; }
void libusb20_tr_setup_bulk(struct libusb20_transfer *t, void *p, uint32_t size, uint32_t timeout) { (void)timeout; t->buffer = p; t->length = size; }
void libusb20_tr_submit(struct libusb20_transfer *t) { t->pending = 1; }
void libusb20_tr_start(struct libusb20_transfer *t) { t->status = LIBUSB20_TRANSFER_START; t->callback(t); }
int libusb20_dev_process(struct libusb20_device *d) {
    for (unsigned n = 0; n < 2; ++n) {
        struct libusb20_transfer *t = &d->transfers[n];
        if (!t->pending) continue;
        if (t->endpoint == 1) {
            const char *chunks[] = {"HOST-", "fragment"};
            if (d->rxcount == 2) continue;
            t->actual = strlen(chunks[d->rxcount]);
            memcpy(t->buffer, chunks[d->rxcount++], t->actual);
        } else {
            /* Confirmed pinned DWC device-IN terminal FIFO starvation.
             * An unrelated OUT interrupt can refill the final 1..511 bytes;
             * this fixture deliberately provides no later host traffic. */
            uint32_t remainder = t->length % 2048;
            if (getenv("MOCK_DWC_FIFO") && t->length > 2048 &&
                remainder > 0 && remainder < 512) continue;
            FILE *capture = fopen(getenv("MOCK_CAPTURE"), "ab");
            uint8_t length[] = {t->length, t->length >> 8, t->length >> 16, t->length >> 24};
            if (!capture || fwrite(length, 1, 4, capture) != 4 ||
                fwrite(t->buffer, 1, t->length, capture) != t->length || fclose(capture)) abort();
            t->actual = t->length;
        }
        t->pending = 0; t->status = LIBUSB20_TRANSFER_COMPLETED; t->callback(t);
    }
    return 0;
}
