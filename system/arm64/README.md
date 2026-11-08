# Static ARM64 cross build and explicit user-mode emulation

This profile cross-builds the synthetic camera code for AArch64 Linux and runs it
with QEMU user mode. It is a separate development target beyond the immutable
round 5 snapshot. No board, sensor, FPGA receiver, DMA path, bootloader or real-time
behavior is implemented or validated by this profile.

The executed host was Debian 13 x86_64 under WSL, with Clang 19.1.7, GCC 14.2.0's
installed `aarch64-linux-gnu` C compiler/sysroot, GNU ld 2.44 and QEMU user 10.0.13.
The host already supplied target C headers, static glibc and libgcc. It lacked the
cross C++ development headers and `libstdc++.a`. The helper downloaded only the
official Debian `libstdc++-14-dev-arm64-cross` 14.2.0-19cross1 package, checked its
2,551,912-byte length and SHA-256 from the preserved lock, and extracted it into
`build/arm64-tools`. No apt installation, package script, service change or system
PATH modification was performed.

The [toolchain lock](toolchain-lock.json) preserves the official package URL and
hash. This is a supplement to installed cross tools, not a universal SDK installer.
On a host missing the declared C/sysroot/compiler/emulator prerequisites, the
helper fails explicitly. The package hash was taken from the host's configured
Debian apt metadata; the helper does not claim a fresh Debian release-signature
verification. Static runtime archive and compiler hashes are recorded for review.
Distribution and runtime-license review remain separate from local emulation.

From the repository root in an actual Linux process:

```sh
python3 -m tools.arm64_build --tools-dir build/arm64-tools --build-dir build/arm64-native --jobs 2
```

This automatically prepares the isolated supplemental package, compiles all
native sources, links four static executables and runs their tests through an
explicit QEMU user-mode invocation. It requires no binfmt registration. The build
preserves the `clang++` invocation name: resolving that symlink to `clang` changes
the link driver's C++ runtime selection, so its invocation and resolved file are
recorded separately.

The outputs are `hf-core-tests`, `hf-daemon-tests`, `hf-native-worker` and
`hf-camerad`. Each ELF is independently inspected for ELF64, little-endian data,
`EM_AARCH64` machine value 183, a loadable segment, and absence of both a dynamic
interpreter and `DT_NEEDED` libraries. A target architecture string in a report is
not accepted as proof of an ARM binary. The builder records 21 repository input
hashes, target C/C++/compiler-header tree fingerprints, critical target runtime
archives, actual tool executable hashes, all commands and output hashes. Native
and recipe inputs must remain unchanged through build and tests.

The static SQLite profile explicitly sets `SQLITE_THREADSAFE=1`, `SQLITE_DQS=0`
and `SQLITE_OMIT_LOAD_EXTENSION=1`. No application API uses loadable SQLite
extensions. The ordinary x86_64 Linux build/packager and existing contracts are
unchanged. ARM evidence uses `hf-arm64-cross-build-v1`, with an explicit target
triple and separate x86_64 host identity, so it cannot be mistaken for a native
x86_64 Linux build report.

Actual user-mode results:

- 89 native capture-core and 82 native authority assertions passed.
- All 20 native daemon/RPC process tests passed under the QEMU launcher, including
  crash recovery, no replay, leases, cancellation, immutable requests, journal
  corruption, retained listing and retained asset descriptors.
- Four ARM ELF/tool-selection tests passed, including rejection of host x86_64
  headers, dynamic-library dependencies, truncated headers and loss of the
  `clang++` invocation name.
- The existing worker CLI suite ran nine tests: six passed and three were
  explicitly skipped. The passing tests cover exact Python Bayer reference
  pixels, scenes/gain/clipping, metadata, no overwrite, complete output before
  stdout success and Unicode paths. LibRaw/rawpy and tifffile were unavailable
  in this Linux interpreter; the full-frame opt-in was unset. No independent
  decoder or full-frame execution is claimed by those skipped checks.

That nine-test worker report is historical. A later explicit ARM64 user-mode
full-frame opt-in test passed; its separate
[full-frame report](evidence/full-frame-worker.json) closes that one skip without
rewriting the earlier result. It establishes synthetic full-frame I/O only, not
an FPS, real-time or physical-sensor performance result.

The [preserved cross-build report](evidence/arm64-build-evidence.json) contains
the actual test stdout/stderr and source/tool/artifact binding. The
[supplemental worker report](evidence/arm64-worker-cli-evidence.json) records the
separately executed worker suite. All four programs were executed under QEMU
user mode, in addition to their ARM machine/header inspection.

`hf-camerad-qemu` and `hf-native-worker-qemu` are generated host test launchers.
They execute `/usr/bin/qemu-aarch64` with the corresponding static ARM ELF and
forward arguments unchanged. They are not board service packages. For example,
use the normal host Python client with an explicit command list:

```python
from camera.camerad.native_client import NativeClient

client = NativeClient('/tmp/hf-arm64-dedicated', [
    '/usr/bin/qemu-aarch64', '/absolute/build/arm64-native/hf-camerad'
])
print(client.get_state())
client.close()
```

Use fresh dedicated Linux storage. Existing request IDs, lease epochs and unknown
outcomes retain their authority semantics; emulation is not permission to replay
an uncertain request. User mode maps the target process's system calls to the
host Linux kernel. It is not a guest-kernel boot, physical ARM board test,
storage-media power-cut test or throughput benchmark. Full-system generic QEMU
`virt` testing is documented separately under `system/qemu-virt`.

## Distinct static ARM64 simulator release

`tools.target_package` stages a release with format
`hf-static-arm64-sim-package-v1`. It deliberately does not use or change the
existing x86_64 host bundle format. The two packaged executables must be the
source-bound, successfully tested static AArch64 artifacts. The release contains
their Python standard-library server/runtime, console assets, offline diagnostics,
an ELF validator and supplied notices. It contains no QEMU executable or launcher.
The project's rights status remains explicitly `pending`; supplied GCC/libstdc++
and glibc cross-runtime copyright texts and their provenance are preserved.

```sh
python3 -m tools.target_package stage --native-dir build/arm64-native --output build/arm64-static-release --package-id arm64-static-example
python3 -m tools.target_package validate build/arm64-static-release
```

Staging and integrity validation can run on the build host. An existing output is
never overwritten. Use a new directory outside the source tree or beneath its
dedicated `build` directory. Stale native sources/artifacts, package corruption,
unexpected files and unsafe paths are rejected. Validation is an integrity check,
not an authenticity signature or project distribution-rights clearance.

On a compatible Linux ARM64 host with Python 3.10+ and its required standard
library modules, the native entry point is:

```sh
python3 -I -S -B /absolute/release/runtime/system/arm64/runtime.py --release-root /absolute/release --data-dir /absolute/dedicated-camera-data --port 8787
```

The entry point checks the actual Linux `platform.machine()` for `aarch64` or
`arm64`, refuses root, and has no architecture override or emulator fallback.
It verifies the release before making an explicitly absolute, separate data
directory. Storage cannot overlap an application package or nest under another
camera journal. The server runs with isolated Python, site hooks disabled, bytecode
writing disabled and only the verified runtime inserted into its module path.
Regular package markers are added inside the release, without changing project
source packages, so external namespace/regular packages cannot replace its camera,
workflow, simulation or runtime modules.

The package implementation passed nine tests on both Windows and actual Debian
Linux, including actual wrong-host refusal without creating data, package/import
integrity, no overwrite and storage/source-tree boundaries. A real 50-file,
approximately 8 MB package was staged and validated. Its packaged Python server
was then exercised on the **x86_64 host** with an explicit QEMU launcher in a
separate temporary directory:

```sh
python3 -m tools.target_package emulation-smoke build/arm64-static-release --report build/arm64-package-smoke.json
```

The final actual Linux package test
[result](evidence/target-package-tests-linux.json) and
[raw log](evidence/target-package-tests-linux.txt) are preserved separately.

This test removes inherited Python overlays, disables site hooks, verifies module
origins under the release, and performs HTTP capture, retained listing, PNG
thumbnail, DNG export and restart/query of the same completed request. Package
bytes remain unchanged. The
[package evidence](evidence/target-package-evidence.json) labels host Python plus
QEMU user mode explicitly. It does not claim execution of the native ARM Python
entry point on a physical board. No service was installed or enabled. Native ARM
host/Python compatibility, target filesystem permissions/durability and actual
device deployment still require checks on the chosen system; no board BSP or
system-image/update behavior is supplied here.
