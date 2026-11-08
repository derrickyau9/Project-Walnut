# DaOS ADB device daemon

This is an independent device-side ADB protocol implementation for Python 3.12
and POSIX systems. It uses only the Python standard library. It does not use an
Android runtime or call a host `adb` program to implement the device endpoint.

The official Android Platform Tools 37.0.1 host has passed the actual command
suite through both the explicit loopback listener and the binary stdio adapter.
Those original Linux host receipts do not validate USB hardware or FreeBSD
execution. A later [FreeBSD ARM64 reference run](evidence/freebsd-arm64-001/summary.json)
loaded the actual source-built module and passed the official-host protocol
suite over an SSH-forwarded private loopback TCP endpoint. Physical Raspberry
Pi USB-C enumeration and Windows binding remain **not validated**. See the
[connection guide](../README.md) for cables, independent power and authorized keys.

## Device installation

Install the eight files in `runtime-manifest.json` together in one directory,
for example `/usr/local/libexec/daos-adb`. The entry point supports directory
renaming; its imports and PTY helper resolve beside the installed files.

Provision one or more already authorized Android `adbkey.pub` lines in a file
such as `/etc/daos/adb_keys`. The device never receives a host private key.
Incoming AUTH public-key packets do not add authorization. RSA2048/e65537
PKCS#1 v1.5 signatures over ADB SHA1 tokens must verify against a provisioned key.

Start through the FreeBSD device-endpoint helper:

```sh
daos-usb-adb --vid 0xffff --pid 0xda05 --wait -- \
  /usr/local/bin/python3.12 /usr/local/libexec/daos-adb/daos_adbd.py \
  --stdio --authorized-keys /etc/daos/adb_keys --cwd / --sync-root /
```

The daemon starts no network listener by default. `--listen 127.0.0.1:PORT` is
an explicit test transport; other addresses are rejected. Shells and sync use
the daemon's operating-system UID and filesystem permissions. `--sync-root` is
required to enable file transfer; omit it to expose only shell and exec.

## Transport contract

Standard input and output contain only ADB bytes. Logs are JSON lines on
standard error. The packet header is 24 bytes, followed by exactly
`data_length` payload bytes. A partial read may split any header or payload.
The USB helper must reassemble stdout packets and submit the header and each
nonempty payload as separate USB bulk IN transfers. It forwards arbitrary bulk
OUT fragments to stdin. `stdio_bridge.py` deliberately sends stdin in seven-byte
fragments and verifies stdout framing, but implements no USB transfers.

The daemon negotiates protocol 0x01000000/0x01000001, maxdata up to 64 KiB, and
advertises only `shell_v2`. Legacy peers receive additive payload checksums;
modern peers use the negotiated skip-checksum behavior. Each stream has one
outstanding WRTE until OKAY, bounded input buffering, and independent stream IDs.

Supported services:

- `shell:`: legacy shell output, with interactive PTY support.
- `shell,v2,raw:` and `shell,v2,pty:`: stdin, stdout, stderr, exit status,
  close-stdin, and terminal-size frames. PTY output merges stdout/stderr.
- `exec:`: raw PTY byte stream for host `exec-out` clients.
- `sync:` when explicitly enabled: uncompressed v1 STAT/LIST/SEND/RECV,
  regular-file push/pull and directory listing, with path and size bounds.

Other services, including root/remount/reboot, forwarding, Android app install,
and network-listener reconfiguration, are rejected. No Android framework API
is advertised. Connection loss closes the stream and reaps its shell process
group; SIGTERM also follows cleanup before exit.

## Verification

```sh
python3 -m unittest discover -s system/adb/daemon -p test_daemon.py -v
python3 system/adb/daemon/integration_adb.py \
  --adb /path/to/official/platform-tools/adb --key /private/development/adbkey \
  --work /private/new-test-run --transport stdio-via-loopback
```

The integration runner starts its own foreground adb server on a random port
and excludes USB devices with `--one-device`. It never uses port 5037 or kills
another adb server. A separate unauthorized server verifies that a different
host key cannot run a shell. Private keys remain outside this source tree;
evidence export copies only a fixed list of receipts and logs.

Public development key: `keys/daos-development-adbkey.pub`. Its Android public
structure SHA256 fingerprint is recorded in `runtime-manifest.json`. The
matching private key remains in the F-backed development tools directory.

## Primary protocol references

- [ADB transport protocol](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/dev/protocol.md)
- [ADB services](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/dev/services.md)
- [Shell frame IDs](https://android.googlesource.com/platform/packages/modules/adb/+/master/shell_protocol.h)
- [PTY, shell and window-size behavior](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/daemon/shell_service.cpp)
- [Android RSA public-key format](https://android.googlesource.com/platform/system/core/+/refs/heads/main/libcrypto_utils/android_pubkey.cpp)
- [Actual additive checksum implementation](https://android.googlesource.com/platform/packages/modules/adb/+/HEAD/adb.cpp)
- [Protocol version constants](https://android.googlesource.com/platform/packages/modules/adb/+/1bacec0a271e494d149d5e46f745b04a3c5c7a19/adb.h)
