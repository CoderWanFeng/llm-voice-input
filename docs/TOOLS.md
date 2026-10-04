# 工具选型：零件 → 工具

> 项目：语音输入工具 ｜ 栈：Python + PySide6 ｜ ASR：云端 ｜ 交互：toggle
> 本文件与 `ONTOLOGY.md` 一一对应：那边定义"有什么"，这边定义"用什么造"。

## 0. 环境基线（已实测）

| 项 | 值 | 说明 |
|---|---|---|
| Python | **3.12.3**（系统 `E:/python/python3.12`） | 3.13 亦可用，但 3.12 的 Qt / 音频 wheel 生态最稳 |
| 包管理 | pip 26.2.1 | 全部候选依赖已用 `--dry-run` 验证可安装 |
| 平台 | Windows x64 | 大量零件依赖 Win32 API，不做跨平台抽象 |

---

## 1. 总表：零件 → 工具

| 零件 | 选定工具 | 版本 | 备选 | 一句话理由 |
|---|---|---|---|---|
| Session / AudioClip / Transcript | **标准库** `dataclasses` + `enum` | — | pydantic | 值对象不需要框架，越少依赖越可控 |
| AppState 状态机 | **标准库** `enum` + 转移表 + `threading.RLock` | — | QtStateMachine | 9 个状态用不上状态机框架 |
| Hotkey 快捷键 | **pywin32** `RegisterHotKey` + Qt 原生事件 | 312 | pynput 1.8.2 | OS 级注册，能拿到"被谁占用"，满足 R11 |
| FocusTarget 焦点 | **pywin32** `win32gui` / `win32process` | 312 | ctypes 手写 | 与剪贴板同库，API 齐全 |
| Recorder 录音器 | **sounddevice** | 0.5.6 | PyAudio 0.2.14 | 活跃维护、wheel 自带 PortAudio、支持回调与常驻流 |
| 音频缓冲 / 数值处理 | **numpy** | 1.26.4 | array 模块 | 环形缓冲与 RMS 计算效率 |
| 重采样（设备不支持 16k 时） | **soxr** | 1.1.0 | scipy 1.13 | 专为音频、体积小；scipy 为此引入太重 |
| AudioClip 封装 | **标准库** `wave` | — | pydub + ffmpeg | 16k 单声道 wav，1 分钟约 1.9MB，直接传即可 |
| AsrClient 识别客户端 | **requests** | 2.31.0 | 腾讯云官方 SDK | 统一接口下隐藏厂商差异，避免重型 SDK 绑架 |
| PostProcessor 文本加工 | **标准库** `re` + 术语表 | — | LLM API | MVP 不做润色，规则替换已够 |
| Injector 注入器 | **pywin32** `win32clipboard` + **ctypes** `SendInput` | 312 | pyautogui | 控制粒度到按键级，且能精确延时 |
| 剪贴板快照 | **pywin32** `win32clipboard` 全格式枚举 | 312 | pyperclip | 只有它能保住用户复制的图片/富文本 |
| UIA 兜底写入 | **uiautomation** | 2.0.29 | pywinauto | 纯 Python 封装 UIA，轻且活跃 |
| Overlay 悬浮窗 | **PySide6** | 6.11.2 | PyQt6 | 授权友好，QPainter 足够画波形 |
| TrayIcon 托盘 | **PySide6** `QSystemTrayIcon` | 6.11.2 | pystray 0.19.5 | 同一套 Qt 事件循环，不引入第二套 |
| Config 配置 | **pydantic** v2 + json 落盘 | 2.13.5 | 裸 dict | 用户会手改 json，需要校验与友好报错 |
| Credential 凭据 | **keyring** + 环境变量兜底 | 25.7.0 | 明文配置文件 | 满足 R15，走 Windows 凭据管理器 |
| Logger 日志 | **标准库** `logging` + `Filter` 脱敏 | — | loguru | 标准库够，脱敏用自定义 Filter |
| 打包 | **PyInstaller** | 待定 | Nuitka | 生态最全，Qt 插件坑有解 |
| 测试 | **pytest** | 待定 | unittest | 待开工后按需引入 |

---

## 2. 关键决策的理由与被否理由

### 2.1 热键：为什么主推 `RegisterHotKey` 而不是 pynput

| 方案 | 机制 | 优点 | 致命缺点 |
|---|---|---|---|
| **pywin32 RegisterHotKey** ✅ | OS 级注册 | 无需管理员；注册冲突**返回 FALSE** 可提示（满足 R11）；不监听全部按键 | 需要一个消息循环（Qt 自带，用 `QAbstractNativeEventFilter` 收 `WM_HOTKEY`） |
| pynput GlobalHotKeys | `WH_KEYBOARD_LL` 低级钩子 | 10 行就能跑通 | 冲突时**静默失效**，违反 R11；受 UAC 完整性级别影响（管理员/被管理员遮挡的窗口收不到） |
| keyboard 0.13.5 | 全局 hook | 语法最简 | 需管理员权限；全局监听易被安全软件误报；卸载残留风险 |

**分阶段执行**：MVP 阶段允许先用 `pynput` 五分钟跑通，进入稳定版必须换成 `RegisterHotKey`——因为 R11（失败必须可见）是硬规矩，pynput 做不到。

### 2.2 录音：sounddevice vs PyAudio

- **选 sounddevice**：PortAudio 绑定、pip wheel 自带二进制无需编译、`InputStream` 回调模型天然支持"常驻流 + 环形缓冲"（解决丢音头的关键）、可查询设备支持的采样率。
- **否 PyAudio**：0.2.14 已多年未实质更新，wheel 与 Python 版本强绑定，API 啰嗦。
- 配套坑：若设备不支持 16000Hz，用 `soxr` 重采样；**不要**用 scipy（为此引入几十 MB 不划算）。

### 2.3 注入：为什么是"剪贴板 + SendInput"，而不是 pyautogui

- **pyautogui 0.9.54**：依赖 Pillow + PyScreeze，启动慢；`hotkey()` 走的是 keyboard 库；最关键的是**按键时序不可控**（R7 要求粘贴后精确延时再还原剪贴板）。
- **ctypes `SendInput`**：直接发 `VK_CONTROL` down → `V` down → `V` up → `CONTROL` up，粒度到每个事件，延时可控。
- **pyperclip 被否**：只能存纯文本。用户剪了一张图，你注入一次文本就把图毁了——直接违反 R7。必须用 `win32clipboard` 枚举并备份所有格式。
- **已知折中**：延迟渲染格式（如 `CF_BITMAP` 句柄）在 `CloseClipboard` 后失效，完整还原成本高。**务实策略**：文本类（`CF_UNICODETEXT`/`CF_TEXT`/`CF_HTML`）与文件列表必须还原，位图类尽力而为，并在日志中标注。

### 2.4 悬浮窗：PySide6，四个标志位缺一不可

```python
Qt.FramelessWindowHint          # 无边框
Qt.WindowStaysOnTopHint         # 置顶
Qt.Tool                         # 不进 Alt+Tab、不进任务栏
Qt.WindowTransparentForInput    # 鼠标穿透
Qt.WindowDoesNotAcceptFocus     # 不抢焦点（R10 的命门）
# 另加属性
Qt.WA_ShowWithoutActivating     # show() 时不激活
Qt.WA_TranslucentBackground     # 圆角/半透明
```

漏掉 `WindowDoesNotAcceptFocus` 或 `WA_ShowWithoutActivating`，浮窗一弹出就把焦点从输入框抢走，需求 3 直接失效。
**否 PyQt6**：GPL 商业授权风险，PySide6 是 LGPL，且已装 6.11.2。

### 2.5 配置与凭据：分开放

- **Config（非敏感）** → pydantic v2 模型 + json 落盘。用户会手改配置文件，pydantic 能给出"哪个字段错了"的友好报错。
- **Credential（敏感）** → keyring 存 Windows 凭据管理器，读不到时回退环境变量。**绝不**写进 json、绝不硬编码、绝不进日志（Logger 加脱敏 Filter）。

### 2.6 不引入的工具

| 工具 | 不用的原因 |
|---|---|
| PyAudio | 维护停滞，sounddevice 全面更优 |
| pyautogui | 体积大、时序不可控 |
| keyboard | 需管理员、易被误报、无冲突检测 |
| pyperclip | 只支持文本，破坏剪贴板（违反 R7） |
| scipy / librosa | 仅为重采样引入太重，soxr 足够 |
| loguru / pystray / PyQt6 | 标准库或 Qt 已覆盖 / 授权风险 |
| ffmpeg / pydub | 增加部署复杂度，wav 直接传云端即可 |

---

## 3. 依赖清单（requirements.txt，版本已实测可安装）

```
PySide6==6.11.2
sounddevice==0.5.6
numpy==1.26.4
pywin32==312
requests==2.31.0
pydantic==2.13.5
keyring==25.7.0
soxr==1.1.0
uiautomation==2.0.29
```

可选（MVP 过渡用，稳定版移除）：`pynput==1.8.2`
按需（选定厂商后再加）：`tencentcloud-sdk-python-asr==3.1.177`

---

## 4. 待定项

| 项 | 状态 | 阻塞的动作 |
|---|---|---|
| ASR 服务商与密钥 | **待用户提供** | A6 识别（当前用 `MockAsrClient` 跑通其余链路） |
| 打包方式（onedir / onefile） | 待定 | 第 5 步 |
| 术语表来源 | 待定 | A7 加工（MVP 可留空） |

### 腾讯云 ASR 补充选型说明

- **SDK**：`tencentcloud-sdk-python-asr==3.1.177`（已实测可装）。用它而不是手写 TC3 签名——
  签名算法细节多、无法本地验证，官方 SDK 久经考验。
- **接口**：一句话识别 `SentenceRecognition`（`asr.tencentcloudapi.com`，版本 2019-06-14）。
- **硬限制**：音频 **≤60 秒、≤3MB**。因此 `MAX_RECORD_MS` 定为 55 秒（留 5 秒余量），
  60 秒的 16k 单声道 wav 约 1.9MB，base64 后约 2.5MB，仍在 3MB 内。
- **免费额度**：一句话识别每月 5000 次（以资源包形式发放，需在控制台开通服务）。
- **计费风险**：后付费默认对新用户关闭；资源包耗尽会自动停服而不是产生账单。
- **两条并行路线**：2026-09-24 起新用户被引导到 v3 新版（`asr.cloud-rtc.com`，
  需 TRTC 的 SDKAppID + UserSig）。本项目走的是传统云 API 3.0 路线（SecretId/SecretKey），
  目前仍然可用且更成熟；若将来被强制迁移，只需替换 `TencentAsrClient` 一个文件。
