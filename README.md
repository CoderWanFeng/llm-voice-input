# VoiceInput

macOS 菜单栏语音输入工具。按 `Option + K` 开始录音，再按一次停止；应用会把语音发送到火山引擎豆包 ASR 识别，并把文字写入剪贴板，具备辅助功能权限时会自动粘贴到当前输入位置。

## 技术栈

- **Swift 5.9 + AppKit** - 菜单栏 App
- **AVFoundation** - 麦克风采集与 16kHz mono 音频转换
- **URLSessionWebSocketTask** - 对接火山引擎豆包流式 ASR
- **CGEventTap** - 全局快捷键监听
- **NSPasteboard + CGEvent** - 文本注入与自动粘贴

## 当前流程

```text
Option + K
  -> StateMachine: idle / recording / transcribing
  -> AudioRecorder 采集 16kHz mono PCM
  -> VolcASRService 发送豆包 ASR WebSocket 音频包
  -> FloatingPanel 显示录音、partial 文本和最终结果
  -> TextCleaner 基础清洗
  -> TextInjector 写入剪贴板并尝试 Cmd+V
```

## 文件结构

```text
voice-input/
├── Package.swift
├── build-app.sh
├── Resources/
│   └── Info.plist
├── Sources/VoiceInput/
│   ├── main.swift
│   ├── AppDelegate.swift
│   ├── AudioRecorder.swift
│   ├── VolcASRService.swift
│   ├── TextInjector.swift
│   ├── TextCleaner.swift
│   ├── HotkeyManager.swift
│   ├── StateMachine.swift
│   ├── StatusBarController.swift
│   ├── FloatingPanelController.swift
│   ├── APIKeyDialog.swift
│   ├── Config.swift
│   └── DiagLog.swift
└── doc/
    ├── PROGRESS_REPORT.md
    └── USER_MANUAL.md
```

## 运行

### 前置要求

- macOS 13+
- Xcode Command Line Tools
- 火山引擎豆包语音识别 `APP ID` 和 `Access Token`
- 网络连接

### 构建

```bash
./build-app.sh
```

构建完成后会生成：

```text
build/VoiceInput.app
```

### 启动

```bash
open build/VoiceInput.app
```

首次启动需要授权：

- 麦克风：用于录音
- 输入监控：用于监听 `Option + K`
- 辅助功能：用于自动模拟 `Cmd + V` 粘贴

## 配置豆包 ASR

方式一：点击菜单栏 `🎙️` 图标，选择“设置豆包语音…”，填入 `APP ID` 和 `Access Token`。

方式二：手动写入配置文件：

```text
~/Library/Application Support/VoiceInput/config.json
```

```json
{
  "app_id": "你的 APP ID",
  "access_token": "你的 Access Token"
}
```

## 使用

1. 将光标放到任意输入框。
2. 按 `Option + K` 开始录音。
3. 说话，底部悬浮面板会显示录音状态和识别文本。
4. 再按 `Option + K` 停止录音。
5. 识别完成后自动写入剪贴板；如果已授权辅助功能，会自动粘贴。

菜单栏也提供“测试快捷键”和“5秒自动录音测试”，可用于排查输入监控权限或完整识别链路。

## 分支与提交流程

本项目采用简易 Git Flow：

- **`main`** —— 稳定分支，每个可发布版本对应一个 commit/tag。
- **`develop`** —— 开发分支，日常功能在此合入并集成测试。

### 提交流程

1. 从最新的 `develop` 分支切出自己的功能分支：

   ```bash
   git checkout develop
   git pull
   git checkout -b feat/your-feature-name
   ```

2. 开发完成后，本地提交：

   ```bash
   git add .
   git commit -m "feat: 简要描述你的修改"
   ```

3. 推送到远程自己的分支：

   ```bash
   git push -u origin feat/your-feature-name
   ```

4. 在 GitHub 上发起 Pull Request：

   - 目标分支（base）选择 **`develop`**，**不要选择 `main`**。
   - 对比分支（compare）选择你自己的功能分支。
   - 填写 PR 标题与说明，关联对应 issue（如有）。
   - 等待 CI / Code Review 通过后由维护者合入 `develop`。

5. 后续会由维护者从 `develop` 统一发起 `develop → main` 的合并，作为新版本发布。

### 注意事项

- **不要直接向 `main` 提 PR**，所有改动先合入 `develop`。
- 提交前请确保本地已构建运行过 `./build-app.sh`，避免引入编译错误。
- 涉及豆包凭证、个人信息等敏感内容时，**切勿写入代码或文档**。

## 已知限制

- 豆包 ASR 依赖网络和有效凭证。
- 快捷键当前固定为 `Option + K`，暂不支持自定义。
- Access Token 当前保存在本地配置文件，后续可迁移到 Keychain。
- 只有基础文本清洗，暂未接入 LLM 后处理。
- 本地构建使用 ad-hoc 签名，暂未做正式发布签名和公证。
