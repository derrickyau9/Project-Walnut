# Native synthetic capture core

This contains a dependency-free C++17 capture core and one-shot DNG worker, plus a separate durable native simulator authority. It produces synthetic Bayer frames; there is no sensor register, FPGA, DMA, V4L2 or libcamera implementation. The native authority uses the preserved SQLite and JSON dependencies described in [third_party](third_party/README.md).

## Build and test

```powershell
python tools/build_native.py
```

The helper uses CMake on PATH or Visual Studio's bundled CMake. A C/C++17 compiler is required. This workspace was built with Visual Studio 2022 / MSVC 19.44. CTest covers the capture core, authority, durable settings/binding, SHA-256 reference vectors and strict calibration-binding validation. The POSIX implementation was also built and exercised on Debian x86_64 Linux in WSL with GCC 14.2.0; see the [Linux host guide](../../system/linux/README.md) and [verification evidence](../../docs/testing/g1-evidence.md). Normal ARM64 Linux Python/Qt and this synthetic native authority were also exercised through two full-system QEMU boots with persistent capture state; see the [ARM64 image guide](../../system/arm64-image/README.md). Physical camera-body execution, sensor acquisition and display hardware remain unverified.

On this Windows build the executable is `build/native/Release/hf-native-worker.exe`; a single-configuration Unix build normally uses `build/native/hf-native-worker`. Generated outputs are excluded from source tracking.

## Native authority and independent UI

The native `hf-camerad` process owns SQLite admission, capture, final file publication, control epochs and lease fencing. Its [bounded stdio RPC](RPC.md) has real subprocess crash and recovery tests. It refuses foreign journals; use a new dedicated directory when selecting a different authority. No automatic journal migration or capture replay is implemented.

```powershell
python ui/on-device/main.py --data-dir var/hf-device
python -m camera.camerad --data-dir var/hf-native-authority --authority native capture --exposure-us 10000
python -m camera.camerad.server --data-dir var/hf-native-authority-web --authority native
python -m daos.capability_runtime --data-dir var/hf-native-authority-workflow --authority native run --job bracket-001 --exposures-us 5000 10000 20000
```

Set `HF_NATIVE_DAEMON` or pass `--native-daemon` to CLI/server tools when the executable is outside the normal build directory. The QML UI uses `--native-command`. This is an independently executable offline simulator path. The private stdio client owns one process; the native process has no multi-client local-socket attach API. The existing loopback HTTP adapter can multiplex clients through one native authority. QML can either launch its own authority or attach to that shared local API:

```powershell
python -m camera.camerad.server --data-dir var/hf-shared --authority native --port 8787
python ui/on-device/main.py --backend http --endpoint http://127.0.0.1:8787 --ui-state-dir var/hf-device-ui
```

The HTTP attachment never stops the server. QML releases only its own lease on shutdown. This shared loopback development path has an actual native-authority capture and UI shutdown regression test; it is not a production socket or authenticated network transport.

## Python reference authority with a native image worker

The earlier mixed path is retained for comparison. Here Python owns the journal and control while C++ only generates the image:

```powershell
python -m camera.camerad.server --data-dir var/hf-native --backend native --native-worker build/native/Release/hf-native-worker.exe
python -m camera.camerad --data-dir var/hf-native-cli --backend native --native-worker build/native/Release/hf-native-worker.exe capture --exposure-us 10000
python -m daos.capability_runtime --data-dir var/hf-native-workflow --backend native --native-worker build/native/Release/hf-native-worker.exe run --job interval-001 --kind interval --interval-ms 1000 --exposures-us 5000 10000 20000
```

Use a separate data directory per concurrently running authority. Existing file locks prevent two processes owning the same journal. There is no automatic fallback to another authority or backend after a failed capture.

## Contract and limits

The core checks dimensions and memory limits before allocation, generates deterministic scene/dark/flat 14-bit RGGB pixels, records requested and actual synthetic settings, and checks cancellation/deadline per row. Frame integrity includes sample range, dimensions and a content checksum. BufferPool limits retained buffers; callers must separately bound frames they keep alive. These mechanisms are not real-time hardware guarantees.

The worker emits only a bounded metadata object on stdout after closing its DNG output. It refuses to overwrite an existing staging path. The service adapter verifies identity, format, actual synthetic settings, output size and the shared DNG structural inspection before file synchronization and final journal commit. Worker nonzero exit, malformed result, timeout or cancellation never becomes a completed capture. Pixel arrays do not travel in JSON.

The metadata preserves request/capture/frame UUIDs; `native_frame_sequence` is local to a worker invocation. `captured_at_unix_ns` is a host software timestamp, not a measured exposure trigger time. The native checksum is an integrity aid, not an authenticity signature. All color matrices and calibration remain synthetic.

The native authority binds photo storage to the canonical directory identity seen at startup. It checks that identity before capture writes and publication. Its internal DNG receipt comes from the writer's actual open file descriptor; expected serialized bytes, file identity and stable file statistics are verified before synchronization/publication and again after publication. Changed, replaced or incomplete staging files are retained for reconciliation; they are never treated as completed captures or blindly replayed. These checks detect changes at the boundaries and do not provide atomic isolation from a hostile process with the same filesystem permissions.

Configured settings are durable SQLite state, committed before changing the confirmed in-memory baseline. Explicit capture parameters do not change that baseline. The paired `settings` and `settings_format` metadata entries use `hf-device-settings-v1`; only an older journal missing both entries receives defaults. Partial, corrupt or unsupported stored settings refuse startup before the control epoch changes. A failed write retains the last confirmed settings, reports failure and fences mutations until restart. State reports `settings_persistent` and `settings_format` so a frontend can distinguish confirmed authority state from its own cache.

API 1.1 also accepts an immutable synthetic calibration binding, selected under an idle control lease and stored as an inline verified snapshot. The native validator hashes the exact ASCII payload bytes, validates the complete package schema and expected `HF-Sim` sensor, and keeps both calibration/correction flags false. A bound capture has explicit record version 2 and retains that snapshot; its DNG carries the `cb1-...` trace identity. Unbound legacy records retain their original shape. Changing or clearing selection cannot reinterpret an admitted request or cause it to be re-executed. No pixel correction or measured hardware calibration is applied. See the [wire protocol](RPC.md) and [binding artifact specification](../calibration/BINDING.md).

Independent tests compare native output to Python Bayer values and decode DNG with tifffile and optional LibRaw. Enable the full-frame test with `HF_FULL_FRAME_TEST=1`; set `HF_NATIVE_WORKER` when using a nondefault executable path. See [G1 evidence](../../docs/testing/g1-evidence.md).
