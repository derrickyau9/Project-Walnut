#!/rescue/sh
# init_script hook: starts before /etc/rc and is re-entered by patched init.
# This hook must return promptly even with no USB cable or broken /var.
PATH=/rescue:/sbin:/bin:/usr/sbin:/usr/bin
# ldconfig has not run and /var/run/ld-elf.so.hints may be unavailable.
LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib
export PATH LD_LIBRARY_PATH
umask 077
RAM=/DaOS-recovery-ram
PYTHON=/usr/local/bin/python3.12
SUPERVISOR=/usr/local/libexec/DaOS-recovery/early_adb.py

fail() { printf 'DaOS early USB recovery unavailable: %s\n' "$*" >&2; exit 0; }
[ -d /dev ] && [ ! -L /dev ] || fail 'invalid /dev mountpoint'
[ -d "$RAM" ] && [ ! -L "$RAM" ] || fail 'missing recovery RAM mountpoint'

# init_script runs before init itself mounts devfs. Avoid /etc/rc.subr,
# /var/run, /var/log, ldconfig, devd, networking and every writable SD volume.
devfs_ready=no
ram_ready=no
mounts=$(/rescue/mount -p) || fail 'cannot inspect mounts'
while read -r source target kind options dump pass; do
    if [ "$target" = /dev ]; then
        [ "$kind" = devfs ] || fail 'unexpected filesystem on /dev'
        devfs_ready=yes
    fi
    if [ "$target" = "$RAM" ]; then
        [ "$kind" = tmpfs ] || fail 'recovery RAM path is not tmpfs'
        case ",$options," in *,rw,*) ;; *) fail 'recovery RAM is read-only';; esac
        ram_ready=yes
    fi
done <<EOF
$mounts
EOF
if [ "$devfs_ready" = no ]; then
    /rescue/mount -t devfs devfs /dev || fail 'devfs mount failed'
fi
if [ "$ram_ready" = no ]; then
    /rescue/mount -t tmpfs -o size=32m,mode=0700 tmpfs "$RAM" || fail 'tmpfs mount failed'
fi
# Optional: the installer/operator may enable this only after the armed FAT
# selector and clean-shutdown hook are installed and verified. It stays absent
# in the default package, and the native program also enforces the policy.
if [ -f /etc/daos/recovery-watchdog.enabled ] &&
   [ ! -L /etc/daos/recovery-watchdog.enabled ] &&
   [ -x /usr/local/libexec/DaOS-recovery/kernel-watchdog ]; then
    recovery_mode=$(/rescue/kenv -q daos_recovery 2>/dev/null)
    if [ "$recovery_mode" != YES ]; then
        /usr/sbin/daemon -c -f -o "$RAM/kernel-watchdog.log" \
            -p "$RAM/kernel-watchdog-launcher.pid" \
            /usr/local/libexec/DaOS-recovery/kernel-watchdog || :
    fi
fi
[ -x "$PYTHON" ] && [ -r "$SUPERVISOR" ] || fail 'recovery runtime missing'
# daemon is in base /usr and needs no /var files because every path is explicit.
# The supervisor takes its own flock; stale PIDs after init kills are harmless.
if "$PYTHON" -I -B "$SUPERVISOR" status >/dev/null 2>&1; then
    exit 0
fi
/usr/sbin/daemon -c -f -p "$RAM/launcher.pid" \
    "$PYTHON" -I -B "$SUPERVISOR" serve || fail 'cannot launch supervisor'
exit 0
