# Windows DaOS USB development client

For the complete Chinese steps, Pi4 USB-C data cable, independent power,
authorization and troubleshooting, see [USB development access](README.md).

Run `Connect-DaOS-USB.cmd` from the project root. It uses the official Windows
Platform Tools in `build/DaOS_USB_Development/host-tools/platform-tools` and a
project-specific host key beneath that host-tools directory. Private keys are
never copied into the target image. Only the public development key and this
PC's existing public adb key are available under `daemon/keys` for provisioning.

The default adb server port is 5049; the normal user server on 5037 is untouched.
The client checks its own server's PID/start time, executable and key identity.
It neither kills nor adopts an unrecorded server. `--server-port 5050` selects
another project port. Use `--serial DaOS-...` for another explicitly named DaOS
board. The server's `--one-device` filter prevents enumeration of other USB
serials; the client additionally excludes phones, TCP transports and foreign
device metadata. Multiple authorized DaOS transports require `--select N` or
an interactive selection. `--list` shows matching transports without a shell.

The script waits up to 90 seconds by default; `--timeout 300` extends that
window. `--no-shell` waits for a ready device and reports its identity. `exit`
leaves the shell; the owned adb server remains available for the next run.

The initial image identity is `DaOS-USB-ADB-DEVELOPMENT`, with unassigned local
development VID/PID `ffff:da05`. On actual Windows hardware the corresponding
USB interface must bind to WinUSB/ADB, and the device image must contain the
matching authorized public key. A data-capable cable and Pi4 peripheral-mode
USB-C setup are required. The host tool download does not establish hardware
enumeration or binding.

Keep Pi4's USB-C port for host data and provide stable independent board power
using a suitable protected arrangement. Its four USB-A ports are host ports,
not this device endpoint. The Chinese guide links the official Pi4 OTG and
power references. A power-only cable cannot carry ADB.

The target does not enroll unknown keys or show Android's authorization popup.
Changing the PC/private key requires explicitly provisioning the new public key
in a rebuilt integration bundle. Never copy the private key into the image.

`host-tools-evidence.json` records the official download URL, archive/client
SHA256, exact adb version, script hashes and public fingerprints. The actual
Windows adb 37.0.1 test connected to a WSL loopback stdio adapter, authenticated,
and returned separate stdout/stderr with exit code 7. Its receipt is
`windows-stdio-integration.json`. This is a TCP protocol test; physical USB was
not exercised. No user system network configuration was changed.
