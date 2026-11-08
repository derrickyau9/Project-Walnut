# DaOS USB 开发接入

当前交付是 Raspberry Pi 4 ARM64 的 DaOS BSD USB 开发镜像，基础系统为
锁定源码构建的 FreeBSD 15.1-RELEASE-p6。USB 设备端提供经预授权公钥认证的
ADB shell 和基本文件传输，默认不开放 TCP ADB 服务。

镜像及其组装证据位于
`F:\Project HasselFree\build\DaOS_BSD_RPi4_USB_ADB_ARM64`。启动与界面的实际
验收请查看该目录的 [VERIFICATION.md](../../build/DaOS_BSD_RPi4_USB_ADB_ARM64/VERIFICATION.md)。
虚拟机不会模拟树莓派的真实 DWC USB-C 设备控制器；界面能启动不代表 USB
实机已经枚举。当前物理 USB-C 枚举、Windows 绑定和供电/线缆组合仍待实机测试。

## 连接板子

1. 将本次 Pi4 镜像写入你明确选定的启动卡；本项目接入脚本不自动写卡。
2. 为 Pi4 准备独立、稳定的供电，把 **USB-C 口留给电脑的数据连接**。
   按下方官方 Pi4 OTG 指南和所用供电板说明确认独立供电方案；指南示例使用
   GPIO 排针的 5V/GND，官方 Pi4 供电建议为 5V/3A。这是供电需求说明，
   不是 GPIO 接线图；本项目尚未验证任何特定供电板与线缆组合。
3. 用支持 USB 数据传输的线连接 **Pi4 USB-C ↔ 这台 Windows 电脑**。
   仅供电的线不能传输 ADB；Pi4 的四个 USB-A 主机口不能作为此设备端入口。
4. 启动镜像，等待系统和后台 USB ADB 服务就绪，再运行下面的电脑客户端。

上述端口与独立供电方式依据 Raspberry Pi 的
[OTG 官方指南](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009276-WP/Using-OTG-mode-on-Raspberry-Pi-SBCs)、
[供电规格](https://www.raspberrypi.com/documentation/computers/getting-started.html#power-supply)
及 [Pi4 USB-C 控制器说明](https://www.raspberrypi.com/documentation/computers/config_txt.html#otg_mode)。
指南中的 Linux `modprobe`/`dtoverlay` 命令不用于本镜像；FreeBSD 模块、设备树
和服务已经由本次镜像集成配方准备，不需要再套用 Raspberry Pi OS 的配置。

## 在这台电脑打开 shell

在项目根目录的 PowerShell 中运行：

```powershell
Set-Location 'F:\Project HasselFree'
.\Connect-DaOS-USB.cmd --list
.\Connect-DaOS-USB.cmd
```

`--list` 立即列出匹配设备。无参数入口最多等待 90 秒，找到已授权设备后打开
交互 shell；`exit` 退出 shell，项目 ADB server 保留供下次连接。
首次启动较慢时可用 `--timeout 300`；只等待并确认连接可用 `--no-shell`。
需要系统中的 Python 3，入口先尝试 `py -3`，再尝试 `python`。

客户端使用 F 盘 `build\DaOS_USB_Development\host-tools\platform-tools\adb.exe`
中的官方 Platform Tools 37.0.1，以及独立项目密钥。ADB server 使用
**127.0.0.1:5049**，不会停止或接管日常使用的 5037 server。
若 5049 已被其他进程占用，改用 `--server-port 5050`；脚本会拒绝接管未知
server，也不会替用户执行全局 `adb kill-server`。

默认只匹配 USB 序列号 `DaOS-USB-ADB-DEVELOPMENT`，再校验 DaOS 设备信息；
普通 Android 手机及 TCP 设备不会被选中。多板调试应为每板配置不同的 DaOS
序列号，再用 `--serial DaOS-BOARD-02 --server-port 5050`；同一筛选器发现多个
可用 transport 时须交互选择或使用 `--select N`。

## 授权密钥

当前私有预览镜像预授权两个公钥：

| 公钥来源 | 项目公钥文件 |
| --- | --- |
| 本项目独立开发密钥 | `daemon/keys/daos-development-adbkey.pub` |
| 这台电脑已有的 Android ADB 公钥 | `daemon/keys/this-pc-adbkey.pub` |

本机公钥文件仅为本地构建输入，不纳入 Git。干净检出后的
`pack_inputs.py` 默认仅授权仓库内的项目开发公钥；使用可重复的
`--adb-public-key /path/to/host-adbkey.pub` 参数可明确指定一个或多个
接收者公钥。显式传入参数会替代默认开发公钥列表，不会更改已发布镜像。

项目私钥仅保存在 F 盘 `build\DaOS_USB_Development\host-tools\keys\adbkey`，
不会写入镜像；这台电脑原来的私钥仍在其用户目录。公钥最终安装到设备的
`/etc/daos/adb_keys`。设备不会自动添加传来的新公钥，也没有 Android 式的
“允许此电脑”弹窗。换电脑、替换或丢失私钥之后，需要明确授权新公钥并重新
生成镜像覆盖层；复制 `.pub` 到电脑或反复插拔线缆不会获得授权。

推荐继续使用项目入口。若改用原有系统 adb，需要同时使用已预授权的原始
私钥和正确 server/设备选择；它不是项目 5049 server 的自动替代品。

## 连接没有出现时

先运行 `Connect-DaOS-USB.cmd --list`。结果为空时，检查供电、数据线、USB-C
端口与设备管理器中的 `VID_FFFF&PID_DA05`；此 VID/PID 仅供本地开发，未获
USB-IF 分配。设备描述符声明 WinUSB 兼容接口，但 Windows 自动绑定仍待真实
硬件验证，不能由安装了 Platform Tools 推断驱动已经工作。

如果出现 `unauthorized`，核对镜像授权公钥与电脑正在使用的私钥是否匹配；
它不是线缆诊断结果。`offline` 或反复断开时同时检查稳定供电和设备日志。
若有本地控制台，可只读检查：

```sh
tail -n 80 /var/log/DaOS-usb-adb.log
kldstat
sysctl hw.usb.template
```

正常集成会在加载自定义模块后选择 template `55813`，后台服务等待 USB 主机；
没有线缆不会阻塞系统启动。现有 shell 按设备服务的 root 权限运行，操作会
直接影响设备文件。已支持 shell/PTY 和基本 push/pull，未实现 Android 应用
安装、remount、转发等 Android 专用服务。

## 已有证据与实现入口

官方 Linux/Windows adb 已分别通过 stdio 适配和 loopback TCP 认证与 shell
测试；真正的 FreeBSD ARM64 参考虚拟机加载了同一模块并通过 shell、PTY、
sync、拒绝未授权密钥及断开清理测试。它使用专用 SSH 转发的 TCP 测试端点，
**不是物理 USB 测试**。模块加载成功、协议成功和实机 USB 枚举是三项独立结论。

- [电脑客户端说明](HOST_CLIENT.md)：5049 server、设备筛选与 Windows 官方工具证据。
- [设备端协议实现](daemon/README.md)：AUTH、shell、PTY、sync 和 stdio 契约。
- [FreeBSD USB 传输与模块](freebsd_usb/README.md)：描述符、端点和原生构建方式。
- [镜像集成配方](integration/README.md)：设备树、模块、后台服务、公钥与覆盖层。
- [BSD Buildroot](../bsd-buildroot/README.md)：默认 USB 目标和独立 NetBSD 目标。
- [FreeBSD ARM64 验证收据](daemon/evidence/freebsd-arm64-001/summary.json)。
- [Windows adb 实际协议测试](daemon/evidence/windows-stdio-integration.json)。
