# VoiceInput Windows 版

macOS VoiceInput 的 Windows 移植版，行为与原项目一致：按 `Ctrl + Alt + K` 开始录音，再按一次停止；音频发送到 ASR 识别，识别完成后自动写入剪贴板并模拟 `Ctrl+V` 粘贴到当前光标位置。

支持语音唤醒、语音结束词、AI 润色后处理，并兼容多家 ASR 服务商。

## 功能特性

- **全局热键录音** —— `Ctrl + Alt + K` 开始/停止，0.3s 防抖
- **多 ASR 提供商** —— 火山引擎豆包、科大讯飞、腾讯云、阿里云，工厂模式可扩展
- **语音唤醒** —— 基于 Vosk 离线检测，说出唤醒词即开始录音，无需按键
- **语音结束词** —— 录音中说出结束词自动停止，支持自定义
- **AI 润色后处理** —— 调用 OpenAI 兼容接口的大模型（如 qwen3.7-plus）纠错、按要点换行
- **悬浮面板** —— 实时显示录音状态、音量、partial 识别文本与最终结果
- **系统托盘** —— 右键菜单配置凭证、唤醒词、AI 润色等
- **焦点恢复注入** —— 录音期间用户切换窗口后，注入前自动切回目标窗口

## 技术栈

- **Python 3.10+** —— 单一运行时，零编译
- **sounddevice + numpy** —— 麦克风直采 16kHz mono PCM
- **websockets** —— ASR WebSocket 客户端（异步事件循环，独立线程）
- **pynput** —— 全局热键监听 `Ctrl + Alt + K`
- **ctypes + user32** —— 剪贴板写入、`SendInput` 模拟粘贴、焦点恢复
- **pystray + Pillow** —— 系统托盘图标与菜单
- **tkinter** —— 录音悬浮面板（标准库，随官方 Python 安装）
- **vosk** —— 离线语音唤醒词检测
- **requests** —— LLM 润色 API 调用

## 当前流程

```text
唤醒词 / Ctrl + Alt + K
  -> StateMachine: idle / recording / transcribing
  -> 记录目标窗口句柄 target_hwnd
  -> AudioRecorder 采集 16kHz mono PCM
  -> ASRService 发送 WebSocket 音频包
  -> FloatingPanel 显示录音、partial 文本和最终结果
  -> TextCleaner 基础清洗
  -> [可选] LLMPostProcessor 调用大模型纠错+按要点换行
  -> TextInjector 焦点恢复 + 写入剪贴板并模拟 Ctrl+V
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
│   ├── config.py                        # 配置文件读写（含 LLM/唤醒词字段）
│   ├── audio_recorder.py                # 麦克风录音
│   ├── asr_base.py                      # ASR 抽象基类
│   ├── volc_asr_service.py              # 火山引擎豆包 ASR
│   ├── xfly_asr_service.py              # 科大讯飞 ASR
│   ├── tencent_asr_service.py           # 腾讯云 ASR
│   ├── aliyun_asr_service.py            # 阿里云 ASR
│   ├── text_cleaner.py                  # 文本清洗
│   ├── text_injector.py                 # 剪贴板 + 模拟粘贴 + 焦点恢复
│   ├── hotkey_manager.py                # 全局快捷键
│   ├── state_machine.py                 # 状态机
│   ├── status_bar_controller.py         # 系统托盘
│   ├── floating_panel_controller.py     # 悬浮面板
│   ├── api_key_dialog.py                # ASR 凭证配置对话框
│   ├── wake_word_detector.py            # Vosk 离线唤醒词检测
│   ├── wake_word_dialog.py              # 唤醒词/结束词设置对话框
│   ├── llm_post_processor.py            # LLM 文本润色（OpenAI 兼容接口）
│   ├── llm_settings_dialog.py           # AI 润色设置对话框
│   └── diag_log.py                      # 诊断日志
└── resources/
    ├── icon.ico                         # 托盘图标（由 make_icon.py 生成）
    ├── make_icon.py                     # 图标生成脚本
    ├── config-template.json             # 配置示例
    └── models/                          # Vosk 唤醒模型目录
        └── vosk-model-small-cn-0.22/    # 中文离线模型（首次运行自动下载）
```

## 运行

### 前置要求

- Windows 10 / 11
- Python 3.10 及以上（含 tkinter，官方 python.org 安装包默认包含）
- ASR 服务凭证（火山引擎豆包 / 科大讯飞 / 腾讯云 / 阿里云任选其一）
- **[可选]** LLM 服务 API Key（用于 AI 润色，如阿里云百炼 DashScope、DeepSeek、智谱等 OpenAI 兼容服务）
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

### 配置 ASR 语音识别

**方式一：图形界面配置（推荐）**

首次启动若未配置，会自动弹出凭证输入对话框。也可点击托盘菜单"设置语音识别凭证…"。

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
  "provider": "volc",
  "providers": {
    "volc": {
      "app_id": "你的 APP ID",
      "access_token": "你的 Access Token"
    }
  }
}
```

> 注：开发模式下应用读取的就是 `resources/config-template.json` 本身（不再是 `%APPDATA%\VoiceInput\config.json`）。PyInstaller 打包成 exe 后该文件会随 exe 释放到临时解压目录、无法编辑，届时请改用方式一图形界面或方式二环境变量。

### 配置语音唤醒与结束词

托盘菜单 → "语音唤醒设置…" 打开对话框，可配置：

- **语音唤醒** —— 启用后，对着麦克风说出唤醒词即开始录音（无需按热键），基于 Vosk 离线模型检测
- **唤醒词** —— 2~6 个常用汉字，如"小薇小薇"，保存前会做离线词典校验
- **语音结束词** —— 启用后，录音中说出结束词自动停止录音
- **结束词** —— 如"结束录音"

### 配置 AI 润色

托盘菜单 → "AI 润色设置…" 打开对话框，可配置：

- **启用 AI 润色** —— 勾选后，ASR 识别完成自动调用大模型纠错
- **接口地址** —— OpenAI 兼容服务 base URL，如 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- **模型** —— 可下拉选择或手动输入，如 `qwen3.7-plus`、`deepseek-chat`、`glm-4` 等
- **API Key** —— 对应服务的密钥（密码输入框，不可见）

AI 润色会解决三类 ASR 常见问题：
1. **专有名词纠错** —— 发音不标准导致的错误（如"long chat" → "LangChain"）
2. **断词修正** —— 口误/停顿导致的错误断词（如"百度、OCR" → "百度OCR"）
3. **按要点换行** —— 多个要点之间自动换行，单要点内部不换行

> LLM 请求超时 30 秒，失败/超时自动回退原始 ASR 文本，不会丢失识别结果。
> DashScope 服务自动关闭 `enable_thinking`，避免 qwen 思考型模型超时。

### 使用

1. 将光标定位到任意输入框（记事本、编辑器、聊天框、浏览器输入框等）。
2. 开始录音（以下两种方式任选其一）：
   - 按 `Ctrl + Alt + K` 开始录音。
   - 启用了语音唤醒时，对着麦克风说出唤醒词（如"小薇小薇"）。
3. 屏幕底部弹出悬浮面板，显示录音状态与实时识别文本。
4. 停止录音（以下三种方式任选其一）：
   - 再次按 `Ctrl + Alt + K`。
   - 启用了语音结束词时，说出结束词（如"结束录音"）。
   - 唤醒词语音开始后，再次说出唤醒词停止。
5. 识别完成后，若启用了 AI 润色，面板显示"AI 润色中"，大模型纠错后注入；否则直接注入。
6. 自动写入剪贴板并模拟 `Ctrl+V` 粘贴到当前光标位置。若录音期间切换了窗口，注入前会自动切回目标窗口。

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
- ASR 凭证与 LLM API Key 以明文存配置文件，未集成 Windows Credential Manager。
- 高完整性进程（以管理员运行的程序）作为粘贴目标时，普通权限进程的模拟按键可能被 UIPI 阻断，会退化为剪贴板 + 提示手动 `Ctrl+V`。
- 单文件 exe 首次启动较慢（PyInstaller 解压到临时目录）。
- AI 润色依赖外部 LLM 服务，网络不通或 Key 失效时自动回退原始识别文本。
