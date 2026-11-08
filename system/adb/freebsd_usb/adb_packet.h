/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef DAOS_ADB_PACKET_H
#define DAOS_ADB_PACKET_H
#include <stdint.h>
#include <stddef.h>
#define DAOS_ADB_HEADER_SIZE 24U
#define DAOS_ADB_MAX_PAYLOAD (1024U * 1024U)
static inline uint32_t
daos_get_le32(const uint8_t *p)
{
    return ((uint32_t)p[0] | ((uint32_t)p[1] << 8) |
        ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24));
}
/* This checks framing only. Authentication belongs to the daemon. */
static inline int
daos_adb_payload_length(const uint8_t *header, uint32_t *length)
{
    *length = daos_get_le32(header + 12);
    return (daos_get_le32(header) == (daos_get_le32(header + 20) ^ UINT32_MAX) &&
        *length <= DAOS_ADB_MAX_PAYLOAD ? 0 : -1);
}
#endif
