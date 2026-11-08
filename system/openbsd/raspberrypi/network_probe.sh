#!/bin/sh
# Read-only collection for a separately prepared OpenBSD RAM trial.
# It never configures an interface, mounts a filesystem, or starts a daemon.
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
LC_ALL=C
export LC_ALL
set -u

refuse() { echo "DAOS_OPENBSD_NETWORK_LOGS_REFUSED: $*" >&2; exit 1; }
[ "$#" -eq 2 ] || refuse 'expected nonce and interface'
trial=$1
interface=$2
[ "${#trial}" -eq 32 ] || refuse 'invalid nonce length'
case "$trial" in *[!0-9a-f]*) refuse 'invalid nonce characters';; esac
case "$interface" in bwfm0|vio0) ;; *) refuse 'unexpected interface';; esac
[ "$(/bin/arch)" = OpenBSD.aarch64 ] || refuse 'OpenBSD ARM64 required'
[ "$(/sbin/sysctl -n hw.machine)" = arm64 ] || refuse 'ARM64 required'
[ -f /etc/daos/probe-nonce ] && [ ! -L /etc/daos/probe-nonce ] || refuse 'ordinary RAM nonce marker required'
[ "$(/bin/cat /etc/daos/probe-nonce)" = "$trial" ] || refuse 'RAM nonce differs'

mounts=$(/sbin/mount) || refuse 'cannot read mount table'
case "$mounts" in *'
'*) refuse 'additional filesystem mounted';; esac
case "$mounts" in '/dev/rd0a on / type ffs ('*')') ;; *) refuse 'only rd0a RAM root may be mounted';; esac

echo "DAOS_OPENBSD_NETWORK_LOGS_BEGIN $trial $interface"
echo '--- kernel ---'
/bin/arch
/sbin/sysctl kern.version kern.boottime hw.model
echo '--- mounts ---'
echo "$mounts"
/bin/df -k /
echo '--- interface (key-bearing lines omitted) ---'
/sbin/ifconfig "$interface" | /usr/bin/sed '/wpakey/d; /wpapsk/d; /password/d; /passphrase/d; /psk=/d; 161,$d'
echo '--- IPv4 routes ---'
/sbin/route -n show -inet | /usr/bin/sed -n '1,160p'
echo '--- dmesg (first 600 lines) ---'
/sbin/dmesg | /usr/bin/sed -n '1,600p'

[ "$(/sbin/mount)" = "$mounts" ] || refuse 'mount topology changed during collection'
[ -f /etc/daos/probe-nonce ] && [ ! -L /etc/daos/probe-nonce ] || refuse 'RAM nonce marker changed'
[ "$(/bin/cat /etc/daos/probe-nonce)" = "$trial" ] || refuse 'RAM nonce changed'
echo "DAOS_OPENBSD_NETWORK_LOGS_END $trial"
