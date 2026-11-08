/*-
 * SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 DaOS contributors.
 * FreeBSD device-mode USB bulk transport. No ADB authentication is done here.
 */
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/cdefs.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libusb20.h>
#include <libusb20_desc.h>
#include "adb_packet.h"

#define RX_SIZE (64U * 1024U)
/* The pinned DWC OTG endpoint-1 FIFO holds 2048 bytes. Its final partial
 * packet arithmetic can schedule 2049..2559 bytes into that FIFO, then wait
 * for an IN completion which cannot occur until unrelated OUT traffic.
 * Bound native IN windows to the FIFO; full packet-aligned windows keep a
 * larger ADB payload contiguous without adding intermediate short packets.
 * RX capacity and the daemon's negotiated ADB maxdata remain unchanged. */
#define TX_SIZE 2048U
struct bridge {
    struct libusb20_device *device;
    struct libusb20_transfer *rx, *tx;
    uint8_t rxbuf[RX_SIZE];
    uint8_t txbuf[DAOS_ADB_HEADER_SIZE + DAOS_ADB_MAX_PAYLOAD];
    size_t rxlen, rxpos, rxcapacity, txhave, txneed, txoffset, txlength, txwindow;
    uint32_t payload;
    int rxready, txbusy, txdone, failed, closing;
};
static volatile sig_atomic_t interrupted;
static void on_signal(int signo) { interrupted = signo; }

static void
transfer_callback(struct libusb20_transfer *transfer)
{
    struct bridge *b = libusb20_tr_get_priv_sc0(transfer);
    unsigned status = libusb20_tr_get_status(transfer);
    if (b->closing) return;
    if (status == LIBUSB20_TRANSFER_START) {
        if (transfer == b->rx)
            libusb20_tr_setup_bulk(transfer, b->rxbuf, b->rxcapacity, 0);
        else
            libusb20_tr_setup_bulk(transfer, b->txbuf + b->txoffset, b->txlength, 0);
        libusb20_tr_submit(transfer);
    } else if (status == LIBUSB20_TRANSFER_COMPLETED) {
        if (transfer == b->rx) {
            b->rxlen = libusb20_tr_get_actual_length(transfer);
            b->rxpos = 0;
            b->rxready = 1;
        } else if (libusb20_tr_get_actual_length(transfer) == b->txlength) {
            b->txdone = 1;
        } else {
            fprintf(stderr, "daos-usb-adb: incomplete USB IN transfer\n");
            b->failed = 1;
        }
    } else {
        fprintf(stderr, "daos-usb-adb: USB %s transfer failed, status %u\n",
            transfer == b->rx ? "OUT" : "IN", status);
        b->failed = 1;
    }
}

static int
adb_endpoints(struct libusb20_device *device, uint8_t *in, uint8_t *out)
{
    struct libusb20_config *config;
    uint8_t index = libusb20_dev_get_config_index(device);
    unsigned matches = 0;
    if (index == 0xff) return (-1); /* Host has not selected configuration. */
    config = libusb20_dev_alloc_config(device, index);
    if (config == NULL) return (-1);
    for (unsigned i = 0; i < config->num_interface; ++i) {
        struct libusb20_interface *iface = &config->interface[i];
        uint8_t candidate_in = 0, candidate_out = 0;
        if (iface->desc.bInterfaceClass != 0xff ||
            iface->desc.bInterfaceSubClass != 0x42 ||
            iface->desc.bInterfaceProtocol != 1 ||
            iface->desc.bAlternateSetting != 0 || iface->num_endpoints != 2) continue;
        for (unsigned e = 0; e < iface->num_endpoints; ++e) {
            struct LIBUSB20_ENDPOINT_DESC_DECODED *ep = &iface->endpoints[e].desc;
            if ((ep->bmAttributes & 3) != 2 || (ep->bEndpointAddress & 15) == 0) continue;
            if (ep->bEndpointAddress & 0x80) candidate_in = ep->bEndpointAddress;
            else candidate_out = ep->bEndpointAddress;
        }
        if (candidate_in && candidate_out) {
            ++matches; *in = candidate_in; *out = candidate_out;
        }
    }
    free(config);
    return (matches == 1 ? 0 : -1);
}

static int
nonblock(int fd)
{
    int flags = fcntl(fd, F_GETFL);
    return (flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK));
}

static pid_t
spawn_daemon(char **command, int *input, int *output)
{
    int to_child[2], from_child[2];
    pid_t pid;
    if (pipe(to_child) < 0) return (-1);
    if (pipe(from_child) < 0) { close(to_child[0]); close(to_child[1]); return (-1); }
    pid = fork();
    if (pid == 0) {
        if (setsid() < 0 || dup2(to_child[0], STDIN_FILENO) < 0 ||
            dup2(from_child[1], STDOUT_FILENO) < 0) _exit(126);
        close(to_child[0]); close(to_child[1]); close(from_child[0]); close(from_child[1]);
        closefrom(3); /* In particular, the daemon must not retain the ugen fd. */
        execvp(command[0], command);
        perror("daos-usb-adb: exec daemon"); _exit(127);
    }
    close(to_child[0]); close(from_child[1]);
    if (pid < 0) { close(to_child[1]); close(from_child[0]); return (-1); }
    *input = to_child[1]; *output = from_child[0];
    return (pid);
}

static int
run_bridge(struct libusb20_device *device, uint8_t in, uint8_t out, char **command)
{
    struct bridge *b = calloc(1, sizeof(*b));
    int daemon_in = -1, daemon_out = -1, result = 1, status;
    pid_t child = -1;
    if (b == NULL) return (1);
    b->device = device;
    if (libusb20_dev_open(device, 2) != 0) {
        fprintf(stderr, "daos-usb-adb: cannot open peripheral; root/USB permissions required\n"); goto done;
    }
    b->rx = libusb20_tr_get_pointer(device, 0); b->tx = libusb20_tr_get_pointer(device, 1);
    if (libusb20_tr_open(b->rx, RX_SIZE, 1, out) != 0 ||
        libusb20_tr_open(b->tx, TX_SIZE, 1, in) != 0) {
        fprintf(stderr, "daos-usb-adb: device endpoints cannot support required transfer sizes\n"); goto done;
    }
    b->rxcapacity = libusb20_tr_get_max_total_length(b->rx);
    b->txwindow = libusb20_tr_get_max_total_length(b->tx);
    uint32_t packet = libusb20_tr_get_max_packet_length(b->tx);
    if (b->rxcapacity > RX_SIZE) b->rxcapacity = RX_SIZE;
    if (b->txwindow > TX_SIZE) b->txwindow = TX_SIZE;
    if (packet == 0 || b->rxcapacity == 0 || b->txwindow < packet ||
        packet > TX_SIZE || b->txwindow < DAOS_ADB_HEADER_SIZE) {
        fprintf(stderr, "daos-usb-adb: invalid endpoint transfer limits\n"); goto done;
    }
    /* Intermediate payload windows must not create a short USB packet/ZLP. */
    b->txwindow -= b->txwindow % packet;
    libusb20_tr_set_callback(b->rx, transfer_callback); libusb20_tr_set_priv_sc0(b->rx, b);
    libusb20_tr_set_callback(b->tx, transfer_callback); libusb20_tr_set_priv_sc0(b->tx, b);
    child = spawn_daemon(command, &daemon_in, &daemon_out);
    if (child < 0 || nonblock(daemon_in) < 0 || nonblock(daemon_out) < 0) {
        perror("daos-usb-adb: daemon pipes"); goto done;
    }
    b->txneed = DAOS_ADB_HEADER_SIZE;
    libusb20_tr_start(b->rx);
    while (!interrupted && !b->failed) {
        struct pollfd fds[3];
        ssize_t count;
        if (libusb20_dev_process(device) != 0) { b->failed = 1; break; }
        if (b->txdone) {
            b->txdone = 0;
            b->txoffset += b->txlength;
            if (b->txoffset < DAOS_ADB_HEADER_SIZE + b->payload) {
                b->txlength = DAOS_ADB_HEADER_SIZE + b->payload - b->txoffset;
                if (b->txlength > b->txwindow) b->txlength = b->txwindow;
                libusb20_tr_start(b->tx);
            } else {
                b->txbusy = 0; b->txhave = 0; b->txneed = DAOS_ADB_HEADER_SIZE;
            }
        }
        if (b->rxready && b->rxpos == b->rxlen) {
            b->rxready = 0; libusb20_tr_start(b->rx);
        }
        /* Backpressure is one USB OUT buffer and one complete ADB IN packet. */
        fds[0] = (struct pollfd){ libusb20_dev_get_fd(device), POLLIN | POLLOUT | POLLRDNORM | POLLWRNORM, 0 };
        fds[1] = (struct pollfd){ daemon_out, b->txbusy ? 0 : POLLIN, 0 };
        fds[2] = (struct pollfd){ daemon_in, b->rxready ? POLLOUT : 0, 0 };
        if (poll(fds, 3, 500) < 0 && errno != EINTR) { perror("daos-usb-adb: poll"); break; }
        if (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) break;
        if (fds[1].revents & POLLIN) {
            count = read(daemon_out, b->txbuf + b->txhave, b->txneed - b->txhave);
            if (count > 0) b->txhave += (size_t)count;
            else if (count == 0) break;
            else if (errno != EAGAIN && errno != EINTR) break;
            if (b->txhave == DAOS_ADB_HEADER_SIZE && b->txneed == DAOS_ADB_HEADER_SIZE) {
                if (daos_adb_payload_length(b->txbuf, &b->payload) != 0) {
                    fprintf(stderr, "daos-usb-adb: invalid daemon ADB framing\n"); break;
                }
                b->txneed += b->payload;
            }
            if (b->txhave == b->txneed) {
                b->txbusy = 1; b->txoffset = 0; b->txlength = DAOS_ADB_HEADER_SIZE;
                libusb20_tr_start(b->tx); /* Header and payload are separate USB writes. */
            }
        }
        if (fds[2].revents & POLLOUT) {
            count = write(daemon_in, b->rxbuf + b->rxpos, b->rxlen - b->rxpos);
            if (count > 0) b->rxpos += (size_t)count;
            else if (count < 0 && errno != EAGAIN && errno != EINTR) break;
        }
        if ((fds[1].revents | fds[2].revents) & (POLLERR | POLLHUP | POLLNVAL)) break;
        if (waitpid(child, &status, WNOHANG) == child) { child = -1; break; }
    }
    result = interrupted ? 0 : 1; /* Disconnect restarts via the outer supervisor. */
done:
    b->closing = 1;
    if (daemon_in >= 0) close(daemon_in);
    if (daemon_out >= 0) close(daemon_out);
    if (child > 0) {
        kill(child, SIGTERM);
        for (unsigned n = 0; n < 20; ++n) {
            if (waitpid(child, &status, WNOHANG) == child) { child = -1; break; }
            usleep(50000);
        }
        if (child > 0) { kill(child, SIGKILL); while (waitpid(child, &status, 0) < 0 && errno == EINTR) {} }
    }
    libusb20_dev_close(device);
    free(b);
    return (result);
}

static long
number(const char *value, long maximum)
{
    char *end;
    long n;
    errno = 0; n = strtol(value, &end, 0);
    if (errno || !*value || *end || n < 0 || n > maximum) {
        fprintf(stderr, "daos-usb-adb: invalid number %s\n", value); exit(2);
    }
    return (n);
}

int
main(int argc, char **argv)
{
    int vid = 0xffff, pid = 0xda05, bus = -1, address = -1, wait_mode = 0, list = 0, option;
    static const struct option options[] = {
        { "vid", required_argument, NULL, 'v' }, { "pid", required_argument, NULL, 'p' },
        { "bus", required_argument, NULL, 'b' }, { "address", required_argument, NULL, 'a' },
        { "wait", no_argument, NULL, 'w' }, { "list", no_argument, NULL, 'l' }, { NULL, 0, NULL, 0 }
    };
    while ((option = getopt_long(argc, argv, "v:p:b:a:wl", options, NULL)) != -1) {
        switch (option) {
        case 'v': vid = number(optarg, 65535); break;
        case 'p': pid = number(optarg, 65535); break;
        case 'b': bus = number(optarg, 255); break;
        case 'a': address = number(optarg, 255); break;
        case 'w': wait_mode = 1; break;
        case 'l': list = 1; break;
        default: return (2);
        }
    }
    if (!list && optind == argc) {
        fprintf(stderr, "usage: daos-usb-adb [--vid N --pid N] [--bus N --address N] [--wait] -- daemon [args...]\n"); return (2);
    }
    signal(SIGINT, on_signal); signal(SIGTERM, on_signal); signal(SIGHUP, on_signal);
    signal(SIGPIPE, SIG_IGN);
    do {
        struct libusb20_backend *backend = libusb20_be_alloc_default();
        struct libusb20_device *device = NULL, *selected = NULL;
        uint8_t in = 0, out = 0, candidate_in, candidate_out;
        unsigned matches = 0;
        if (!backend) { fprintf(stderr, "daos-usb-adb: no FreeBSD USB backend\n"); return (1); }
        while ((device = libusb20_be_device_foreach(backend, device)) != NULL) {
            struct LIBUSB20_DEVICE_DESC_DECODED *descriptor = libusb20_dev_get_device_desc(device);
            if (libusb20_dev_get_mode(device) != LIBUSB20_MODE_DEVICE ||
                descriptor->idVendor != vid || descriptor->idProduct != pid ||
                (bus >= 0 && libusb20_dev_get_bus_number(device) != bus) ||
                (address >= 0 && libusb20_dev_get_address(device) != address) ||
                adb_endpoints(device, &candidate_in, &candidate_out) != 0) continue;
            if (list) printf("ugen%u.%u device %04x:%04x ff/42/01 IN=%02x OUT=%02x\n",
                libusb20_dev_get_bus_number(device), libusb20_dev_get_address(device),
                vid, pid, candidate_in, candidate_out);
            selected = device; in = candidate_in; out = candidate_out; ++matches;
        }
        if (list) { libusb20_be_free(backend); return (matches ? 0 : 1); }
        if (matches > 1) {
            fprintf(stderr, "daos-usb-adb: ambiguous peripheral; select --bus and --address\n"); libusb20_be_free(backend); return (1);
        }
        if (selected) {
            fprintf(stderr, "daos-usb-adb: serving peripheral ugen%u.%u (native USB bulk)\n",
                libusb20_dev_get_bus_number(selected), libusb20_dev_get_address(selected));
            int result = run_bridge(selected, in, out, argv + optind);
            libusb20_be_free(backend); return (result);
        }
        libusb20_be_free(backend);
        if (!wait_mode) break;
        usleep(250000);
    } while (!interrupted);
    if (!interrupted) fprintf(stderr, "daos-usb-adb: no configured matching device-mode ADB peripheral\n");
    return (interrupted ? 0 : 1);
}
