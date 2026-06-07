# VoiceInput 项目进度报告

## 一、项目概述

VoiceInput 是一个 macOS 菜单栏语音输入工具。当前版本已经完成 MVP 闭环：用户通过全局快捷键开始/结束录音，应用采集麦克风音频并发送到火山引擎豆包 ASR 进行识别，最终将识别结果写入剪贴板并尝试自动粘贴到当前焦点窗口。

**当前状态**：项目已可用，核心语音输入链路已跑通。

---

## 二、当前技术方案

### 2.1 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    VoiceInput 当前架构                       │
├─────────────────────────────────────────────────────────────┤
│  [用户层]                                                    │
│    Option+K 快捷键 → 菜单栏控制 → 底部悬浮面板反馈              │
├─────────────────────────────────────────────────────────────┤
│  [控制层]                                                    │
│    HotkeyManager → StateMachine → AppDelegate                │
│         ↓              ↓              ↓                      │
│     全局监听       状态管理       录音/识别/注入调度             │
├─────────────────────────────────────────────────────────────┤
│  [核心层]                                                    │
│    AudioRecorder → VolcASRService → TextInjector             │
│         ↓              ↓              ↓                      │
│     音频采集       豆包 ASR       剪贴板写入 + 自动粘贴           │
├─────────────────────────────────────────────────────────────┤
│  [配置与反馈层]                                               │
│    AppConfig │ APIKeyDialog │ StatusBarController │ FloatingPanel │ DiagLog
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件说明

| 组件 | 职责 | 当前状态 |
|------|------|----------|
| `HotkeyManager` | 基于 CGEventTap 监听全局快捷键 | ✅ 可用，默认 `Option + K` |
| `StateMachine` | 管理 `idle / recording / transcribing` 状态 | ✅ 可用 |
| `AudioRecorder` | 使用 AVAudioEngine 采集麦克风音频并转换为 16kHz mono | ✅ 可用 |
| `VolcASRService` | 对接火山引擎豆包流式 ASR WebSocket 协议 | ✅ 可用 |
| `TextInjector` | 写入剪贴板并模拟 `Cmd + V` 粘贴 | ✅ 可用，自动粘贴依赖辅助功能权限 |
| `FloatingPanelController` | 底部悬浮面板、录音波形、识别状态与文本反馈 | ✅ 可用 |
| `StatusBarController` | 菜单栏入口、配置、测试、退出 | ✅ 可用 |
| `APIKeyDialog` | 图形化配置豆包 APP ID / Access Token | ✅ 可用 |
| `AppConfig` | 读写本地配置文件 | ✅ 可用 |
| `DiagLog` | 写入诊断日志，辅助排查录音/ASR/注入问题 | ✅ 可用 |

### 2.3 当前语音输入流程

1. 用户按 `Option + K` 或点击菜单测试项触发录音。
2. 状态机从 `idle` 切换到 `recording`。
3. 应用检查豆包 ASR 配置是否完整。
4. 创建 `VolcASRService` 并发送首个配置帧。
5. 启动 `AudioRecorder`，实时采集麦克风音频。
6. 每 200ms 拉取新增音频样本，转换为 16bit PCM LE 后发送到豆包 ASR。
7. ASR 返回 partial 文本时，悬浮面板实时展示识别内容。
8. 用户再次按 `Option + K` 后停止录音，发送 finish 包。
9. 收到 definite 最终结果后，将文本写入剪贴板并尝试自动粘贴。
10. 悬浮面板显示识别结果并自动隐藏，状态回到 `idle`。

---

## 三、已解决问题

| 序号 | 问题描述 | 根因分析 | 解决方案 | 状态 |
|------|----------|----------|----------|------|
| 1 | 点击 5 秒测试后应用异常退出 | AVAudioConverter 状态复用导致音频转换异常 | 每次 buffer 回调创建新的 converter | ✅ 已修复 |
| 2 | 录音后没有识别结果 | 音频 tap / converter 生命周期问题 | 重构音频转换与样本缓存逻辑 | ✅ 已修复 |
| 3 | finish 后收不到最终结果 | 发送 finish 后过早关闭连接 | 分离 `sendFinished` 与 `closed` 状态，继续接收 definite 结果 | ✅ 已修复 |
| 4 | 快捷键无响应 | CGEventTap 权限、修饰键组合和 keycode 问题 | 当前固定为 `Option + K`，并增加权限提示与菜单测试入口 | ✅ 已修复 |
| 5 | 文本无法自动粘贴 | macOS 缺少辅助功能权限 | 已增加权限检查、系统设置跳转和剪贴板兜底 | ✅ 已处理 |
| 6 | 状态机未正确触发录音 | 状态转换逻辑重复/不清晰 | 简化为 `idle → recording → transcribing → idle` | ✅ 已修复 |
| 7 | 豆包配置不方便 | 早期需要手动写配置文件 | 新增菜单栏“设置豆包语音…”图形化配置入口 | ✅ 已完成 |
| 8 | 录音状态缺少反馈 | 仅菜单栏状态不明显 | 新增底部悬浮面板、波形、partial 文本和最终结果展示 | ✅ 已完成 |

---

## 四、当前进度

### 4.1 已完成功能

| 功能 | 状态 | 说明 |
|------|------|------|
| macOS 菜单栏应用 | ✅ | `LSUIElement` 模式运行，不显示 Dock 图标 |
| .app 打包脚本 | ✅ | `build-app.sh` 可构建并打包 `build/VoiceInput.app` |
| 全局快捷键 | ✅ | 当前为 `Option + K` 开始/结束录音 |
| 快捷键开关 | ✅ | 菜单栏可启用/禁用快捷键 |
| 菜单手动测试 | ✅ | 可绕过 CGEventTap 直接触发流程 |
| 5 秒自动录音测试 | ✅ | 一次点击完成开始录音、等待、结束识别 |
| 麦克风采集 | ✅ | AVAudioEngine 采集输入音频 |
| 音频格式转换 | ✅ | 转为 16kHz mono Float32，并发送 16bit PCM LE |
| 豆包 ASR WebSocket 接入 | ✅ | 已接入 `bigmodel_async` 流式 ASR |
| partial 结果展示 | ✅ | 悬浮面板实时展示识别中间文本 |
| definite 最终结果处理 | ✅ | 收到最终结果后自动注入 |
| 剪贴板写入 | ✅ | 自动写入识别文本 |
| 自动粘贴 | ✅ | 有辅助功能权限时模拟 `Cmd + V` |
| 剪贴板恢复 | ✅ | 自动粘贴后尝试恢复原剪贴板内容 |
| 悬浮面板反馈 | ✅ | 录音中、识别中、已识别、错误状态均有反馈 |
| 豆包配置弹窗 | ✅ | 菜单栏可设置 APP ID / Access Token |
| 本地配置持久化 | ✅ | 写入 Application Support 目录 |
| 诊断日志 | ✅ | 录音、ASR、注入关键路径均有日志 |

### 4.2 当前仍需优化

| 功能 | 状态 | 说明 |
|------|------|------|
| 自定义快捷键 | ⬜ | 当前快捷键固定为 `Option + K` |
| 更完善的权限引导 | ⬜ | 已有弹窗和跳转，后续可增加启动时统一检查 |
| ASR 错误重试 | ⬜ | 当前失败后提示错误，未做自动重连/重试 |
| 文本清洗接入 | ⬜ | `TextCleaner` 已存在，但当前最终注入仍直接使用 ASR 文本 |
| README 同步 | ⬜ | README 仍包含旧的 Speech.framework 方案描述，需要后续同步 |
| 配置安全优化 | ⬜ | 当前本地保存 token，后续可迁移到 Keychain |
| 发布与签名 | ⬜ | 当前为本地 ad-hoc 签名，未做正式分发签名/公证 |

### 4.3 当前快捷键与菜单

- **开始/结束录音**：`Option + K`
- **状态栏图标**：`🎙️`
- **菜单项**：
  - 状态显示
  - 豆包 ASR 配置状态
  - 启用/禁用快捷键
  - 设置豆包语音…
  - 测试快捷键（绕过 CGEventTap）
  - 5 秒自动录音测试
  - 退出

---

## 五、使用说明

### 5.1 构建应用

```bash
./build-app.sh
```

构建完成后会生成：

```bash
build/VoiceInput.app
```

### 5.2 启动应用

```bash
open build/VoiceInput.app
```

### 5.3 配置豆包 ASR

方式一：通过菜单栏配置

1. 点击菜单栏 `🎙️` 图标。
2. 选择“设置豆包语音…”。
3. 填入火山引擎豆包语音的 `APP ID` 与 `Access Token`。
4. 保存后模型状态显示为“豆包 ASR 就绪”。

方式二：手动写配置文件

配置文件路径：

```bash
~/Library/Application Support/VoiceInput/config.json
```

配置格式：

```json
{
  "app_id": "你的 APP ID",
  "access_token": "你的 Access Token"
}
```

### 5.4 正常录音输入流程

1. 将光标放到任意输入框。
2. 按 `Option + K` 开始录音。
3. 底部悬浮面板显示录音状态和波形。
4. 说完后再次按 `Option + K`。
5. 应用进入识别中状态。
6. 识别完成后自动写入剪贴板，并在具备辅助功能权限时自动粘贴到当前输入框。

### 5.5 权限配置

| 权限 | 用途 | 说明 |
|------|------|------|
| 麦克风 | 录音采集 | 首次启动会请求 |
| 输入监控 | 全局快捷键监听 | 需要在系统设置中允许 VoiceInput |
| 辅助功能 | 自动模拟 `Cmd + V` | 未授权时识别结果仍会保留在剪贴板，可手动粘贴 |

系统路径：

- 系统设置 → 隐私与安全性 → 输入监控
- 系统设置 → 隐私与安全性 → 辅助功能

---

## 六、当前文件结构

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
│   ├── HotkeyManager.swift
│   ├── StateMachine.swift
│   ├── StatusBarController.swift
│   ├── FloatingPanelController.swift
│   ├── APIKeyDialog.swift
│   ├── Config.swift
│   ├── TextCleaner.swift
│   └── DiagLog.swift
└── doc/
    └── PROGRESS_REPORT.md
```

---

## 七、下一步计划

1. 同步更新 README，移除旧的 Speech.framework 方案说明。
2. 将 `TextCleaner.basicCleanup` 接入最终注入链路。
3. 增加自定义快捷键配置能力。
4. 增加启动时权限检查面板，降低首次使用成本。
5. 增加 ASR WebSocket 失败重试和超时兜底。
6. 将 Access Token 存储迁移到 macOS Keychain。
7. 优化悬浮面板 UI 和动画细节。
8. 评估正式签名、公证与发布流程。

---

## 八、结论

VoiceInput 当前已完成从快捷键触发、录音采集、豆包 ASR 识别、结果展示、剪贴板写入到自动粘贴的完整 MVP 闭环。除权限配置、自定义快捷键、错误重试和文档同步等体验优化外，项目已经具备日常试用能力。

**文档更新时间**：2026-06-07  
**项目状态**：MVP 已可用，进入体验优化与稳定性增强阶段
