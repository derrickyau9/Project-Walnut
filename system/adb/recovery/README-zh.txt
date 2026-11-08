DaOS USB 急救 002

电脑端（原授权电脑，保持 USB-C 连接）
Recover-DaOS-USB.cmd status
Recover-DaOS-USB.cmd shell
Recover-DaOS-USB.cmd rescue
Recover-DaOS-USB.cmd normal
等待重新枚举：Recover-DaOS-USB.cmd --timeout 300 status

电脑端按精确 DaOS USB 序列号复用已认证的本地 ADB server；不停止其他 server、
不替换密钥、不开放 TCP ADB。包内不附带电脑私钥。没有可复用连接时，可指定原授权目录：
Recover-DaOS-USB.cmd --tools "原host-tools目录" status
电脑需有 Python；默认使用 Windows 的 py -3。

如何判断急救可用
进入 RAM 急救时屏幕出现单用户 shell 提示本身不是失败；DAOS_RAM_RESCUE_READY 标记
加电脑端 status 的实际 RSA 认证成功才是有效确认，不需要在 Pi 上输入回车。
rescue 为主根仍可读时的一次性 RAM 启动；独立 FATv3 路径不依赖主根或 /var。
正常启动前 FAT marker 置1；正常关机清零。异常复位后选择独立 FAT RAM 急救。

生产硬件看门狗
已实机验证：BCM 硬件 watchdog 停止喂狗约8秒后复位，再从独立 FAT 进入 RAM。
它不能在 panic/死锁现场原地切换系统；当前内核必须先由硬件复位。
WD 尚未启动前就卡住，或启动分区/固件本身损坏，仍可能需要断电重插或外部修复。
启用工具是 system/adb/recovery/enable_recovery_watchdog.py（c306...版本，21项测试），
只有显式 plan/enable、全部卡/FAT/启动链/健康检查通过后才写入0444策略。不要盲目开启。

USB 分区恢复（必须先确认处于 RAM 急救）
Recover-DaOS-USB.cmd write --role root --image "完整根分区.ufs" --journal "新日志目录"
中断恢复：Recover-DaOS-USB.cmd write --role root --image "相同镜像.ufs" --resume "原日志目录"
每块最多16MiB；写前核对 UUID、双份 GPT、大小和前像，再写入、fsync、回读。
只允许 root/state，拒绝已挂载分区；没有整卡、GPT、照片、校准分区写入入口。
state 写入会恢复该镜像的设置/数据库，002不提供 STATE 或照片镜像。

offline-root-v3 中的 .ufs.gz 是本卡配置重建的 ROOT 候选，不是当前实卡逐字节备份。
解压后必须恰好2684354560字节，并核对 SHA256SUMS。它只属于 ROOT分区，不能写到整卡。
它包含0444看门狗策略，仅限已经验证的本卡 FATv3 + bootchain-v3。详细要求见该目录说明。
此完整镜像已做 native fsck/19文件源对应/完整gzip及散列验证，尚未覆盖实卡做整分区验收。
DaOS-Rescue-USB-health-v3-arm64.img 是独立介质构建产物；未宣称实际从USB存储启动过。

文件级修复
replace_helper_in_ram.py 只在 NativeBackend 证实 RAM 根、所有物理分区卸载，并持有全局锁
后，离线挂载绑定ROOT，核对旧helper/内核/init，备份、原子替换、回读、只读重挂并卸载。
禁止热替换正在运行的原生 USB helper；历史方案已撤回，history 中只有不可执行文本。
不要把 helper 更新加入普通 --live 安装路径。

已完成的实机验收
正常USB、一次性RAMv3、独立FATv3、返回正常、离线helper更新、长stdout、16MiB RAM上传、
硬件看门狗停喂复位、生产策略自动启动及21秒持续喂狗均有回执。
最终根RO；SQLite integrity_check=ok；9条complete与9张RAW的文件名/SHA均未改变。
最终Qt截图带HF-Sim标记，所以仅证明UI正常，不代表真实传感器采集验证。
Wi-Fi仍未修复/未完成实机验收；002不包含正在新写的WiFi005 trial工具。

完整回执见 ACCEPTANCE-CURRENT.json、evidence/physical 和 SOURCE-BINDING-QA.json。
001原包保持为历史，不应以旧README/旧载荷覆盖002。
