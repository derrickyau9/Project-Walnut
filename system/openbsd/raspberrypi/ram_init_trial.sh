#!/bin/sh
# PRIVATE ONE-TRIAL PAYLOAD ONLY. No installer, physical mount, FAT write or USB.
PATH=/sbin:/bin:/usr/bin:/usr/sbin:/
export PATH
LC_ALL=C
export LC_ALL
umask 077
fail() { echo "DAOS_OPENBSD_TRIAL_REFUSED: $*" >/dev/console; exit 1; }
[ "$(/bin/arch)" = OpenBSD.aarch64 ] || fail 'OpenBSD ARM64 required'
[ "$(sysctl -n hw.machine)" = arm64 ] || fail 'machine differs'
# OpenBSD may preserve the prior kernel message buffer on warm reset. Keep only
# the last kernel banner and its following lines before accepting root/FDT proof.
bootlog=$(dmesg | sed -n '/^OpenBSD /h; /^OpenBSD /!H; ${g;p;}') || fail 'current boot log'
echo "$bootlog" | grep '^OpenBSD 8\.0 ' >/dev/null || fail 'current OpenBSD8 banner absent'
echo "$bootlog" | grep '^root on rd0a swap on rd0b dump on rd0b$' >/dev/null || fail 'current root is not rd0a'
mounts=$(mount) || fail 'mount query'
case "$mounts" in
    'root_device on / type ffs (local, read-only)')
        ;;
    '/dev/rd0a on / type ffs (local)'|'/dev/rd0a on / type ffs (local, wxallowed)') ;;
    *) fail 'only resident rd0a may be mounted';;
esac
mount -u /dev/rd0a / || fail 'resident root update'
ram_mount=$(mount) || fail 'resident mount query'
case "$ram_mount" in
    '/dev/rd0a on / type ffs (local)'|'/dev/rd0a on / type ffs (local, wxallowed)') ;;
    *) fail 'resident mount update changed topology';;
esac
nonce=$(cat /etc/daos/probe-nonce) || fail 'nonce'
case "$nonce" in *[!a-f0-9]*|'') fail 'nonce characters';; esac
[ "${#nonce}" -eq 32 ] || fail 'nonce length'
[ "$(cat /etc/daos/trial-kind)" = OPENBSD8_PI4_RAM_WIFI_ONLY ] || fail 'private trial class'
[ ! -f /var/run/daos-trial-started ] || exit 0
mkdir -p /var/run /var/log /var/db/dhcpleased || fail 'resident directories'
echo "$nonce" >/var/run/daos-trial-started
# This is a userland fallback, not hardware watchdog coverage. It begins before
# any userland association request and is never extended by SSH or DHCP success.
(
    trap '' HUP
    sleep 120
    if [ "$(mount)" = "$ram_mount" ]; then
        echo 'DAOS_OPENBSD_DEADLINE type=userland seconds=120 action=quick-reboot' >/dev/console
        reboot -nq
    else
        echo 'DAOS_OPENBSD_DEADLINE_REFUSED unexpected-filesystem' >/dev/console
    fi
) &
echo "$!" >/var/run/daos-openbsd-deadline.pid
echo "DAOS_OPENBSD_TRIAL_BEGIN nonce=$nonce deadline=userland-120s hardware_watchdog=unavailable" >/dev/console
product=$(sysctl -n hw.product 2>/dev/null)
case "$product" in 'Raspberry Pi 4 Model B'*) ;; *) fail 'Pi4 product differs';; esac
echo "$bootlog" | grep '^"daos,openbsd-dtb-001" at mainbus0 not configured$' >/dev/null || fail 'current provenance FDT leaf absent'
for file in /etc/daos/test-ssid /etc/daos/test-psk /etc/ssh/authorized_keys /etc/ssh/ssh_host_ed25519_key; do
    [ -f "$file" ] && [ ! -L "$file" ] && [ -s "$file" ] || fail 'ordinary opaque trial input required'
done
[ -c /dev/bpf ] || fail 'BPF device absent'
/usr/sbin/sshd -t -f /etc/ssh/sshd_config || fail 'SSH configuration'
/usr/sbin/sshd -D -e -f /etc/ssh/sshd_config >/var/log/daos-sshd.log 2>&1 &
listener=$!
echo "$listener" >/var/run/daos-sshd.pid
attempt=0
listening=NO
while [ "$attempt" -lt 10 ]; do
    kill -0 "$listener" 2>/dev/null || fail 'SSH listener exited'
    if grep '^Server listening on 0\.0\.0\.0 port 22\.[[:space:]]*$' /var/log/daos-sshd.log >/dev/null; then
        listening=YES
        break
    fi
    sleep 1
    attempt=$((attempt + 1))
done
[ "$listening" = YES ] || fail 'SSH listener readiness deadline'
/sbin/resolvd >/var/log/daos-resolvd.log 2>&1 || fail 'resolver daemon'
/sbin/dhcpleased -d >/var/log/daos-dhcpleased.log 2>&1 &
echo "$!" >/var/run/daos-dhcpleased.pid
# Bring up the independent wired path before the Wi-Fi request. A missing or
# failed Wi-Fi attachment must not prevent diagnostics over connected Ethernet.
wired=absent
if ifconfig bse0 >/dev/null 2>&1; then
    wired=request_failed
    if ifconfig bse0 inet autoconf up; then wired=DHCP_requested; fi
fi
ssid=$(cat /etc/daos/test-ssid) || fail 'opaque SSID input'
psk=$(cat /etc/daos/test-psk) || fail 'opaque PSK input'
case "$ssid" in *[![:print:]]*|'') fail 'SSID format';; esac
[ "${#ssid}" -le 32 ] || fail 'SSID length'
case "$psk" in *[![:print:]]*) fail 'PSK format';; esac
[ "${#psk}" -ge 8 ] && [ "${#psk}" -le 63 ] || fail 'PSK length'
# The command and its diagnostic file are never echoed or uploaded. Arguments
# are quoted data, not sourced shell code. Only the task AP may be provisioned.
wifi=interface_absent
if ifconfig bwfm0 >/dev/null 2>&1; then
    wifi=configuration_failed
    if ifconfig bwfm0 nwid "$ssid" wpakey "$psk" up >/var/log/private-network-setup.log 2>&1; then
        wifi=DHCP_request_failed
        if ifconfig bwfm0 inet autoconf; then wifi=DHCP_requested; fi
    fi
fi
unset ssid psk
[ "$(mount)" = "$ram_mount" ] || fail 'filesystem topology changed'
echo "$nonce" >/var/run/daos-openbsd.ready
echo "DAOS_OPENBSD_ACCESS_REQUESTED nonce=$nonce auth=public-key-only wifi=$wifi wired=$wired" >/dev/console
echo 'DAOS_OPENBSD_STATUS association_and_DHCP_unproven; collect_authenticated_readonly_logs' >/dev/console
