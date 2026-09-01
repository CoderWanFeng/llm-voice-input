# VoiceInput Windows 版

macOS VoiceInput 的 Windows 移植版，行为与原项目一致：按 `Ctrl + Alt + K` 开始录音，再按一次停止；音频发送到火山引擎豆包 ASR 识别，识别完成后自动写入剪贴板并模拟 `Ctrl+V` 粘贴到当前光标位置。

## 技术栈

- **Python 3.10+** —— 单一运行时，零编译
- **sounddevice + numpy** —— 麦克风直采 16kHz mono PCM
- **websockets** —— 火山引擎豆包流式 ASR 客户端（异步事件循环，独立线程）
- **pynput** —— 全局热键监听 `Ctrl + Alt + K`
- **ctypes + user32** —— 剪贴板写入与 `SendInput` 模拟粘贴
- **pystray + Pillow** —— 系统托盘图标与菜单
- **tkinter** —— 录音悬浮面板（标准库，随官方 Python 安装）

## 当前流程

```text
Ctrl + Alt + K
  -> StateMachine: idle / recording / transcribing
  -> AudioRecorder 采集 16kHz mono PCM
  -> VolcASRService 发送豆包 ASR WebSocket 音频包
  -> FloatingPanel 显示录音、partial 文本和最终结果
  -> TextCleaner 基础清洗
  -> TextInjector 写入剪贴板并模拟 Ctrl+V
```

## 文件结构

```text
llm-voice-input4windows/
├── README.md
├── requirements.txt
├── build-app.bat                        # PyInstaller 打包脚本
├── src/voice_input/
│   ├── __init__.py
│   ├── __main__.py                      # 入口: python -m voice_input
│   ├── app.py                           # 主应用编排
│   ├── config.py                        # 配置文件读写
│   ├── audio_recorder.py                # 麦克风录音
│   ├── volc_asr_service.py              # 火山 ASR WebSocket
│   ├── text_cleaner.py                  # 文本清洗
│   ├── text_injector.py                 # 剪贴板 + 模拟粘贴
│   ├── hotkey_manager.py                # 全局快捷键
│   ├── state_machine.py                 # 状态机
│   ├── status_bar_controller.py         # 系统托盘
│   ├── floating_panel_controller.py     # 悬浮面板
│   ├── api_key_dialog.py                # 凭证配置对话框
│   └── diag_log.py                      # 诊断日志
└── resources/
    ├── icon.ico                         # 托盘图标（由 make_icon.py 生成）
    ├── make_icon.py                     # 图标生成脚本
    └── config-template.json             # 配置示例
```

## 运行

### 前置要求

- Windows 10 / 11
- Python 3.10 及以上（含 tkinter，官方 python.org 安装包默认包含）
- 火山引擎豆包语音识别 `APP ID` 和 `Access Token`
- 网络连接
- 麦克风设备

### 方式一：源码直接运行（推荐用于开发调试）

```bat
REM 进入项目目录
cd /d e:\ai\voice\llm-voice-input4windows

REM 安装依赖
python -m pip install -r requirements.txt

REM 启动应用（设置 PYTHONPATH 让 -m voice_input 可被找到）
set PYTHONPATH=src
python -m voice_input
```

### 方式二：打包为单文件 exe

```bat
cd /d e:\ai\voice\llm-voice-input4windows
build-app.bat
```

构建完成后产物：

```text
dist\VoiceInput.exe
```

双击 `VoiceInput.exe` 即可启动。

### 配置豆包 ASR

**方式一：图形界面配置（推荐）**

首次启动若未配置，会自动弹出凭证输入对话框。也可点击托盘菜单"设置豆包语音…"。

**方式二：环境变量**

```bat
set VOICEINPUT_APP_ID=你的APPID
set VOICEINPUT_ACCESS_TOKEN=你的AccessToken
python -m voice_input
```

**方式三：手动写入配置文件**

配置文件路径（项目内，开发调试友好）：

```text
e:\ai\voice\llm-voice-input4windows\resources\config-template.json
```

直接编辑该文件填入凭证即可：

```json
{
  "app_id": "你的 APP ID",
  "access_token": "你的 Access Token"
}
```

> 注：开发模式下应用读取的就是 `resources/config-template.json` 本身（不再是 `%APPDATA%\VoiceInput\config.json`）。PyInstaller 打包成 exe 后该文件会随 exe 释放到临时解压目录、无法编辑，届时请改用方式一图形界面或方式二环境变量。

### 使用

1. 将光标定位到任意输入框（记事本、编辑器、聊天框、浏览器输入框等）。
2. 按 `Ctrl + Alt + K` 开始录音。
3. 屏幕底部弹出悬浮面板，显示录音状态与实时识别文本。
4. 再次按 `Ctrl + Alt + K` 停止录音。
5. 识别完成后自动写入剪贴板并模拟 `Ctrl+V` 粘贴到当前光标位置。

托盘菜单也提供"测试快捷键"和"5秒自动录音测试"，用于排查热键监听与完整识别链路。

## 权限说明

Windows 上首次运行会请求以下权限（按需）：

- **麦克风** —— 系统设置 → 隐私和安全性 → 麦克风，允许桌面应用访问麦克风。
- **网络** —— Windows 防火墙首次启动可能弹窗，允许即可。

注：模拟 `Ctrl+V` 粘贴到以管理员权限运行的程序时，可能被 UIPI 阻断；此时退化为"剪贴板已写入，请手动按 Ctrl+V"。VoiceInput 自身无需管理员权限即可运行。

## 日志与诊断

日志路径：

```text
%APPDATA%\VoiceInput\diag.log
```

包含状态转换、音频采集计数、ASR 收发字节数与错误堆栈等。

## 与 macOS 版的差异

| 项 | macOS 版 | Windows 版 |
|---|---|---|
| 热键 | `Option + K` | `Ctrl + Alt + K` |
| 录音 | AVFoundation 重采样到 16kHz | sounddevice 直采 16kHz |
| ASR | URLSessionWebSocketTask | websockets 异步 |
| 粘贴 | CGEvent 模拟 Cmd+V | SendInput 模拟 Ctrl+V |
| 托盘 | NSStatusItem | pystray |
| 悬浮面板 | NSPanel | tkinter Toplevel |
| 配置路径 | `~/Library/Application Support/VoiceInput/config.json` | 开发模式：`resources/config-template.json`；打包后：`%APPDATA%\VoiceInput\config.json` |
| 签名 | ad-hoc 签名 | 无签名（首次运行可能被 SmartScreen 拦截，点"仍要运行"） |

ASR 二进制 WebSocket 协议、状态机流转、文本清洗规则、剪贴板备份与恢复时序，均与原项目完全一致。

## 已知限制

- 热键固定为 `Ctrl + Alt + K`，暂不支持自定义。
- Access Token 以明文存配置文件，未集成 Windows Credential Manager。
- 仅基础文本清洗，暂未接入 LLM 后处理。
- 高完整性进程（以管理员运行的程序）作为粘贴目标时，普通权限进程的模拟按键可能被 UIPI 阻断，会退化为剪贴板 + 提示手动 `Ctrl+V`。
- 单文件 exe 首次启动较慢（PyInstaller 解压到临时目录）。
