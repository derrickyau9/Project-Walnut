#!/rescue/sh
# Factory preview variant: public, volatile machine identity is separate from
# the mode-0700 authenticated USB supervisor's private state.
PATH=/rescue:/sbin:/bin:/usr/sbin:/usr/bin
export PATH
IDENTITY=/DaOS-identity
BASE=/usr/local/libexec/DaOS-recovery/early-init-base.sh

identity_ready=no
identity_mounts=0
if [ -d "$IDENTITY" ] && [ ! -L "$IDENTITY" ]; then
    mounts=$(/rescue/mount -p)
    if [ "$?" -eq 0 ]; then
        while read -r source target kind options dump pass; do
            if [ "$target" = "$IDENTITY" ]; then
                identity_mounts=$((identity_mounts + 1))
                if [ "$kind" = tmpfs ]; then
                    case ",$options," in *,rw,*) identity_ready=yes;; esac
                fi
                [ "$identity_ready" = yes ] || identity_ready=unexpected
                [ "$identity_mounts" -eq 1 ] || identity_ready=unexpected
            fi
        done <<EOF
$mounts
EOF
        if [ "$identity_ready" = no ]; then
            if /rescue/mount -t tmpfs -o size=1m,mode=0755 tmpfs "$IDENTITY"; then
                identity_ready=yes
            fi
        fi
    fi
fi
[ "$identity_ready" = yes ] || printf 'DaOS volatile machine identity unavailable\n' >&2
exec "$BASE"
