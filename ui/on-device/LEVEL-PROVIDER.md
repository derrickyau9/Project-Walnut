# Electronic level input

Project Walnut's level is a display feature. This release includes no IMU
driver, automatic attitude discovery, or claim of calibrated physical angles.
It starts disabled. Enable it in Display settings; without a provider it shows
an unavailable state and dashes, rather than invented zero-degree readings.
Only the separate `level-preferences.json` stores the enabled setting. Existing
language, time-zone, developer-mode, camera, and calibration settings are not
migrated or rewritten.

## Optional local producer

Launch the UI with `--attitude-file /absolute/local/attitude.json`. A future
hardware adapter must write one UTF-8 JSON object with exactly these fields:

```json
{
  "format": "Project-Walnut-attitude-v1",
  "valid": true,
  "monotonic_ns": 12000000000,
  "roll_deg": 1.25,
  "pitch_deg": -2.5
}
```

The sample timestamp above is illustrative: the producer must use the current
local machine's `time.monotonic_ns()` at measurement time, from the same boot
and clock domain as the UI. UTC, Unix time, timestamps from a remote computer,
and values saved before reboot are unsuitable. Samples aged 0–500 ms inclusive
are accepted; future or older timestamps clear both displayed angles. The UI
polls every 100 ms by default.

`valid` must be the boolean `true`; timestamps must be positive integers below
2^63. Roll must be a finite number in [-180, 180] degrees, and pitch in [-90, 90].
Booleans, NaN/infinity, duplicate or extra fields, wrong formats, and malformed
JSON are rejected. The input must be a nonempty ordinary local file of at most
4096 bytes. Symbolic links/reparse points and special files are refused; reads
use nonblocking mode where the platform supports it.

Publish by writing a complete temporary file in the same directory and
atomically replacing the target. A producer should remove the file when
disconnected or publish `valid: false` when a measurement is invalid. Never
reuse the last successful sample indefinitely. Missing input is unavailable;
invalid input and stale input have their own visible states. All clear the
numeric values.

The adapter is responsible for mounting orientation, sign conventions,
calibration, and whether its measurements can be trusted. The overlay rotates
its line by negative roll and moves it downward for positive pitch; pitch
movement is visually limited while the numeric value remains available.
Alignment means both absolute values are at most 0.5 degrees. This display
threshold is not a hardware accuracy specification. This local JSON interface
does not authenticate a sensor and does not change capture/RAW metadata.

## Explicit demonstration

Use `--demo-attitude ROLL PITCH` to show a level without a hardware provider.
This is mutually exclusive with `--attitude-file`. The overlay clearly labels
the values **DEMO**; they do not expire because they are demonstration values,
not measurements. Demo launch enables the level only in memory and does not
persist sample angles. Do not write fabricated demo values into a real
provider file.

The controller's `clearDemo()` restores a configured file provider, or the
unavailable state if there is none. Save failures retain the previous enabled
state and expose `settingsError`; they do not silently rewrite other settings.

## 中文说明

水平仪默认关闭。本版本没有集成真实 IMU 驱动；开启后没有新鲜数据时显示未连接、
无效或已过期状态，并清空角度，不会伪造为零度。真实提供者使用同一次启动的本机
单调时钟，每条记录有效期最多 500 毫秒，并通过原子替换发布完整 JSON。

演示使用 `--demo-attitude 横滚 俯仰`，界面明确标注 DEMO，不能视作传感器读数。
启用偏好单独保存；样例角度不进入配置、拍摄记录、RAW 或校准数据。
