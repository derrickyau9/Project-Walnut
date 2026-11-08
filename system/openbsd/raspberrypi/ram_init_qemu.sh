#!/bin/sh
# OFFLINE QEMU PROTOTYPE ONLY. No physical-card mount, Wi-Fi setup or installation.
PATH=/sbin:/bin:/usr/bin:/usr/sbin:/
export PATH
umask 077
[ ! -f /var/run/daos-openbsd.ready ] || exit 0
fail() { echo "DAOS_OPENBSD_BOOT_REFUSED: $*"; exit 1; }
mounts=$(mount) || fail 'mount query'
case "$mounts" in
    '/dev/rd0a on / type ffs (local)'|'/dev/rd0a on / type ffs (local, wxallowed)') ;;
    'root_device on / type ffs (local, read-only)')
        # The kernel's initial generic label is replaced by mount -u, exactly
        # as the signed installer .profile does. Bind it to the resident root.
        dmesg | grep '^root on rd0a swap on rd0b dump on rd0b$' >/dev/null || fail 'initial root is not rd0a'
        ;;
    *) fail 'only resident rd0a may be mounted';;
esac
nonce=$(cat /etc/daos/probe-nonce) || fail 'nonce'
case "$nonce" in *[!a-f0-9]*|'') fail 'nonce format';; esac
[ "${#nonce}" -eq 32 ] || fail 'nonce length'
product=$(sysctl -n hw.product 2>/dev/null)
case "$product" in QEMU*) ;; *) fail 'this image is bound to isolated QEMU';; esac
mount -u /dev/rd0a / || fail 'resident root update'
case "$(mount)" in
    '/dev/rd0a on / type ffs (local)'|'/dev/rd0a on / type ffs (local, wxallowed)') ;;
    *) fail 'resident mount update changed topology';;
esac
mkdir -p /var/run /var/log /etc/ssh || fail 'RAM directories'
# Stock SMALL_KERNEL has no registered BCM hardware watchdog. This bounded
# userland reboot is deliberately labelled, and cannot recover a global hang.
(
    trap '' HUP
    sleep 120
    echo DAOS_OPENBSD_USERLAND_DEADLINE_REBOOT >/dev/console
    reboot -n
) &
echo "$!" >/var/run/daos-openbsd-deadline.pid
echo 'DAOS_OPENBSD_DEADLINE type=userland seconds=120 hardware_watchdog=unavailable'
/usr/bin/ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key || fail 'ephemeral RAM host key'
/usr/sbin/sshd -t -f /etc/ssh/sshd_config || fail 'SSH configuration'
ifconfig vio0 inet 10.0.2.15 netmask 255.255.255.0 || fail 'isolated QEMU interface'
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
echo "$nonce" >/var/run/daos-openbsd.ready
echo "DAOS_OPENBSD_RAM_READY nonce=$nonce access=public-key-only image=QEMU_ONLY"
