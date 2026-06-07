# VoiceInput

按 `Ctrl+Option+K` 开始录音，再按一次停止并把语音转成文字，自动粘贴到当前应用。

## 技术栈

- **Swift 5.9 + AppKit** - 菜单栏 App
- **AVFoundation** - 麦克风采集（16kHz mono Float32）
- **Speech.framework** - Apple 系统自带 ASR（**零外部依赖**）
- **CGEventTap** - 全局快捷键监听
- **NSPasteboard + CGEvent** - 文本注入（模拟 Cmd+V）

## 架构

```
┌────────────────────────────────────────────────┐
│  HotkeyManager (Ctrl+Option+K)                 │
└──────────────┬─────────────────────────────────┘
               │ 按下
               ▼
┌────────────────────────────────────────────────┐
│  StateMachine: idle → recording → transcribing │
└──────────────┬─────────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
┌─────────────┐  ┌────────────────┐
│ AudioRecorder│  │ SpeechService  │
│ 16kHz PCM   │  │ (系统 Speech)  │
└──────┬──────┘  └────────┬───────┘
       │                  │
       │         ┌────────┴─────────┐
       │         ▼                  │
       │   写临时 wav (16bit PCM)   │
       │         │                  │
       │         ▼                  │
       │   SFSpeechURLRecognitionRequest
       │         │                  │
       │         ▼                  │
       │       文本                 │
       ▼                            ▼
       └────────────┬───────────────┘
                    ▼
           ┌────────────────┐
           │ TextCleaner    │
           │ (基础清洗)     │
           └────────┬───────┘
                    ▼
           ┌────────────────┐
           │ TextInjector   │
           │ NSPasteboard   │
           │ + Cmd+V 模拟   │
           └────────────────┘
```

## 文件结构

```
voice-input/
├── Package.swift                       # SwiftPM 清单（零外部依赖）
├── build-app.sh                        # 打包 .app bundle 的脚本
├── Resources/Info.plist                # 权限配置
└── Sources/VoiceInput/
    ├── main.swift                     # 入口
    ├── AppDelegate.swift              # 主控制器
    ├── AudioRecorder.swift            # 录音
    ├── HotkeyManager.swift            # 全局快捷键
    ├── SpeechService.swift            # Apple Speech.framework 封装 + 内置 WAV 写入器
    ├── TextInjector.swift             # 文本注入
    ├── TextCleaner.swift              # 文本清洗
    ├── StateMachine.swift             # 状态管理
    └── StatusBarController.swift      # 菜单栏 UI
```

## 运行

### 前置要求
- macOS 13+
- Apple Silicon / Intel 都可以
- Xcode 命令行工具: `xcode-select --install`
- Swift 5.9+（系统自带即可）
- **网络**：首次使用 Speech 识别需要联网（Apple 服务器识别），macOS 26+ 可在「系统设置 → 键盘 → 听写」下载离线包

### 步骤

1. **编译并打包成 .app**（必须打包，否则系统不会授予麦克风/辅助功能权限）：
   ```bash
   ./build-app.sh
   ```

2. **启动**：
   ```bash
   open build/VoiceInput.app
   ```
   首次启动会依次弹出：
   - **麦克风权限** 弹窗 → 点「好」
   - **语音识别权限** 弹窗 → 点「好」
   - **Input Monitoring 权限**（CGEventTap 需要）→ 自动跳转到「系统设置 → 隐私与安全性 → Input Monitoring」手动把 VoiceInput 加进去

3. **使用**：
   - 菜单栏出现 🎙️ 图标
   - 把光标放在任意输入框（编辑器、浏览器、聊天软件等）
   - 按 `Ctrl+Option+K` 开始录音（菜单栏图标会显示「录音中」）
   - 说话
   - 再按 `Ctrl+Option+K` 结束 → 系统识别 → 自动粘贴到光标位置

## 已知限制（MVP 阶段）

- **依赖系统 Speech 准确率**：没有本地大模型，对术语/口音识别可能比 Whisper 差
- **依赖网络**：默认走 Apple 服务器（macOS 26+ 可配置离线）
- **首次需手动授权**：Input Monitoring 权限必须到系统设置里加
- **整段识别**：说完一整句才出结果，没有流式输出
- **没有 LLM 后处理**：只做基础去口头禅
- **无浮动 UI 反馈**：录音时只能看菜单栏图标

## 下一步优化方向

- [ ] 切到 Whisper.cpp（本地，更准，但需要能下到二进制或自己编译）
- [ ] 加 Silero VAD 实现"说完自动停"
- [ ] 支持流式识别（边说边出字）
- [ ] 加 LLM 润色（接 GPT-4o-mini / Claude Haiku）
- [ ] 屏幕 OCR 上下文（提升专有名词识别率）
- [ ] 浮动面板显示波形 + 实时文本
- [ ] 说话人分离（会议模式）
