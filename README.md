# Project Walnut

**They made the leaf. We cracked the nut.**

**OPEN HARDWARE. · OPEN FIRMWARE. · OPEN PHOTOGRAPHY.**

Project Walnut is an effort to build a professional camera ecosystem that belongs to its users. Photographers and developers should be able to understand, manufacture, repair, modify and extend their equipment, and keep maintaining it after its original maker moves on.

Project Walnut 希望建立真正属于用户、由社区共同发展的专业数码相机生态。摄影师不应只能使用被允许的功能；开发者不应只能面对无法理解的黑箱；设备的寿命也不应完全取决于制造者是否继续支持它。

Walnut，核桃。我们希望打开设备的外壳与黑箱，让硬件、固件和摄影工作流都成为用户可以理解、维修、修改和持续创造的工具。

[镜像下载 / Releases](https://github.com/derrickyau9/Project-Walnut/releases) · [项目命名](docs/product/naming.md) · [产品规划](docs/product/ff1-prd.md) · [相机功能说明](docs/product/camera-apps.md) · [硬件工程](hardware/README.md) · [构建系统](system/bsd-buildroot/README.md)

## 为什么做这件事 / Why it exists

我们希望让功能的边界取决于硬件能力与社区的创造力，让更新、维修、适配和长期维护成为用户可以参与的事情。摄影始终是第一功能；开放接口和可编程工作流服务于拍摄，而不是让相机成为一个通用管理后台。

保有独立研究、修改和发布成果的空间，让设备的控制权与长期维护能力属于用户。

The goal is an open platform whose limits are determined by hardware and engineering, with documented interfaces and a community that can sustain it. Photography comes first. Programmability should make the camera more useful to its owner.

成功不只是造出一台相机。更重要的标准是：其他开发者能够依据公开的设计和明确的开放许可，制造自己的相机、修改系统、扩展功能并继续维护，无须逐次向原作者请求授权。

## 一个计划，三个层次 / Three connected layers

| 层次 / Layer | 定位 / Purpose |
| --- | --- |
| **Project Walnut** | 开放摄影生态、工程资料与社区协作的整体计划。The wider ecosystem and engineering effort. |
| **DaOS Camera** | 面向摄影的操作系统集成、硬件抽象、原生相机服务、界面与工作流。The camera software platform. |
| **Walnut FF-1** | 计划中的第一款整机，以可用的专业全画幅无反相机为目标。The first planned camera body and architecture proving ground. |

### 开放的方向 / What we are building toward

- **Open Hardware**：可编辑的机身、电路板、传感器接口、模块化结构与制造资料。
- **Open Firmware**：可研究、编译、修改和维护的 ARM64 相机软件，包含 BSD/Unix 系统集成。
- **Open Photography**：公开采集、RAW、图像处理、校准绑定和相关开发接口。
- **Modular Lens Mount**：可更换卡口模组、清楚的接口边界与适配机制。
- **Community Development**：让制造、维修、移植、功能扩展和长期维护能够由社区接续。

## 当前进度 / Current status

**这是研发中的原型项目，尚不是完成的全画幅相机产品。** FF-1 的真实传感器、FPGA 高速采集、实测色彩校准、镜头电子控制、自动对焦和其他机身硬件能力仍有独立的工程验证工作。

当前可运行的软件原型包含 C++17 相机服务、SQLite 日志、Bayer/DNG 合成采集、Qt Quick 界面、保留媒体工作流和 USB 开发工具。树莓派研发设备已运行 FreeBSD ARM64 相机界面，并验证过 DSI 显示、触摸和 USB ADB；图像源仍为 **HF-Sim 合成场景**。

The running prototype uses **HF-Sim**, a synthetic image source. Screenshots and simulator captures demonstrate software behavior; they do not establish physical sensor acquisition, calibrated color, recording performance or production hardware readiness.

| 工程范围 / Area | 当前边界 / Present boundary |
| --- | --- |
| 相机服务、合成 RAW 与日志 | 已有原生实现和自动测试。Native implementation and tests exist. |
| Qt 相机界面 | 摄影、回放、工作区、工作流及系统页面持续迭代。Camera-first interface under active development. |
| 树莓派显示、触摸、USB | 研发设备有实机记录；每份 Release 镜像仍以自己的验证说明为准。Development-board evidence is separate from image acceptance. |
| USB 急救与异常回退 | 已在固定研发设备验证独立 RAM 急救、USB 回连和正常系统恢复；绑定设备的恢复镜像不能当作通用整卡镜像。 |
| 板载 Wi-Fi | 实机连接尚未验证成功。Physical connectivity remains unfinished. |
| 双轴水平仪 | 提供姿态显示与数据输入接口；当前没有已验证的实机 IMU 数据或校准，演示数据独立标示。 |
| OpenBSD 迁移 | 候选完成 ARM 虚拟机启动、认证 SSH 和传输测试；尚未替换实机主系统。Experimental migration, not the released main OS. |
| FF-1 整机与真实图像链 | 硬件设计、接口和验证工作仍在推进。Hardware and acquisition work remains in progress. |

## 当前软件功能 / Software features

### 拍摄与工作区 / Capture and workspaces

- 摄影优先的取景界面、曝光与增益设置、网格、明暗主题和可隐藏任务栏。
- 可开关的双轴电子水平仪，显示横滚、俯仰及调平状态；缺失或过期的姿态数据会失效，演示角度明确标记 DEMO。真实 IMU 接入与校准仍需实机验证。
- 十二格 Q 菜单、My Menu、C1–C3 工作区、六种布局、工作区导入导出及 Fn 输入边界。
- 原生相机控制、独占控制租约、参数确认和原请求 ID 恢复；未知结果不会自动重拍。
- 独立的合成实时预览及 Bayer/DNG 保存。预览配置不等于实测帧率或真实传感器能力。

### 回放与图像工作流 / Playback and image workflow

- 保留照片浏览、原像素分块查看、元数据、评分、保护和可恢复移除。
- 调整参数以旁车文件保存；曝光、对比度、饱和度、旋转和居中裁切保留原始 RAW。
- DNG 导出，以及有尺寸上限、未校准的 SDR JPEG 校样导出。
- 图像风格、配方编辑、包围拍摄计划、执行进度、暂停、继续、取消和任务汇总。

### 联机与设备工具 / Connectivity and device tools

- USB 开发入口、相机联机协议、预览和媒体传输接口，以及配对式网络联机的软件实现。
- 系统偏好、配置备份恢复、更新包检查、诊断和能力边界展示。
- 触摸键盘、离线拼音输入和终端界面。需要目标系统服务或硬件的功能会显示其可用状态。

界面中存在的视频参数工作区不表示视频采集和编码已经完成；网络页面也不表示板载无线已通过实机验收。

## 界面截图 / UI screenshots

中英文截图按相同功能对应展示。取景示例与回放、比较、评分和编辑演示使用项目对话提供的三张 JPEG 照片，通过实际 Qt 界面渲染。底层拍摄控制与合成 RAW 仍由 HF-Sim 提供；样片不作为传感器或硬件性能证明。

The English and Simplified Chinese galleries cover the same interface states. User-provided JPEG samples demonstrate the viewfinder, playback and editing through the actual Qt UI. Capture control and synthetic RAW remain HF-Sim; the sample photographs are not sensor or hardware validation.

<!-- UI_GALLERY_START -->
**50 个对应功能界面 × 2 种语言，共 100 张截图。** [完整图库和源码对应清单 / Gallery and source manifest](docs/screenshots/README.md)

| English | 简体中文 |
| --- | --- |
| **Capture and sample preview** | **拍摄与示例预览** |
| ![Capture and sample preview — English desktop preview](docs/screenshots/en_US/01-capture.png) | ![拍摄与示例预览 — 中文桌面预览](docs/screenshots/zh_CN/01-capture.png) |
| **Level unavailable** | **水平仪未连接** |
| ![Level unavailable — English desktop preview](docs/screenshots/en_US/01b-level-unavailable.png) | ![水平仪未连接 — 中文桌面预览](docs/screenshots/zh_CN/01b-level-unavailable.png) |
| **Level aligned demo** | **水平仪水平示例** |
| ![Level aligned demo — English desktop preview](docs/screenshots/en_US/01c-level-demo-aligned.png) | ![水平仪水平示例 — 中文桌面预览](docs/screenshots/zh_CN/01c-level-demo-aligned.png) |
| **Level tilted demo** | **水平仪倾斜示例** |
| ![Level tilted demo — English desktop preview](docs/screenshots/en_US/01d-level-demo-tilted.png) | ![水平仪倾斜示例 — 中文桌面预览](docs/screenshots/zh_CN/01d-level-demo-tilted.png) |
| **Application taskbar** | **应用任务栏** |
| ![Application taskbar — English desktop preview](docs/screenshots/en_US/02-taskbar.png) | ![应用任务栏 — 中文桌面预览](docs/screenshots/zh_CN/02-taskbar.png) |
| **Exposure and analogue gain** | **曝光与增益** |
| ![Exposure and analogue gain — English desktop preview](docs/screenshots/en_US/03-exposure-editor.png) | ![曝光与增益 — 中文桌面预览](docs/screenshots/zh_CN/03-exposure-editor.png) |
| **Photo quick controls** | **照片十二格快捷面板** |
| ![Photo quick controls — English desktop preview](docs/screenshots/en_US/04-quick-photo.png) | ![照片十二格快捷面板 — 中文桌面预览](docs/screenshots/zh_CN/04-quick-photo.png) |
| **Video layout and capability limits** | **视频布局与能力边界** |
| ![Video layout and capability limits — English desktop preview](docs/screenshots/en_US/05-quick-video.png) | ![视频布局与能力边界 — 中文桌面预览](docs/screenshots/zh_CN/05-quick-video.png) |
| **Quick-control assignment** | **快捷功能分配** |
| ![Quick-control assignment — English desktop preview](docs/screenshots/en_US/06-quick-assignment.png) | ![快捷功能分配 — 中文桌面预览](docs/screenshots/zh_CN/06-quick-assignment.png) |
| **My Menu** | **我的菜单** |
| ![My Menu — English desktop preview](docs/screenshots/en_US/07-my-menu.png) | ![我的菜单 — 中文桌面预览](docs/screenshots/zh_CN/07-my-menu.png) |
| **C1–C3 custom profiles** | **C1–C3 记忆** |
| ![C1–C3 custom profiles — English desktop preview](docs/screenshots/en_US/08-custom-profiles.png) | ![C1–C3 记忆 — 中文桌面预览](docs/screenshots/zh_CN/08-custom-profiles.png) |
| **Fn and dial mappings** | **Fn 与拨轮映射** |
| ![Fn and dial mappings — English desktop preview](docs/screenshots/en_US/09-input-mappings.png) | ![Fn 与拨轮映射 — 中文桌面预览](docs/screenshots/zh_CN/09-input-mappings.png) |
| **Workspace configuration sharing** | **工作台配置分享** |
| ![Workspace configuration sharing — English desktop preview](docs/screenshots/en_US/10-layout-import.png) | ![工作台配置分享 — 中文桌面预览](docs/screenshots/zh_CN/10-layout-import.png) |
| **Exposure settings** | **曝光菜单** |
| ![Exposure settings — English desktop preview](docs/screenshots/en_US/11-exposure-menu.png) | ![曝光菜单 — 中文桌面预览](docs/screenshots/zh_CN/11-exposure-menu.png) |
| **Image and RAW** | **图像与 RAW** |
| ![Image and RAW — English desktop preview](docs/screenshots/en_US/12-image-raw.png) | ![图像与 RAW — 中文桌面预览](docs/screenshots/zh_CN/12-image-raw.png) |
| **Display and framing** | **显示与构图** |
| ![Display and framing — English desktop preview](docs/screenshots/en_US/13-display.png) | ![显示与构图 — 中文桌面预览](docs/screenshots/zh_CN/13-display.png) |
| **Storage information** | **存储信息** |
| ![Storage information — English desktop preview](docs/screenshots/en_US/14-storage.png) | ![存储信息 — 中文桌面预览](docs/screenshots/zh_CN/14-storage.png) |
| **Wireless network** | **无线网络** |
| ![Wireless network — English desktop preview](docs/screenshots/en_US/15-wifi.png) | ![无线网络 — 中文桌面预览](docs/screenshots/zh_CN/15-wifi.png) |
| **System preferences** | **系统偏好** |
| ![System preferences — English desktop preview](docs/screenshots/en_US/16-system.png) | ![系统偏好 — 中文桌面预览](docs/screenshots/zh_CN/16-system.png) |
| **Configuration backup and restore** | **配置备份与恢复** |
| ![Configuration backup and restore — English desktop preview](docs/screenshots/en_US/17-backup.png) | ![配置备份与恢复 — 中文桌面预览](docs/screenshots/zh_CN/17-backup.png) |
| **Application update and rollback** | **应用更新与回退** |
| ![Application update and rollback — English desktop preview](docs/screenshots/en_US/18-updates.png) | ![应用更新与回退 — 中文桌面预览](docs/screenshots/zh_CN/18-updates.png) |
| **Developer and diagnostics** | **开发与诊断** |
| ![Developer and diagnostics — English desktop preview](docs/screenshots/en_US/19-developer.png) | ![开发与诊断 — 中文桌面预览](docs/screenshots/zh_CN/19-developer.png) |
| **Controlled tethering** | **受控联机** |
| ![Controlled tethering — English desktop preview](docs/screenshots/en_US/20-link.png) | ![受控联机 — 中文桌面预览](docs/screenshots/zh_CN/20-link.png) |
| **Terminal entry** | **终端入口** |
| ![Terminal entry — English desktop preview](docs/screenshots/en_US/21-terminal.png) | ![终端入口 — 中文桌面预览](docs/screenshots/zh_CN/21-terminal.png) |
| **Touch keyboard** | **触摸键盘** |
| ![Touch keyboard — English desktop preview](docs/screenshots/en_US/22-touch-keyboard.png) | ![触摸键盘 — 中文桌面预览](docs/screenshots/zh_CN/22-touch-keyboard.png) |
| **Capabilities and performance** | **能力与性能** |
| ![Capabilities and performance — English desktop preview](docs/screenshots/en_US/23-capabilities.png) | ![能力与性能 — 中文桌面预览](docs/screenshots/zh_CN/23-capabilities.png) |
| **Runtime diagnostics** | **运行诊断** |
| ![Runtime diagnostics — English desktop preview](docs/screenshots/en_US/24-diagnostics.png) | ![运行诊断 — 中文桌面预览](docs/screenshots/zh_CN/24-diagnostics.png) |
| **Capture plans** | **拍摄计划** |
| ![Capture plans — English desktop preview](docs/screenshots/en_US/25-recipes.png) | ![拍摄计划 — 中文桌面预览](docs/screenshots/zh_CN/25-recipes.png) |
| **Exposure-bracket plan** | **曝光包围计划** |
| ![Exposure-bracket plan — English desktop preview](docs/screenshots/en_US/26-bracketing.png) | ![曝光包围计划 — 中文桌面预览](docs/screenshots/zh_CN/26-bracketing.png) |
| **Interval and calibration sequences** | **间隔与校准序列** |
| ![Interval and calibration sequences — English desktop preview](docs/screenshots/en_US/27-interval.png) | ![间隔与校准序列 — 中文桌面预览](docs/screenshots/zh_CN/27-interval.png) |
| **Photo rendering looks** | **照片显示风格** |
| ![Photo rendering looks — English desktop preview](docs/screenshots/en_US/28-photo-looks.png) | ![照片显示风格 — 中文桌面预览](docs/screenshots/zh_CN/28-photo-looks.png) |
| **Background task center** | **后台任务中心** |
| ![Background task center — English desktop preview](docs/screenshots/en_US/29-tasks.png) | ![后台任务中心 — 中文桌面预览](docs/screenshots/zh_CN/29-tasks.png) |
| **Image processing workspace** | **图像计算工作台** |
| ![Image processing workspace — English desktop preview](docs/screenshots/en_US/30-image-tools.png) | ![图像计算工作台 — 中文桌面预览](docs/screenshots/zh_CN/30-image-tools.png) |
| **Image processing operations** | **图像处理功能** |
| ![Image processing operations — English desktop preview](docs/screenshots/en_US/31-processing-operations.png) | ![图像处理功能 — 中文桌面预览](docs/screenshots/zh_CN/31-processing-operations.png) |
| **Photo library** | **照片库** |
| ![Photo library — English desktop preview](docs/screenshots/en_US/32-photo-library.png) | ![照片库 — 中文桌面预览](docs/screenshots/zh_CN/32-photo-library.png) |
| **Multi-selection and batch management** | **多选与批量管理** |
| ![Multi-selection and batch management — English desktop preview](docs/screenshots/en_US/33-multi-select.png) | ![多选与批量管理 — 中文桌面预览](docs/screenshots/zh_CN/33-multi-select.png) |
| **Photo playback** | **照片回放** |
| ![Photo playback — English desktop preview](docs/screenshots/en_US/34-viewer.png) | ![照片回放 — 中文桌面预览](docs/screenshots/zh_CN/34-viewer.png) |
| **Side-by-side and pixel inspection** | **并排与像素查看** |
| ![Side-by-side and pixel inspection — English desktop preview](docs/screenshots/en_US/35-compare-zoom.png) | ![并排与像素查看 — 中文桌面预览](docs/screenshots/zh_CN/35-compare-zoom.png) |
| **Non-destructive tone editing** | **非破坏明暗编辑** |
| ![Non-destructive tone editing — English desktop preview](docs/screenshots/en_US/36-tone-crop.png) | ![非破坏明暗编辑 — 中文桌面预览](docs/screenshots/zh_CN/36-tone-crop.png) |
| **Rotation and crop** | **旋转与裁切** |
| ![Rotation and crop — English desktop preview](docs/screenshots/en_US/36b-rotation-crop.png) | ![旋转与裁切 — 中文桌面预览](docs/screenshots/zh_CN/36b-rotation-crop.png) |
| **White balance and RGB** | **白平衡与 RGB** |
| ![White balance and RGB — English desktop preview](docs/screenshots/en_US/37-white-balance.png) | ![白平衡与 RGB — 中文桌面预览](docs/screenshots/zh_CN/37-white-balance.png) |
| **Tone curve** | **色调曲线** |
| ![Tone curve — English desktop preview](docs/screenshots/en_US/38-tone-curve.png) | ![色调曲线 — 中文桌面预览](docs/screenshots/zh_CN/38-tone-curve.png) |
| **Custom styles** | **自定义风格** |
| ![Custom styles — English desktop preview](docs/screenshots/en_US/39-custom-styles.png) | ![自定义风格 — 中文桌面预览](docs/screenshots/zh_CN/39-custom-styles.png) |
| **Edit history** | **编辑历史** |
| ![Edit history — English desktop preview](docs/screenshots/en_US/40-edit-history.png) | ![编辑历史 — 中文桌面预览](docs/screenshots/zh_CN/40-edit-history.png) |
| **Batch export** | **批量导出** |
| ![Batch export — English desktop preview](docs/screenshots/en_US/41-export.png) | ![批量导出 — 中文桌面预览](docs/screenshots/zh_CN/41-export.png) |
| **File browser and copy import** | **文件浏览与复制导入** |
| ![File browser and copy import — English desktop preview](docs/screenshots/en_US/42-import.png) | ![文件浏览与复制导入 — 中文桌面预览](docs/screenshots/zh_CN/42-import.png) |
| **Photo library actions** | **照片库工具** |
| ![Photo library actions — English desktop preview](docs/screenshots/en_US/43-library-actions.png) | ![照片库工具 — 中文桌面预览](docs/screenshots/zh_CN/43-library-actions.png) |
| **Photo ratings** | **照片评分** |
| ![Photo ratings — English desktop preview](docs/screenshots/en_US/44-rating.png) | ![照片评分 — 中文桌面预览](docs/screenshots/zh_CN/44-rating.png) |
| **Photo metadata** | **照片元数据** |
| ![Photo metadata — English desktop preview](docs/screenshots/en_US/45-photo-info.png) | ![照片元数据 — 中文桌面预览](docs/screenshots/zh_CN/45-photo-info.png) |
| **Permanent-delete confirmation** | **永久清空确认** |
| ![Permanent-delete confirmation — English desktop preview](docs/screenshots/en_US/46-trash-confirmation.png) | ![永久清空确认 — 中文桌面预览](docs/screenshots/zh_CN/46-trash-confirmation.png) |
<!-- UI_GALLERY_END -->

## 运行源码 / Run from source

需要 Python 3.12、CMake、C++17 编译器，以及 Qt/PyQt 运行时。原生服务和界面各自有明确的构建入口：

```sh
python -m pip install -r requirements-ui.txt
python tools/build_native.py --build-dir build/native
```

Windows 默认 CMake Release 输出的启动示例：

```powershell
python ui/on-device/main.py --native-command build/native/Release/hf-camerad.exe --data-dir var/hf-device
```

可使用 `--language en_US` 或 `--language zh_CN` 选择界面语言，也可在系统偏好中切换。语言设置不会改写文件名、命令、路由或用户输入。

样片取景演示可加 `--demo-image ui/on-device/demo-images/coast.jpg`。水平仪演示可加 `--demo-attitude 0 0`，会明确标记 DEMO；实际姿态输入使用 `--attitude-file PATH`，两种姿态输入方式互斥。照片库可复制导入 [三张示例照片](ui/on-device/demo-images/README.md)，用于回放、比较、评分和非破坏编辑；示例预览会明确标示其来源。

Unix 构建通常输出 `build/native/hf-camerad`；将 `--native-command` 指向该文件。每个并行实例使用独立数据目录。详见 [界面说明](ui/on-device/README.md) 与 [原生相机说明](camera/native/README.md)。

## 固件镜像 / Firmware images

**镜像放在 [GitHub Releases](https://github.com/derrickyau9/Project-Walnut/releases)，不进入 Git 源码历史。** 每个镜像附带 SHA-256、适用硬件、构建来源、包含功能及验证限制。

Full-card images and partition-repair images are different artifacts. Follow the individual release instructions and checksums. Experimental OpenBSD payloads and device-bound recovery backups are not general-purpose firmware releases.

当前整卡镜像面向树莓派 4 ARM64 开发平台及其指定显示配置。FF-1 整机固件、板载 Wi-Fi 和真实传感器能力不因发布镜像而自动成为已完成项目。

Project Walnut 新预览的样片演示、水平仪接口、固定构建输入、授权公钥和实机验证边界见 [镜像构建与使用说明 / Preview guide](docs/releases/walnut-preview-002.md)。

## 目录 / Repository map

| 目录 | 内容 |
| --- | --- |
| `hardware/` | 机身、板卡、传感器接口及导入设计的来源记录 |
| `fpga/` | 采集数据链 RTL、测试台和接口工程 |
| `firmware/` | 辅助控制器固件与边界定义 |
| `camera/` | 原生相机服务、RAW/DNG、校准、媒体与图像处理 |
| `ui/on-device/` | 摄影界面、工作区、工作流与设备工具 |
| `daos/` | 相机协议、能力接口与扩展基础 |
| `system/` | BSD/ARM64、镜像构建、USB 和恢复集成 |
| `tests/`、`tools/` | 自动测试、构建与验证工具 |
| `docs/` | 产品、架构、验证说明与完整截图 |
| `licenses/` | 来源、第三方条款与项目授权状态 |

`build/`、`var/`、虚拟机、凭据、私人备份和用户照片均不属于 Git 源码内容。

## 参与和授权 / Contributing and licensing

欢迎围绕摄影体验、可维修硬件、采集接口、图像管线、系统移植、测试和文档开展协作。请区分提案、模拟器结果、虚拟机结果和实机结果，并保留第三方来源与许可声明。

当前仓库为私有研发仓库。开放硬件、软件、SDK 和文档的授权方案记录在 [许可清单](licenses/README.md)；其中待确认的项目不能当作已经生效的统一授权。第三方组件继续遵循各自条款。项目的开放目标包括明确的制造、修改和再分发权利。

**让相机成为用户可以理解、控制、修改和创造的工具。**

**They made the leaf. We cracked the nut.**
