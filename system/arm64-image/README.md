# Full-system ARM64 development image

This target boots a normal Debian ARM64 Python and Qt 5 runtime with the native
synthetic camera authority on QEMU `virt`. It verifies application integration
and persistence. It does not validate a board, physical display, sensor, lens,
camera interface, or storage device.

Run on a non-root Linux host with the existing QEMU, e2fsprogs, dpkg-deb and
AArch64 static compiler tools. The authenticated runtime tree is produced by
`tools.rootfs_build`; the native binaries by the ARM64 build helper. No packages
are installed on the host, no image is mounted on the host, and no block device
is opened. Use a **new** directory under this repository's `build` directory.

```sh
python3 -m tools.image_build build \
  --rootfs /path/to/stage/rootfs \
  --rootfs-manifest /path/to/stage/stage-manifest.json \
  --kernel-dir build/qemu-kernel-round6 \
  --native-dir build/arm64-native \
  --build-dir build/my-arm64-image
python3 -m tools.image_boot boot --build-dir build/my-arm64-image --phase first --timeout 330
python3 -m tools.image_boot boot --build-dir build/my-arm64-image --phase resume --timeout 330
```

The builder validates the authenticated rootfs entry manifest, exact tested
native binary/source identities, and checksum-pinned kernel package. It reads
the ext4/virtio module closure directly from that verified package. The CRC32C
crypto module is explicitly loaded because ext4 requests it at mount time.
The deterministic root-owned tar and ext4 creation use fixed timestamps, UUIDs,
directory hash seeds, and disabled lazy initialization. Build evidence records
tool versions, commands, hashes, package authentication, and application inputs.

`rootfs.ext4` is immutable and attached read-only. `data.ext4` is a separate
private writable filesystem mounted at `/data`; it survives guest poweroff.
The small static bootstrap is guest PID 1, mounts only guest devices, prepares
temporary runtime mounts, and drops to UID/GID 1000 before invoking ordinary
`/usr/bin/python3`. The UI uses Qt's offscreen platform and software renderer.
No external NIC, host mount, shared directory, monitor, or host display socket
is exposed. Guest loopback is private to the VM. QEMU stays in the invoking
process group and is terminated on interruption or the bounded boot timeout.

The first boot operates the real QML controls, captures a synthetic DNG, queries
its UUID, displays a conversion of the retained Bayer data, and exports an
identical copy. The second boot uses the same data image and saved UI state to
query the original UUID without another shutter action. Both phases require
one complete journal row, clean authority shutdown, clean data unmount and
read-only filesystem verification. The host checks that the root image hash is
unchanged and extracts bounded reports, screenshots, thumbnails and DNGs with
read-only `debugfs`. No pixel bytes are carried in JSON.

Evidence is stored at `build-evidence.json` and
`boots/{first,resume}/boot-evidence.json`; each boot also retains `serial.log`
and extracted files in `guest/`. A phase output directory must be new. Failed
or completed boots are never replayed automatically, and a changed data image
is rejected unless it is exactly the previous successful phase's image. Keep
failed evidence and create a new build directory after correcting guest inputs.

The staged application also includes the normal launcher:

```sh
/usr/bin/python3 -B /opt/hasslefree/ui/on-device/main.py \
  --native-command /opt/hasslefree/bin/hf-camerad \
  --data-dir /data/camera --ui-state-dir /data/device-ui --offscreen
```

This is a local development image with an automated PID 1 test entrypoint, not
a production OS, secure-boot chain, update system, login environment, or deployable
board image. Runtime caches are private writable state; the application and
runtime packages remain on the read-only filesystem.
