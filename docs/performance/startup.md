# Startup optimization

For the actual **power-on to camera UI** goal, see [the whole-boot candidate and
measurement definition](power-on.md). The desktop measurements below cover
only the application substage.

The camera capture screen is the startup priority. Menu, playback, tools,
workspace controls and keyboard QML are created on first use, with their
dependencies installed before component completion. Loaded pages are retained,
including editor state; navigation does not destroy and reconstruct them.
Camera authority, capture-result reconciliation, storage/restore checks and the
USB recovery path are not deferred or disabled.

The FreeBSD session's informational whole-kernel and shell SHA256 audit starts
after the real UI-ready marker. It no longer reads the entire kernel from SD
before starting Xorg. Complete digests still produce `base-identity`; failed or
cancelled reads produce `base-identity-incomplete`, never a partial digest.
`session-identity` records initial platform information and the pending audit.
Session events include milliseconds since supervisor startup. Account identity,
required mounts, runtime ownership, X authentication and splash handoff remain
synchronous checks. This does not replace a verified-boot mechanism.

## Offline measurements, 2026-11-12

Five alternating baseline/optimized fresh-process runs, with empty state and
cache directories, Windows/Python 3.12/Qt 5.15 software rendering and the native
HF-Sim authority. Both variants use the same `loadData` driver and QML base URL.
The timer starts at the child benchmark script, before importing Qt. Capture
readiness requires an actual presented frame and the controller's `canCapture`.
No photograph is taken. These are not Pi power-on or cold-SD measurements.

| Metric, median | Before | Optimized | Reduction |
| --- | ---: | ---: | ---: |
| First presented frame | 950 ms | 583 ms | 38.6% |
| Capture-ready frame | 989 ms | 617 ms | 37.7% |
| Main QML load | 669 ms | 321 ms | 52.0% |
| Qt objects at readiness | 2077 | 423 | 79.6% |

[Raw samples and source hashes](startup-2026-11-12.json) retain the comparison
and baseline revision. The timings above concern the application; the SD I/O
saved by deferring the kernel audit has not been measured on the board.

To benchmark the current source on a desktop with the optional Qt dependencies:

```sh
python tools/benchmark_camera_startup.py --output build/startup-new --samples 5
python tools/benchmark_camera_startup.py --output build/startup-native-new --samples 5 --backend native --native-command build/native/hf-camerad
```

Use `build/native/Release/hf-camerad.exe` on Windows. Each output directory must
be new. No existing camera database, USB transport or physical card is used.

## Image and hardware boundary

The previously published preview images remain frozen. The optimized QML needs
the new application bundle, and the deferred audit needs the updated FreeBSD
session overlay as well. An application-only update does not change the root
session. This source change has not been flashed or timed on the Pi.

Before claiming an end-to-end power-on time, measure the same board, card and
power source over multiple cold boots, including firmware, loader, kernel,
mounts, session, first frame and camera authority readiness. Check first-use
menus/playback/keyboard, capture UUID recovery, single-user USB rescue and a
failed-start path. Keep the existing loader interruption window and device
enumeration limits until that physical validation is available.

当前离线结果只证明相机应用启动更快，不能把约 0.6 秒解释成树莓派整机开机
时间。实机尚未连接；完整冷启动、真实图像源和急救回退仍需在设备上验收。
