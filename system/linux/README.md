# Linux host simulator deployment and recovery exercises

This is an executable Linux **host simulator** path. It does not supply a board
BSP, bootloader, partition map, verified boot, sensor driver or firmware update
agent. The A/B model below makes durable host-side decisions; it does not reboot,
mount, format, copy a disk image, select an actual boot slot or change an installed
release. Photographs, native request journals and calibration remain separate.

## Build and exercise the real POSIX implementation

Requirements: Linux, Python 3.10+, a C compiler and a C++17 compiler, standard
Linux development headers/libraries, and approximately 250 MB of build space.
SQLite and JSON sources are pinned in `camera/native/third_party`; the build
downloads and installs nothing. CMake is an alternative, not a prerequisite for
the direct compiler helper:

Use a Linux-native filesystem for persistent simulator data and recovery state,
with directories dedicated to that OS. Do not mix Windows and Linux authorities
on a shared DrvFS/NTFS directory. A disposable Windows/WSL probe observed exclusion
in both directions on this host (Linux reported `storage_error` against a Windows
owner; Windows reported `already_running` against a Linux owner). That observation
does not establish cross-OS filesystem durability or a portable lock guarantee.

```sh
python3 tools/linux_build.py --build-dir build/linux-native --jobs 2
```

This compiles the POSIX file lock, `/dev/urandom` tokens, SQLite journal, file and
directory synchronization and exclusive publication code. It runs both native
test executables, native daemon/client and HTTP process tests, build configuration
tests and Linux deployment/recovery tests. It records compiler, platform, exact
commands, native source/header/build-recipe hashes and executable hashes in
`build/linux-native/build-evidence.json`. `--native-tests-only` limits execution
to the two C++ test programs. For a CMake-equipped host, the existing
`python3 tools/build_native.py` remains supported.

After the build, exercise staging, an actual nonroot packaged HTTP capture,
clean shutdown/restart, real trial-health confirmation and offline fallback:

```sh
python3 -m tools.linux_build_smoke --build-dir build/linux-native
```

This uses a new temporary directory and writes
`build/linux-native/deployment-evidence.json`. It enables no service. The launcher
uses Python's `-B` option so execution does not add bytecode files to an immutable
bundle. If available, `systemd-analyze --user verify` checks the staged unit too.

The Windows development environment has a working Debian WSL distribution even
though its Ubuntu distribution fails with `Wsl/Service/E_UNEXPECTED`. Genuine
Debian Linux builds and process tests are recorded in the build evidence; these
are distinct from MSVC tests. Docker's client is installed but its Linux engine
was unavailable. No Linux tool or distribution was installed for this work.

## Stage an immutable host release as a regular user

Build on a compatible Linux host of the intended architecture. The bundle uses
that host's normal C/C++ runtime libraries; no broad glibc or distribution
compatibility is implied. The packager requires successful Linux build evidence,
checks that executable hashes still match it, and refuses Windows PE binaries.
Native build inputs must also match the tested source hashes. Missing legacy
source hashes, changed inputs or inputs changing during a build require a fresh
build and tests before packaging. This is source and artifact traceability, not
a hermetic-toolchain or bit-for-bit cross-host reproducibility claim.

```sh
python3 -m tools.linux_package --build-dir build/linux-native --package-id sim-001 --output "$HOME/.local/opt/hf-sim/releases/sim-001"
```

The new directory contains `bin`, Python runtime sources, a user service template,
preserved dependency notices and a complete SHA-256/length manifest. An existing
output directory is refused. The manifest detects corruption and incompatible
host architecture; it is not a package signature or an authenticity policy.

Before running a service, exercise a **new dedicated scratch directory**. Never
point a health check at owner photographs or a live service data directory:

```sh
python3 -m system.linux.healthcheck --native-daemon "$HOME/.local/opt/hf-sim/releases/sim-001/bin/hf-camerad" --scratch-dir "$HOME/.local/state/hf-sim/health-run-001"
```

The check acquires a lease, submits one synthetic request, verifies its DNG,
closes and restarts the authority, and queries the same completed ID. It never
resubmits after transport uncertainty. Existing scratch directories are refused
and retained evidence is left for inspection. Health of an existing journal can
be inspected offline with `python3 -B -m tools.diagnostics --data-dir /absolute/camera-data --offline-only`; that tool is included in the staged runtime and does not
replay requests. An `unknown` capture must remain unknown until independently
reconciled; changing binaries or restarting is not permission to capture again.

## Optional nonroot systemd user service

Review `hf-sim.service` before enabling it. The native daemon has private stdio
RPC, so this unit starts the existing loopback HTTP adapter, which owns one native
process. It binds only `127.0.0.1:8787`; it is a development simulator endpoint.
The Qt UI can attach with `--backend http --endpoint http://127.0.0.1:8787`.

Run as the intended unprivileged account. Create the dedicated persistent data
directory before service startup; keep offline calibration in its own store.
The launcher refuses root and application/data-directory overlap. Select the
release explicitly; `ln -s` below refuses an existing `current` path.

```sh
mkdir -p "$HOME/.local/share/hf-sim/native-authority" "$HOME/.config/systemd/user"
ln -s "$HOME/.local/opt/hf-sim/releases/sim-001" "$HOME/.local/opt/hf-sim/current"
install -m 644 system/linux/hf-sim.service "$HOME/.config/systemd/user/hf-sim.service"
systemctl --user daemon-reload
systemctl --user enable --now hf-sim.service
systemctl --user status hf-sim.service
journalctl --user -u hf-sim.service
```

The unit uses the user's permissions, a private umask, explicit persistent
storage, bounded restart attempts and a bounded stop. SIGINT requests normal
server cleanup; a forced kill may leave a capture `unknown` after restart. No
capture replay is configured. The template requires a working systemd user
manager and support for its namespace protections; inspect a startup failure
instead of silently weakening the unit. Unit parsing and an actual nonroot
foreground launch are separately testable; installation/enabling is optional.
No service was enabled in the development user's environment.

## Offline A/B recovery state machine

`system.linux.recovery` validates extracted bundles and stores its own state in
`recovery.sqlite3` under an explicitly separate directory. Before creating a
directory or opening SQLite, it refuses locations inside a recognized host-bundle
manifest tree or an existing `journal.sqlite3` camera tree. These checks include
existing ancestors, so nested paths under a bundle or camera `photos` directory
are refused without creating directories or changing bytes. Arbitrary unmarked
owner data and calibration directories cannot be inferred; keep those explicitly
separate as well. A trial attempt is committed
before a simulated boot choice is returned. Unconfirmed attempts consume a finite
budget across abrupt process exits; exhaustion selects the last confirmed bundle.
Changed bundles fall back. A confirmation must match the latest attempt, bundle
manifest and native executable, with successful synthetic capture/restart health
evidence. These are host state-machine tests, not actual boot recovery tests.

```sh
python3 -m system.linux.recovery --state-dir "$HOME/.local/state/hf-sim/recovery-model" --architecture x86_64 bootstrap "$HOME/.local/opt/hf-sim/releases/sim-001"
python3 -m system.linux.recovery --state-dir "$HOME/.local/state/hf-sim/recovery-model" --architecture x86_64 stage "$HOME/.local/opt/hf-sim/releases/sim-002" --attempts 2
python3 -m system.linux.recovery --state-dir "$HOME/.local/state/hf-sim/recovery-model" --architecture x86_64 choose-boot
```

Use the returned attempt ID and exact release path for a trial health check:

```sh
python3 -m system.linux.healthcheck --native-daemon "$HOME/.local/opt/hf-sim/releases/sim-002/bin/hf-camerad" --scratch-dir "$HOME/.local/state/hf-sim/health-run-002" --bundle "$HOME/.local/opt/hf-sim/releases/sim-002" --attempt-id ATTEMPT_UUID > health.json
python3 -m system.linux.recovery --state-dir "$HOME/.local/state/hf-sim/recovery-model" --architecture x86_64 confirm ATTEMPT_UUID health.json
```

The selection is a report only. Changing an actual running release remains an
explicit operator action after stopping the service and preserving its data.
Real board power cuts, bootloader fallback, authenticity/owner-install policy,
storage-media guarantees and update/recovery integration remain unimplemented.
