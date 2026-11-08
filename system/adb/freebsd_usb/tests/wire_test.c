/* SPDX-License-Identifier: BSD-2-Clause */
#include <assert.h>
#include <string.h>
#include "daos_ms_os.h"
#include "adb_packet.h"
int main(void)
{
    uint16_t size = 999;
    uint32_t length;
    uint8_t header[24] = { 'C', 'N', 'X', 'N' };
    for (unsigned i = 0; i < 4; ++i) header[20+i] = header[i] ^ 0xff;
    assert(daos_adb_payload_length(header, &length) == 0 && length == 0);
    header[14] = 0x10;
    assert(daos_adb_payload_length(header, &length) == 0 && length == 1024*1024);
    header[12] = 1;
    assert(daos_adb_payload_length(header, &length) != 0);
    header[12] = header[14] = 0; header[20] ^= 1;
    assert(daos_adb_payload_length(header, &length) != 0);
    assert(daos_ms_vendor_descriptor(0xc0, 0xda, 4, 0, &size) == daos_ms_compatible_id && size == 40);
    assert(daos_ms_vendor_descriptor(0xc1, 0xda, 5, 0, &size) == daos_ms_extended_properties && size == 146);
    assert(daos_ms_vendor_descriptor(0xc0, 0xda, 5, 0, &size) == daos_ms_extended_properties && size == 146);
    const uint8_t wrong[][4] = {{0x40,0xda,4,0},{0xc0,0xdb,4,0},{0xc0,0xda,3,0},{0xc0,0xda,4,1},{0xc1,0xda,4,0}};
    for (unsigned i = 0; i < sizeof(wrong)/sizeof(wrong[0]); ++i) {
        assert(daos_ms_vendor_descriptor(wrong[i][0], wrong[i][1], wrong[i][2], wrong[i][3], &size) == NULL && size == 0);
    }
    assert(sizeof(daos_ms_os_string) == 18 && daos_ms_os_string[16] == 0xda);
    return 0;
}
