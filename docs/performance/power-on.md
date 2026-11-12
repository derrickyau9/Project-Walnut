# Power-on to camera UI

The target is **power applied to the board → actual camera UI presented**.
The desktop application's approximately 0.6-second measurements are a substage,
not an end-to-end boot result. No physical power-on result is available yet.

## Whole-chain candidate

| Stage | Candidate change | Validation boundary |
| --- | --- | --- |
| EEPROM / GPU firmware | Preserve the current device and display configuration | Boot order and physical firmware time require the board; no EEPROM/OTP write |
| U-Boot | `build_uboot_selector.py --fast-boot` derives a separate selector binary with `CONFIG_BOOTDELAY=0`, preserving the selector-first SD command | The recorded pinned baseline has delay 2. Binary must be rebuilt and tested; saved FAT environment can override defaults |
| FreeBSD loader | Explicit fast profile sets `autoboot_delay="0"` instead of the current 1 | Candidate configuration, not a measured one-second end-to-end gain |
| Kernel / mounts | Preserve current drivers, USB rescue and required persistent-volume checks | Do not guess shorter SD/USB discovery timeouts or skip filesystem checks without a cold-boot trace |
| Xorg / Python | Start unprivileged Python definition imports while Xorg initializes | QApplication waits for a fresh root-owned display handoff; 30-second failure limit and child cleanup retained |
| Splash handoff | In fast mode, observe native renderer supervisor cleanup before issuing the Qt handoff | Brief real-exit polling, then the existing bounded service-stop fallback; no handoff while the known renderer is alive |
| Camera UI | Existing deferred pages; actual post-splash camera frame marker | Software presentation marker, still needs physical display validation |

The two recorded countdown defaults add up to **3 configured seconds**. Their
removal is not a claim that a built or installed board now boots three seconds
faster. Neither a zero-delay U-Boot binary nor a new complete SD image has been
built in this offline run, and no physical media has been modified.

U-Boot's `bootdelay=0` retains abort-key checking, unlike `-2`; see the
[official environment documentation](https://docs.u-boot.org/en/latest/usage/environment.html).
FreeBSD's zero loader countdown behavior is documented in
[loader.conf(5)](https://man.freebsd.org/cgi/man.cgi?query=loader.conf&sektion=5&manpath=FreeBSD+15.1-RELEASE).
The native renderer uses `daemon -P` without restart; its supervisor removes
the pidfile after the child exits, as described in
[daemon(8)](https://man.freebsd.org/cgi/man.cgi?query=daemon&sektion=8&manpath=FreeBSD+15.1-RELEASE).

## Explicit activation and coordinated files

Standard startup remains the default. The Pi4 image assembler's `--fast-boot`
option changes the **normal loader and root session configuration**. It does
not replace its verified U-Boot firmware input or install a rescue selector.
The zero-countdown selector needs its own pinned source build, existing
bootchain identity checks, backup and hardware acceptance. Never replace an
existing `uboot.env` or enroll another card's rescue identity as a speed tweak.

The system UI installer also accepts explicit `--fast-boot`. It requires all
four coordinated root components: session supervisor, unprivileged entrypoint,
preload helper and rc service. It refuses fast activation with incomplete
payloads or `--files-only`; loader/rc changes use the existing backup/rollback
paths. Applying an application bundle alone does not enable this boot profile.

Before application imports, the fast entrypoint validates the active version
and its release-ready manifest identity, then pins that entrypoint for the
process. UI modules and the camera package are preloaded from the same selected
tree. The stable legacy directory is not preloaded, and a pointer switch during
startup cannot mix two releases.

These paths have not been installed on the board. Native FreeBSD/ARM image
assembly is currently blocked by the host WSL failure.

## Measurement definition

Capture these milestones on the same board/card over multiple cold boots:

1. Physical power applied, observed externally.
2. EEPROM/GPU and U-Boot/loader stages, if serial capture is available.
3. `session-start`, `Xorg-started`, Python preload start/end, `display-ready`.
4. `native-splash-handoff`, UI-ready marker, then `camera-frame-presented`.

`camera-frame-presented` is emitted once after the application startup mask
has been dismissed and another Qt frame has actually been presented. Authority
readiness is included separately. An internal software frame still precedes
the physical LCD scanout; the final acceptance is the visible camera interface.

Root and child software markers use the board-local monotonic clock:

```sh
python tools/analyze_boot_trace.py DaOS-camera.log --output boot-stages.json
```

The analyzer isolates the last supervisor session, rejects reversed clock
domains and reports absent stages as unavailable. It deliberately leaves
`power_on_to_UI_ms` null: kernel/app monotonic clocks or USB enumeration are
not an external power-on reference. With only the power/data USB cable, a video
showing power application and the first camera interface is a valid way to
establish that outer interval; it requires no device modification.

验收指标是整机“通电到相机 UI 出现”。桌面 Qt、启动 Logo、Python 启动、USB
连上或摄像服务就绪，都不能单独替代这个指标。现在已准备启动链候选改动，
尚未编译新的 U-Boot、重打整卡镜像或获得实机冷启动时间。
