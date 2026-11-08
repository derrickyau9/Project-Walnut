# DaOS 触摸键盘与离线中文输入

键盘为 800×480 设计，高度保持 252 px。黑白灰不透明界面包含始终可见的
编辑预览、光标键、收起/完成、拼音候选带、三行字符键及模式/空格/回车。
按键按下有 65 ms 缩放与 75 ms 灰度反馈；字母键高 36 px，底部主操作高 44 px。

## 中文

- 普通文本框默认中文全拼；`中/EN` 可随时切换。`ABC` 返回字母布局，
  `123` 和 `#+=` 分别输入数字、符号。切换布局先确认当前组合。
- 连续输入 `nihao` 得到“你好”；支持词组和多词组合，例如
  `jintiantianqihenhao` →“今天天气很好”。`v` 代替 `ü`，如 `lvse` →“绿色”。
- 空格或轻点候选选词。物理键盘 `1–9` 选择当前页对应候选；左右页键翻页，
  候选带可横向滑动。每页 9 项，最多展示 90 项。
- `分词 '` 插入音节分隔符，例如 `xi'an` 优先得到“西安”，不当作单音节 `xian`。
- 退格、Delete、左右/Home/End 在当前拼音组合内编辑；取消/Esc 丢弃未确认组合。
  选择首个词组时会保留剩余拼音，可继续选词。
- 第一次 Enter 只确认组合；再次 Enter 才调用原文本框 Return/accepted 行为，
  防止选词时意外连接 Wi-Fi 或提交表单。收起/完成先确认组合。
- 点按顶部编辑预览可定位原编辑器光标；已输入文本的光标位置使用 Qt 的 UTF-16
  坐标，因此 emoji、中文混排仍可正常插入和删除。

## 英文、密码、终端

- 英文 Shift 依次切换小写、单个大写和 Caps Lock。退格按住连续删除。
  全选、光标键、数字和符号均经原编辑器事件处理，保留选择、验证和撤销机制。
- 密码、敏感、纯数字/纯拉丁/大写限定字段自动使用英文，禁用拼音候选。
  密码只以圆点进入 QML 预览；不学习、不记录、不发送任何输入。
  对敏感字段禁用中文是本版本的明确行为，不支持中文密码的软键盘组合输入。
- `terminalInput` 终端保持英文 raw 输入模式，保留 Ctrl+C、方向键、退格、回车的
  原始终端字节；键盘不镜像终端输出，也不缓存 shell 密码。
- 更换输入框会清除未确认拼音，避免把一个字段的内容带入另一个字段。

## 避让与兼容

有可滚动祖先的编辑器会自动滚动到键盘上方；必要时临时增加 Flickable 的底部
滚动余量，收起时恢复。未放在滚动容器中的弹窗仍可使用键盘顶部编辑预览和确认。
现有 Wi-Fi/终端页面继续使用原有 252 px 底部避让空间。

`main.py` 提供 `SoftKeyboardController` 实例作为 `softKeyboard` QML context object。
键盘通过 `QInputMethodEvent` 维护预编辑和提交，不使用全局键盘注入或网络服务。
旧 review 工具未注入此对象时，组件保持隐藏，所有子绑定使用惰性默认数据。

运行依赖：既有 PyQt5/Qt Quick 5.15、Python 3.11 标准库 `sqlite3`。FreeBSD 镜像需
提供中文字体。本版本的 `fonts/` 包含可追溯的静态 Noto Sans CJK SC 字体和 OFL；
在 QML 加载前用 QFontDatabase 注册即可，不需要系统全局安装。
未安装词库时退回英文，并显示词库不可用提示。

## 词库来源与构建

`ime_data/pinyin_simp.sqlite3` 包含 **65,132 条词/读音记录**。基础词库来自
[Rime rime-pinyin-simp](https://github.com/rime/rime-pinyin-simp)，固定版本
`0c6861ef7420ee780270ca6d993d18d4101049d0`，由 Android Pinyin IME 衍生，
按 **Apache License 2.0** 分发；29 条摄影用语补充位于 `camera_vocabulary.tsv`。

目录保留原始 `pinyin_simp.dict.yaml`、`LICENSE.Apache-2.0`、`AUTHORS`、
`NOTICE.txt`、`SOURCE.json`，其中 manifest 记录原始词库和索引的 SHA-256。
打包时需完整带上 `pinyin_ime.py`、`ime_data/`，包括许可证和无常规后缀文件。

离线重建：

```text
python3.11 ui/on-device/ime_data/build_dictionary.py
```

只有显式传 `--fetch` 才会从官方 GitHub 下载上述固定 commit。
运行时数据库只读，无联网、用户词库或输入历史文件。

这是确定性的词频/词组和多词分段输入法，覆盖日常与摄影文本；不提供云端联想、
个人学习、模糊音、双拼、手写或语音。组合最多 64 个字母/分隔符，复杂长句和
专有名词可能需要逐词选择；候选排序不宣称达到商业输入法的语言模型准确率。

## 验证

`tests/test_soft_keyboard_pinyin.py` 覆盖开放词库及句子分段、真实 Qt TextField 预编辑、
空格/数字/分页选词、组合退格、Unicode 光标、选择替换、密码隔离、真实
TerminalSurface 原始字节、触摸按键保持焦点、滚动避让及无 context object 的旧入口。
用现有 Qt5.15.2 Windows 软件渲染环境验证；未以桌面结果声称树莓派端帧率或时延。

800×480 实际渲染图位于 `build/DaOS_Camera_Experience_001/keyboard-qa/`。
