# DaOS R14 Raspberry Pi 4 ARM64 镜像制作与刷写

USB 开发优先的新产品镜像见
[`DaOS_BSD_RPi4_USB_ADB_ARM64/VERIFICATION.md`](../../../build/DaOS_BSD_RPi4_USB_ADB_ARM64/VERIFICATION.md)，
构建入口见 [`BSD Buildroot`](../../bsd-buildroot/README.md)，接入见
[`USB ADB 指南`](../../adb/README.md)。下文候选 rpi4-r14-002 的数据保留为历史基线；
新 USB 镜像的身份与验证以其独立收据为准。手动刷写步骤同样适用，但应选新 USB 镜像文件。

此配置面向 **Raspberry Pi 4 Model B**，并纳入 **Pi 400** 的专用设备树。
系统使用项目的 FreeBSD 15.1-RELEASE-p6 ARM64 world、`DaOS` 内核、原生相机程序、
Qt/Python 运行时和完整 R14 界面。普通启动路径为 HDMI 图形界面，使用 USB 键盘、鼠标
或兼容的 USB 触摸设备操作。

候选 **rpi4-r14-002 已完成完整镜像组装**，EFI FAT32 与四个 UFS 文件系统的
只读检查均通过。交付目录为
[`build/DaOS_R14_RPi4_ARM64`](../../../build/DaOS_R14_RPi4_ARM64)，
原始装配收据、各项检查日志、内核及固件来源均已保存。
真实 QEMU `virt` ARM64 启动已观察到内核 Boot Logo、R14 相机及合成预览，
System 页显示 FreeBSD 15.1-RELEASE-p6 / arm64。Logo 的 743×554 个可见像素与输入 PNG
完全一致。独立隔离 ARM64 虚拟实例已完成各应用入口、参数确认、一次原生合成拍摄、
回放与导出；离线核验确认唯一拍摄和导出文件一致。第二次完整 kernel 启动后，原请求、
配置与 DNG 恢复一致且没有新 shutter；停止后的独立比对通过。
Taskbar 手势专项验收与 Pi 4B/400 实机验证仍为 Pending。
详见独立的 [`VERIFICATION.md`](../../../build/DaOS_R14_RPi4_ARM64/VERIFICATION.md)。

| 组装产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| [`DaOS-R14-RPi4-arm64.img`](../../../build/DaOS_R14_RPi4_ARM64/DaOS-R14-RPi4-arm64.img) | 3,254,820,864 | `f6911768c6112a178ae999d916443d8acacabafaf3a8e96e588b1f2a9a402fc1` |
| `DaOS-R14-RPi4-arm64.img.xz` | 331,319,280 | `869f56015fbb6d8d618ecb97817cbccc3518b13f91fb7240b3c86b24639354c3` |

[`SHA256SUMS.txt`](../../../build/DaOS_R14_RPi4_ARM64/SHA256SUMS.txt)
同时列出压缩镜像、装配收据、构建证据以及原始 JPEG / 转换后 PNG Logo 的身份。
Logo 原始图片 SHA-256 为
`153a528048e42f4534259708a1efbca4ee88c7fa63151f297572f39fc21f043d`。
这些文件哈希用于核对这份交付；是否通过真实启动仍以另行保存的验收结果为准。

## 板卡与介质

- Raspberry Pi 4B / Pi 400；此配置不承诺 Pi 5、CM4 或其他板型可直接使用。
- 至少 8 GB 的 microSD 卡，以及对应板型的稳定电源。
- 将显示器连接 **HDMI0**；Pi 4B 上为靠近 USB-C 电源口的 micro-HDMI 口。
- 使用 USB 键盘、鼠标进行首次操作。DSI 显示屏、GPIO 按键及专用触摸控制器需要另行验证。
- EEPROM 引导程序必须支持 GPT。2021 年起的官方 EEPROM 发布记录已包含 GPT/FAT
  支持与修正；旧 EEPROM 应先通过树莓派官方流程检查版本。本镜像构建或刷写流程不更新 EEPROM。

本配置使用单个、位于 GPT 表最前面的 FAT32 EFI 分区，不使用 `autoboot.txt`
进行多 FAT 分区切换。树莓派固件负责找到该 FAT 分区，之后的系统根分区由 BSD loader
通过 GPT 标签选择。[官方 EEPROM 发布记录](https://raw.githubusercontent.com/raspberrypi/rpi-eeprom/master/firmware-2711/release-notes.md)
和[维护者关于 GPT 分区顺序的说明](https://github.com/raspberrypi/rpi-eeprom/issues/654)
解释了相关要求。

## 启动链与分区

启动链为：EEPROM → Raspberry Pi GPU 固件及板型 DTB → `u-boot.bin` →
`EFI/BOOT/BOOTAA64.EFI` → `DaOS` FreeBSD 内核 → Xorg SCFB → R14 相机界面。
ARM64 原生应用和系统镜像不依赖主机目录共享。

| 分区标签 | 默认大小 | 内容 / 挂载点 |
| --- | ---: | --- |
| `DaOS-efi` | 128 MiB | 首分区 FAT32；Pi 固件、U-Boot、DTB、EFI loader |
| `DaOS-root` | 2560 MiB | UFS2；只读 `/`、系统、运行时和 R14 源码 |
| `DaOS-state` | 128 MiB | UFS2；可写 `/var`、配置、相机日志及 UI 状态 |
| `DaOS-calibration` | 32 MiB | UFS2；独立 `/var/DaOS/calibration` |
| `DaOS-photos` | 256 MiB | UFS2；独立 `/var/DaOS/camera/photos` |

默认分区不会自动扩展到整张卡。8 GB 是介质容量下限；默认照片分区仍为 256 MiB。
`/tmp` 使用 tmpfs。分区按 GPT 标签挂载，避免依赖 SD/USB 设备枚举顺序。

## 固定输入与 Boot Logo

已获取的官方 FreeBSD 软件包输入为：

| 输入 | 版本 | 来源 |
| --- | --- | --- |
| `rpi-firmware` | `1.20260521.g20260521` | `sysutils/rpi-firmware`，`FreeBSD:15:aarch64` |
| `u-boot-rpi-arm64` | `2026.07` | `sysutils/u-boot-rpi-arm64`，`FreeBSD:15:*` |

获取过程仅使用隔离目录中的 `pkg fetch`，未安装包或执行包脚本。
本地 `build/pi4-firmware/firmware-receipt.json` 保存软件包版本、来源、许可、原始包哈希
和 46 个所选启动/许可文件的哈希；`manifests/` 保留原包 manifests。
`payload/rpi-firmware/` 包含官方 `config_arm64.txt`、Pi 4B/400/CM4 DTB、
`start4*.elf`、`fixup4*.dat`、`armstub8-gic.bin` 及三个官方 overlays。
`payload/u-boot-rpi-arm64/` 包含 U-Boot。各组件许可单独保留。

装配器保留官方 `config_arm64.txt` 的 64 位、U-Boot 和 armstub 选项，追加 HDMI
800×600、60 Hz 配置，并设置 `disable_splash=1` 关闭 Pi 固件的默认彩虹 splash。
该设置不等于从上电起全程显示自定义图片。

Boot Logo 使用用户提供的猫图片，工程输入为
`assets/boot-logo/DaOS-logo.png`，系统安装位置为 **`/boot/images/DaOS-logo.png`**。
当前图片为 743×554、8 位 RGBA PNG，保持原图完整画面；转换记录位于
`assets/boot-logo/provenance.json`。装配器要求 PNG 尺寸不超过 800×600，且不在装配时缩放。

Logo 依赖重新构建的内核，而不仅是添加 `loader.conf`：

```text
include GENERIC
ident DaOS
device splash
```

完成的 base-build 收据必须绑定该配置内容、配置哈希及内核产物哈希，并标记
`kernel_config.device_splash=true`。装配器拒绝未包含 `device splash` 的旧内核。
同一内核还需保留 `vt`、EFI framebuffer、FDT/Broadcom、SDHCI 和 USB 输入所需设备；
使用 pinned ARM64 `GENERIC` 作为基础，不应随意裁掉相关驱动。

生成的 `/boot/loader.conf` 包括：

```ini
console="efi"
boot_mute="YES"
splash="/boot/images/DaOS-logo.png"
kern.vt.splash_cpu="0"
```

这是 FreeBSD 内核启动阶段的 PNG splash。正常图形路径还要求固件/U-Boot 提供有效
framebuffer、BSD `efifb` 可映射、Xorg SCFB 成功启动；插入 Logo 文件本身不能证明这些条件。
实现依据见固定版本的
[loader 默认配置](https://raw.githubusercontent.com/freebsd/freebsd-src/95719d8a895bf8170851e9844d9b4092b1d2b6fe/stand/defaults/loader.conf)、
[splash 数据加载](https://raw.githubusercontent.com/freebsd/freebsd-src/95719d8a895bf8170851e9844d9b4092b1d2b6fe/stand/common/gfx_fb.c)
及[官方 RPI 发布配置](https://raw.githubusercontent.com/freebsd/freebsd-src/95719d8a895bf8170851e9844d9b4092b1d2b6fe/release/arm64/RPI.conf)。

## 制作流程

基础 world/kernel 的复现流程见 [`../base/README.md`](../base/README.md)，
运行时与原生相机证据要求见 [`../image/README.md`](../image/README.md)。
本次板级装配器为 [`assemble_pi4.py`](assemble_pi4.py)，必须在原生 FreeBSD 构建环境执行。
它验证 source-built base、ARM64 运行时、已经过目标平台测试的原生相机程序、固件收据、
Logo 和内核 splash 配置，随后使用 `makefs`、只读 filesystem check 和 `mkimg` 生成镜像。

下面是 **FreeBSD 构建 guest 内** 的命令形状。输入目录必须已经完整上传；
`--work-dir` 必须是新目录。例子的 `003` 是后续独立候选编号，不得覆盖已经成功的
`002` 或已有失败日志。

```sh
python3.12 -B /opt/DaOS-image-input-pi4-001/system/freebsd/raspberrypi/assemble_pi4.py plan \
  --architecture arm64 \
  --base-state /var/daos-builds/daos-base-arm64-pi-splash-001/build-state.json \
  --runtime /var/DaOS-runtime-arm64-r10-004 \
  --project /opt/DaOS-image-input-pi4-001 \
  --native-command /opt/hasslefree/build/freebsd-arm64-native-001/hf-camerad \
  --native-evidence /opt/DaOS-image-input-pi4-001/_evidence/arm64-target-validation.json \
  --firmware-dir /var/DaOS-pi4-firmware-003 \
  --work-dir /var/DaOS-image-rpi4-r14-003 \
  --root-mib 2560 --compress
```

审阅计划后，以完全相同输入将 `plan` 换为 `build`。`plan` 不生成镜像。
装配器在 GPT 生成前检查 EFI FAT32 和四个 UFS 文件系统；任一检查失败都不能成为交付镜像。
预期产物名称为 `DaOS-R14-RPi4-arm64.img`、同名 `.img.xz` 和 `image-evidence.json`。
`image-evidence.json` 中的 raw/XZ 字节数及 SHA-256 才是该次候选的身份。

当前工作区的 `build/build_pi4_image.py`、`build/fetch_pi4_firmware.py` 是分阶段传输助手。
它们采用独立、创建式输出路径，已经存在的输入包、候选和收据不可直接覆盖重跑；
复现时应选择新编号及对应的新路径，保留此前结果。

## 手动刷写 microSD

构建流程不会自动向物理盘写入。完成候选的校验后，由操作者执行刷写：

1. 用交付目录中的 `image-evidence.json` / `SHA256SUMS.txt` 核对所选 `.img.xz` 或 `.img`
   的 SHA-256。Windows 可用 `Get-FileHash -Algorithm SHA256 <文件路径>`。
2. 在 Raspberry Pi Imager 或兼容镜像工具中选择自定义镜像，再明确选中目标 microSD。
   按容量和设备信息核对目标；此步会覆盖该卡原有内容。
3. 完成写入及工具自带的写后校验，安全弹出介质。Windows 若提示格式化 BSD 分区，应取消。
4. 将卡插入断电的 Pi 4B/400，连接 HDMI0、USB 输入和电源，观察完整启动。
   不要将普通 QEMU/AAVMF 镜像文件当作 Pi 镜像刷入。
5. 首次检查自定义 Logo、R14 相机预览、任务栏左右手势及七个入口；
   再检查一次合成拍摄、回放、导出及正常关机重启后的同一请求恢复。

此原型当前使用 HF-Sim 合成传感器。树莓派启动成功不表示 IMX410、CSI、GPIO 控制器
或其他真实相机硬件已集成。没有正常关机证据时，不应把直接拔电当作重启恢复验收。

## 验证状态分开记录

| 验证层级 | 必需证据 | 当前说明状态 |
| --- | --- | --- |
| 文件系统/静态装配 | 新候选的五项只读检查、输入/输出哈希、GPT/FAT/UFS 结构、Logo 与固件路径 | **已完成组装**；候选 002 的五项只读检查通过，见 `image-evidence.json` 与 `build-evidence/filesystem-validation.json` |
| 通用 ARM64 虚拟机 | 从该候选真实启动，观察 Logo、完整 R14 UI、输入操作和正常关机 | **已观察 Logo、相机/预览、各应用入口、参数确认、一次拍摄、回放导出及完整重启恢复**；两次停止后的原请求/配置/DNG 独立比对通过；Taskbar 手势专项验收仍 Pending |
| Pi 4B/400 实机 | 记录板型、EEPROM、HDMI0、USB、介质与实际启动/操作/恢复证据 | **Pending** |

QEMU `virt` 使用通用虚拟 ARM64 硬件。即使同一镜像能在该环境启动，也不能替代 Pi EEPROM、
GPU 固件、U-Boot、SD 控制器、HDMI 和 USB 的整板验收。交付时应保留原始装配收据，
另加虚拟机和实机验收记录，不回写历史记录使其看似已经验证。

本次 QEMU 证据中的 `virtual-boot/frame-04.png` 是真实内核 Logo 画面，
`virtual-boot/boot-logo-validation.json` 记录像素核对；`virtual-boot/frame-25.png`
显示 R14 相机和合成预览，`operation/system.png` 显示运行平台。
较早的 `operation/diagnostics.png`、`devices.png`、`settings.png`、`recipes.png`
受到鼠标/人工输入干扰，文件名不能证明对应页面已经通过测试；这些图片保留为原始记录，
不计入导航通过项。独立隔离实例的 `operation-validation/` 图片已逐项观察正确，
`evidence/validation/first/validation.json` 完成离线核验：唯一请求
`f8f505a1-277a-49d1-b2e5-9d4ebb30f2af`，128×96 合成 Bayer/DNG，实际曝光 4000 µs、
增益 1.25，原始和导出 DNG 均为 25,666 字节，SHA-256 相同。
QMP 正常关机请求后 QEMU 返回 0，应用与 Xorg 都返回 0 且未强制终止；
EFI console 的串口日志未观察到 `All buffers synced`，不声称存在该同步标记。
`evidence/validation/resumed/validation.json` 已完成第二次完整 kernel 启动后的独立核验，
`capture_rows_unchanged`、`settings_unchanged`、`retained_DNG_unchanged` 及
`restart_recovery_verified` 均为 true；总计两个 guest session，期间没有新 shutter。
两次应用/Xorg 都正常返回 0，串口同步标记的限制保持不变。

本文和交付验证报告是在镜像组装后更新的文档。镜像内源文件身份以
`image-evidence.json` 和 `build-evidence/source-package-manifest.json` 的哈希为准，
不能据当前工作区文档已变化推断镜像内容也已变化。
