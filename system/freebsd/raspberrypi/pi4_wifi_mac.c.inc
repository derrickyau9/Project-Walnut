/* DaOS Pi4 integration: use the firmware-provided board MAC, never the
 * example address embedded in a distributed NVRAM calibration file. */
static int
daos_wifi_mac(phandle_t node, uint8_t mac[6], int depth)
{
    if (depth > 32)
        return (0);
    for (; node > 0; node = OF_peer(node)) {
        if (ofw_bus_node_is_compatible(node, "brcm,bcm4329-fmac") ||
            ofw_bus_node_is_compatible(node, "brcm,bcm43455-fmac")) {
            int len = OF_getprop(node, "mac-address", mac, 6);
            if (len != 6)
                len = OF_getprop(node, "local-mac-address", mac, 6);
            if (len == 6 && !(mac[0] & 1) &&
                (mac[0] | mac[1] | mac[2] | mac[3] | mac[4] | mac[5]))
                return (1);
        }
        if (daos_wifi_mac(OF_child(node), mac, depth + 1))
            return (1);
    }
    return (0);
}

static void *
daos_pi4_nvram(const void *data, size_t size, uint32_t *length)
{
    const char *input = data;
    uint8_t mac[6];
    char *text;
    size_t i = 0, used = 0;
    void *result;

    if (size > 65536)
        return (NULL);
    if (!daos_wifi_mac(OF_peer(0), mac, 0)) {
        /* Firmware may omit WLAN MAC when its node was disabled for U-Boot's
         * SD routing. Use a stable locally-administered address from this
         * Pi4's unique firmware serial, preserving board identity on reboot. */
        char serial_text[32];
        uint64_t serial = 0;
        phandle_t root = OF_finddevice("/");
        int len = OF_getprop(root, "serial-number", serial_text,
            sizeof(serial_text));
        int n, digit;
        if (len < 2 || len > 17 ||
            !ofw_bus_node_is_compatible(root, "raspberrypi,4-model-b"))
            return (NULL);
        for (n = 0; n < len && serial_text[n] != '\0'; n++) {
            char ch = serial_text[n];
            if (ch >= '0' && ch <= '9') digit = ch - '0';
            else if (ch >= 'a' && ch <= 'f') digit = ch - 'a' + 10;
            else if (ch >= 'A' && ch <= 'F') digit = ch - 'A' + 10;
            else return (NULL);
            serial = (serial << 4) | digit;
        }
        if (serial == 0)
            return (NULL);
        mac[0] = 0x02;
        for (n = 1; n < 6; n++)
            mac[n] = (serial >> ((5 - n) * 8)) & 0xff;
    }
    text = malloc(size + 64, M_BRCMFMAC, M_WAITOK | M_ZERO);
    while (i < size) {
        size_t start = i, key;
        while (i < size && input[i] != '\n')
            i++;
        key = start;
        while (key < i && (input[key] == ' ' || input[key] == '\t'))
            key++;
        if (i - key < 8 || memcmp(input + key, "macaddr=", 8) != 0) {
            memcpy(text + used, input + start, i - start);
            used += i - start;
            text[used++] = '\n';
        }
        if (i < size)
            i++;
    }
    used += snprintf(text + used, size + 64 - used,
        "macaddr=%02x:%02x:%02x:%02x:%02x:%02x\n",
        mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    result = brcmf_nvram_parse(text, used, length);
    free(text, M_BRCMFMAC);
    return (result);
}
