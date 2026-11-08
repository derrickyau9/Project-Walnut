# DaOS BSD Buildroot

DaOS 的 BSD 源码构建系统当前默认目标为 **Raspberry Pi 4 / ARM64 USB 开发镜像**，
配置是 `rpi4-aarch64-usb`，后端是 `freebsd_usb`。它以锁定的 FreeBSD
15.1-RELEASE-p6 源码为基础，组合 Boot Logo、当前相机界面、原生相机服务及
需要预授权公钥的 USB ADB shell。Qt/PyQt 运行时的软件包来源仍单独记录；
不能把它称为全部组件从源码编译或独立研发的 DaOS 内核。

NetBSD 11.1 源码构建目标继续保留，使用时必须显式选择对应 profile。
两个后端的基础系统、工具链和运行库各自核验，不混用 ABI。

## 默认 USB 目标

本机项目位于 `F:\Project HasselFree`，Debian WSL 的文件系统位于
`F:\WSL\Debian`。Windows 入口委托 WSL；默认 USB 工作区为
`/home/dy09/daos-bsd-buildroot-usb-scratchguard-002`。实际原生构建在已准备的隔离 FreeBSD
构建虚拟机中，通过它的 root SSH 执行，输出在该虚拟机的私有目录中。

```powershell
.\Build-DaOS-BSD.cmd doctor
.\Build-DaOS-BSD.cmd plan
.\Build-DaOS-BSD.cmd configure
.\Build-DaOS-BSD.cmd build --stage base
.\Build-DaOS-BSD.cmd build --stage usb
.\Build-DaOS-BSD.cmd packages
.\Build-DaOS-BSD.cmd image
.\Build-DaOS-BSD.cmd status
```

`plan` 无构建副作用，也不连接 SSH。`configure` 核验源码锁、现有源码基线及
执行配方快照；`build --stage base` 默认实际核验并复用已完成的 source world
与 splash 内核，收据写明 `reused:true, executed:false`。只有随后 `usb`、
`packages`、`image` 阶段分别进行原生模块编译、覆盖层打包和新镜像组装。
`all` 按此顺序执行完整流水线；它不会自行启动构建虚拟机。

profile 声明的源码归档、运行时、固件、原生相机产物、公钥和项目快照必须已
准备好。构建虚拟机若已停止，在另一个 WSL 终端启动这个已准备的实例，再运行
`configure` 或 `all`：

```sh
python3 tools/freebsd_vm.py serve --work-dir /home/dy09/hf-task-freebsd-r10/vm-001 --memory-mib 16384 --cpus 12
```

完整重编 source world/kernel 要显式指定 `--rebuild-base`，并选择新工作区：

```powershell
.\Build-DaOS-BSD.cmd all --rebuild-base --workspace /home/dy09/daos-bsd-full-rebuild-001
```

源码锁为 `system/freebsd/base/source-p6-arm64.lock.json`，固定提交
`95719d8a895bf8170851e9844d9b4092b1d2b6fe`、实际源码归档大小及 SHA256。
基础系统、SDK、模块与镜像各自有可核验收据。旧 world 对象缓存已裁剪；
现有 SDK 和 splash 内核对象复用不会被记为重新编译了工具链。
参数、路径及 API 见 [USB 目标配方](targets/rpi4-aarch64-usb/README.md)。

**再次运行 `all` 或 `image` 之前**，还要在构建虚拟机内检查
`mount -p` 和 `df -h /var/DaOS-pi4-usb-build-memory`，确认该路径确实是已挂载、
空间足够的 12 GiB tmpfs。构建虚拟机重启后，此临时挂载及内容会消失；
只有同名目录不够，未挂载时会回落到剩余空间约 5.9 GiB 的根磁盘。
后端在新镜像组装前会核对精确 tmpfs 挂载点和 `statvfs` 可用空间，缺失或
不足时拒绝进入组装；它不自动挂载或删除旧产物。空间要求为全部分区容量的
三倍加 32 MiB，覆盖准备树、分区文件和 raw 镜像同时存在的情况。
F 盘已导出的 `.img`/`.img.xz` 是持久文件，不依赖该 tmpfs。

## 当前交付和验证

当前已导出的 USB 镜像为
`F:\Project HasselFree\build\DaOS_BSD_RPi4_USB_ADB_ARM64\DaOS-BSD-RPi4-USB-ADB-arm64.img`
及其 `.img.xz` 压缩版；同目录的 `image-evidence.json` 是实际组装证据。
本次镜像由相同的独立原生配方阶段组装。新主 CLI 的实际验收目前覆盖
Windows `plan`、`configure` 和 `build --stage base`，以及 60 项 host/mock
回归测试；尚未通过新 CLI 执行 `usb`、`packages`、`image` 或完整
`--rebuild-base`，不能把这部分接口测试算成又完成一次构建。

初次基线验证保留在 `evidence/freebsd-usb-target-001`。空间门禁修订后的验证
使用新工作区 `/home/dy09/daos-bsd-buildroot-usb-scratchguard-002`，收据保留在
`evidence/freebsd-usb-target-002`；旧工作区和证据未覆盖。
后续更改配方导致输入哈希变化时，应明确指定新的 `--workspace`，不要复用
不匹配的编译缓存或篡改旧状态。

[USB 接入指南](../adb/README.md) 说明 USB-C 数据线、独立供电、预授权公钥和
5049 端口。运行根目录 `Connect-DaOS-USB.cmd` 等待唯一匹配的 USB 设备并打开
shell。ARM64 FreeBSD 参考虚拟机已加载实际模块，并通过官方 adb 的认证、
shell、PTY 和文件传输测试；这些协议测试使用私有 loopback TCP 和 SSH 转发。
树莓派实机 USB-C 枚举、Windows WinUSB 绑定及线缆行为仍未验收。
最终镜像启动和界面测试以发行目录的
[VERIFICATION.md](../../build/DaOS_BSD_RPi4_USB_ADB_ARM64/VERIFICATION.md) 为准。

## 显式 NetBSD 目标

NetBSD UI 目标使用独立工作区 `/home/dy09/daos-bsd-buildroot-ui`，默认源码锁为
`sources-ui.lock.json`，包含五个官方源码包及逐包 SHA512。元数据签名验证记录
位于 `references/NetBSD-11.1-metadata-verification.json`。

```powershell
.\Build-DaOS-BSD.cmd plan --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd fetch --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd configure --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd build --stage tools --jobs 16 --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd build --stage kernel --jobs 16 --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd build --stage distribution --jobs 16 --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd packages --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd image --profile rpi4-aarch64-ui
.\Build-DaOS-BSD.cmd status --profile rpi4-aarch64-ui
```

`rpi4-aarch64` 是不含 Qt 运行时的最小 NetBSD 目标，`qemu-aarch64` 是虚拟平台
目标。基础系统、X11、内核和原生相机服务从源码构建；Qt/PyQt 及其 pkgsrc
依赖使用单独锁定的 NetBSD 软件包。源码展开后记录全树内容指纹；失败尝试、
日志及收据保留。现有软件包集绑定的旧 coordinator 已归档，后续 CLI 变更
不会被当作旧镜像的原始构建来源。

已完成的 NetBSD 源码工具链、distribution、内核、软件包和镜像收据继续保留。
Boot Logo 经 NetBSD `genfb`/`SPLASHSCREEN` 编译入内核，ARM64 QEMU 已验证
显示、启动和界面交互。NetBSD 的 `vm.py` 采用 USB 键盘及相对 USB 鼠标；
它和 USB 开发镜像的 FreeBSD 虚拟机入口各自独立。

## 目标硬件边界

虚拟 ARM64 启动和界面测试不能替代 Pi4 的 HDMI、USB 控制器、SD 卡、网络
或真实传感器验收。相机仍使用现有模拟采集服务；原 DaOS TypeScript 通用
运行时尚未整合进镜像。USB 设备端已实现协议和原生模块，实机结果以最终
发行验证记录中的明确结论为准。
