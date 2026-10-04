# 本体定义：零件 · 动作 · 规矩

> 项目：语音输入工具（说话变文字）
> 技术栈：Python + PySide6 ｜ ASR：云端 API ｜ 交互：toggle（按一下开始，再按一下停）
> 本文只定义"有什么、做什么、不许做什么"，不涉及具体实现与库选型。

---

## 0. 一句话本体

一次说话上屏 = **一个 Session（会话）**，它持有"焦点目标 + 音频片段 + 转写结果"三样东西，
按顺序驱动 6 个动作，全程受 19 条规矩约束（另有 A14–A16 三条设置/日志辅助动作）。

---

## 1. 零件（实体与值对象）

零件是系统里**被动作操作的东西**。分三层：聚合根、核心零件、外围零件。

### 1.1 聚合根

| 零件 | 是什么 | 关键字段 | 需求 |
|---|---|---|---|
| **Session** 会话 | 一次 toggle 的完整生命周期，是唯一的"事务单位"，可整体回滚 | `session_id`、`state`、`focus_target`、`audio_clip`、`transcript`、`started_at`、`error` | 全部 |

### 1.2 核心零件（参与主链路）

| 零件 | 是什么 | 关键字段 | 需求 |
|---|---|---|---|
| **Hotkey** 快捷键 | 一个全局按键绑定 | `combo`、`mode=toggle`、`registered`、`conflict_with` | 1 |
| **FocusTarget** 焦点目标 | 按下快捷键那一刻的"上屏目的地"快照 | `hwnd`、`thread_id`、`pid`、`process_name`、`integrity_level`、`captured_at` | 3 |
| **Recorder** 录音器 | 常驻的麦克风输入流 | `device`、`rate=16000`、`channels=1`、`width=2`、`ring_buffer`、`is_streaming` | 2 |
| **AudioClip** 音频片段 | 一次录音的产物（值对象，不可变） | `pcm_bytes`、`duration_ms`、`rate`、`channels`、`width`、`has_preroll` | 2 |
| **AsrClient** 识别客户端 | 云端 ASR 的适配器（对上层隐藏厂商差异） | `provider`、`endpoint`、`credential_ref`、`timeout_s`、`retry` | 2 |
| **Transcript** 转写结果 | 识别产物（值对象） | `text`、`confidence`、`duration_ms`、`is_final`、`error` | 2 |
| **PostProcessor** 文本加工 | 标点、口语顺滑、术语替换（可整体关闭） | `enabled`、`glossary`、`polish_level` | 2 |
| **Injector** 注入器 | 把文本送进目标光标的执行者，内含多条策略 | `strategy`、`clipboard_snapshot`、`paste_delay_ms` | 3 |
| **ClipboardSnapshot** 剪贴板快照 | 注入前对剪贴板的完整备份（含非文本格式） | `formats`、`restore_deadline` | 3 |
| **Overlay** 悬浮窗 | 屏幕底部的状态显示器 | `position`、`state`、`level_flags`、`mouse_transparent` | 4 |

### 1.3 外围零件（支撑，不进主链路）

| 零件 | 是什么 | 关键字段 |
|---|---|---|
| **AppState** 状态机 | 全局唯一状态，驱动 UI 与动作门禁 | `current`、`transitions`、`lock` |
| **TrayIcon** 托盘 | 常驻入口：开始/停止、设置、查看日志、退出 | `menu`、`icon_state` |
| **Config** 配置 | 用户偏好持久化（**只存非敏感项**） | `hotkey`、`device`、`asr_provider`、`asr_region`、`asr_engine`、`polish_enabled`、`overlay_position`、`log_level` |
| **Credential** 凭据 | ASR 密钥的安全载体 | `provider`、`secret_ref`（**不存明文**） |
| **SettingsWindow** 设置界面 | 用户自己录入密钥与偏好的唯一入口 | `provider_form`、`secret_fields`、`test_connection` |
| **Logger** 日志 | 可观测性：全流程 + 错误双文件，含敏感信息脱敏 | `level`、`redact_rules`、`app_log`、`error_log` |
| **LogViewer** 日志查看器 | 只读查看日志尾部，方便自查报错 | `tabs`、`refresh`、`clear` |

---

## 2. 动作（行为）

动作是**零件之间发生的事**。每个动作写明触发者、前置条件、后置结果。
主链路 A1→A8 是一次成功上屏；A9–A12 是异常与辅助路径。

### 2.1 主链路

| # | 动作 | 触发者 | 前置条件 | 后置结果 |
|---|---|---|---|---|
| A1 | **register_hotkey** 注册热键 | 应用启动 | 无 | 热键生效；冲突则给出可见提示 |
| A2 | **toggle** 切换 | 用户按键 | 热键已注册 | 空闲→开始录音；录音中→停止并进入转写 |
| A3 | **capture_focus** 抓焦点 | 进入录音**之前** | 无 | Session 持有 `FocusTarget`（hwnd） |
| A4 | **start_recording** 开始录音 | toggle 进入录音态 | 常驻流已开启 | 环形缓冲开始累积，含 ≥300ms 前置音频 |
| A5 | **stop_recording** 封口 | toggle 退出录音态 | 录音时长 ≥ 最短门禁 | 产出不可变 `AudioClip` |
| A6 | **transcribe** 识别 | 拿到 AudioClip | 凭据可用、网络可达 | 产出 `Transcript`（或超时/失败） |
| A7 | **polish** 加工 | 拿到 Transcript | 加工开关为开 | 产出最终 `text` |
| A8 | **inject** 注入上屏 | 拿到最终 text | 文本非空、焦点仍有效 | 文字出现在原光标处；剪贴板已还原 |

### 2.2 异常与辅助路径

| # | 动作 | 触发者 | 说明 |
|---|---|---|---|
| A9 | **abort** 中止 | 用户按 Esc / 再按一次且时长不足 | 丢弃 Session，不产生任何副作用 |
| A10 | **fallback_inject** 降级注入 | 主策略（粘贴）失败 | 依次尝试 UIA 写入 → 逐字输入 → 仅复制到剪贴板并提示 |
| A11 | **retry_transcribe** 重试 | ASR 超时或 5xx | 最多重试 1 次；仍失败则暂存文本并提示，绝不静默丢字 |
| A12 | **set_overlay_state** 更新浮窗 | 任一状态变化 | 待机 / 录音中 / 转写中 / 完成 / 错误，**永不抢焦点** |
| A13 | **restore_clipboard** 还原剪贴板 | 注入结束（成功或失败） | 必执行，写在 finally 中 |
| A14 | **save_settings** 保存设置 | 用户在设置界面点保存 | 非敏感项落 config.json，密钥落系统凭据库；随后**热重载**（热键重注册、AsrClient 重建、换设备重开流） |
| A15 | **test_connection** 测试连接 | 用户点"测试连接" | 用界面上未保存的凭据 + 1 秒静音探针跑一次识别，只判断连通与鉴权 |
| A16 | **open_logs / clear_logs** 查看与清空日志 | 用户操作 | 读文件尾部展示；清空时重开句柄，不留 NUL 空洞 |

---

## 3. 规矩（约束与不变式）

规矩是**系统任何时刻都必须成立的事实**。违反即 bug。按约束对象分为五组。

### 3.1 时序与并发

| # | 规矩 | 约束谁 | 违反后果 |
|---|---|---|---|
| R1 | 同一时刻**只允许存在一个活跃 Session**；重复触发视为幂等而非新建 | Session、A2 | 连按两次 → 粘贴两份文字 |
| R2 | **焦点先抓后用**：任何 UI 变化（浮窗、托盘、弹窗）发生前，`FocusTarget` 必须已缓存 | A3、A8、Overlay | 浮窗抢焦点 → 粘贴到空气里 |
| R3 | 状态转移只能走转移表，**禁止跨态跳转** | AppState | 出现"转写中又开始录音"的错乱 |
| R4 | 录音回调**只写环形缓冲**，禁止在回调里做任何阻塞操作 | Recorder | 爆音、丢帧、卡顿 |

### 3.2 数据完整性

| # | 规矩 | 约束谁 | 违反后果 |
|---|---|---|---|
| R5 | 音频参数**单一真相源**：16000Hz / 单声道 / 16bit，Recorder 与 AsrClient 共用同一常量 | Recorder、A6 | 采样率错配 → 识别率断崖 |
| R6 | `AudioClip` 一旦封口**不可变** | AudioClip | 并发修改导致识别内容错乱 |
| R7 | 剪贴板**必须还原**，成功失败都要（finally 语义），且保留原有非文本格式 | A8、A13 | 用户复制的图片/富文本被永久破坏 |
| R8 | **空结果不上屏**：`text.strip()` 为空则跳过注入，仅在浮窗提示 | A8 | 输入焦点被无意义地改动 |

### 3.3 交互边界

| # | 规矩 | 约束谁 | 违反后果 |
|---|---|---|---|
| R9 | 录音时长门禁：最短 300ms（防误触），最长 300s（自动停止并走正常转写） | A5 | 误触产生垃圾请求；忘按停止导致内存膨胀 |
| R10 | **浮窗永不激活**：不抢焦点、不进 Alt+Tab、鼠标穿透、不拦截点击 | Overlay | 需求 3 直接失效 |
| R11 | 热键注册失败**必须可见**：明确告知被谁占用，不能静默降级 | A1 | 用户以为工具坏了 |
| R12 | 注入前**复核焦点仍有效**（窗口未销毁、进程未变），失效则降级为"复制到剪贴板 + 提示" | A8 | 粘贴到错误的窗口 |
| R19 | **一次按键只产生一次触发**：Windows 按下与松开都会发 WM_HOTKEY，必须做边沿消抖（触发后等按键真正松开才重新武装），并按住时不自动重复 | A1、A2 | 按一下 = 开始又立刻结束，录了个寂寞 |

### 3.4 容错与可观测

| # | 规矩 | 约束谁 | 违反后果 |
|---|---|---|---|
| R13 | ASR 超时 8s → 重试 1 次 → 仍失败则**暂存文本并明确报错**，绝不静默丢弃用户说过的话 | A6、A11 | 用户白说一段，且毫不知情 |
| R14 | 任何错误必须在**浮窗可见**，并写入脱敏日志 | Overlay、Logger | 出问题无法定位 |
| R17 | **双文件日志**：`app.log` 记全流程（INFO+），`error.log` 只记 WARNING+ 且带文件名行号；未捕获异常（主线程、后台线程、Qt 消息）**一律入账** | Logger、A16 | 崩溃无声无息，用户和开发者都无从下手 |

### 3.5 安全与工程

| # | 规矩 | 约束谁 | 违反后果 |
|---|---|---|---|
| R15 | 凭据**不落明文、不进代码、不进日志**：走 keyring 或环境变量，日志脱敏 | Credential、Logger | 密钥泄露 |
| R16 | 跨线程**只发信号**，禁止子线程直接操作 Qt 控件 | 全部 | 界面随机崩溃、死锁 |
| R18 | 密钥**只经设置界面（或环境变量）录入**：输入框默认掩码、展示一律脱敏、绝不要求用户把密钥贴进对话；凭据库写入失败要如实告知并提供环境变量退路 | A14、Credential | 密钥经聊天记录/日志外泄 |

---

## 4. 状态机（Session 生命周期）

```
        [按键]                    [按键]
  IDLE ───────→ RECORDING ──────────→ TRANSCRIBING
    ↑              │  [Esc/时长不足]        │
    │              ↓                       ↓
    │           ABORTED                 INJECTING
    │                                       │
    └───── DONE ◄──────────────────────────┘
                  [失败/超时]
                       ↓
                    ERROR ──[重试1次]──→ TRANSCRIBING
                       │
                       └──[放弃]──→ DONE（文本暂存，提示可手动粘贴）
```

**允许的状态转移**

| 从 | 到 | 触发 |
|---|---|---|
| IDLE | RECORDING | toggle（A2 → A3 → A4） |
| RECORDING | TRANSCRIBING | toggle（A5） |
| RECORDING | ABORTED | Esc 或时长 < 300ms（A9） |
| TRANSCRIBING | INJECTING | 识别成功（A6 → A7） |
| TRANSCRIBING | ERROR | 超时/失败（A11 后仍失败） |
| ERROR | TRANSCRIBING | 重试（最多 1 次） |
| INJECTING | DONE | 注入完成（A8 → A13） |
| ABORTED / DONE | IDLE | 收尾清理 |
| **任意** | IDLE | 用户主动取消（必须触发清理与剪贴板还原） |

---

## 5. 零件 × 动作 × 规矩 对照

| 零件 | 主要动作 | 主要约束 |
|---|---|---|
| Hotkey | A1、A2 | R1、R11 |
| FocusTarget | A3、A12 | R2、R12 |
| Recorder / AudioClip | A4、A5 | R4、R5、R6、R9 |
| AsrClient / Transcript | A6、A11 | R5、R13 |
| PostProcessor | A7 | R8 |
| Injector / ClipboardSnapshot | A8、A10、A13 | R7、R8、R12 |
| Overlay | A12 | R10、R14 |
| AppState / Session | 全部 | R1、R3、R16 |
| SettingsWindow | A14、A15 | R18 |
| Credential / Logger / LogViewer | A16、支撑 | R15、R17、R18 |

> **修订记录**：新增零件 `SettingsWindow`、`LogViewer`；新增动作 A14–A16；新增规矩 R17、R18、R19。
> 原因：密钥不再由开发者代录，改由用户经界面自行填写；报错需要独立可查的错误日志；
> 打包实测发现 Windows 按下与松开都会发 WM_HOTKEY，导致一次按键触发两次。
