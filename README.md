# 这是一个全程用 workbuddy 开发的语音输入工具

按一下快捷键开始说话，再按一下停，文字自动粘到光标所在位置。

技术栈：Python 3.12 + PySide6 ｜ 平台：Windows x64

## 设计文档

| 文档                 | 内容                              |
| ------------------ | ------------------------------- |
| `docs/ONTOLOGY.md` | 本体：零件 / 动作 / 规矩（实现契约，改代码前先对齐这里） |
| `docs/TOOLS.md`    | 工具选型：每个零件用什么库、为什么不用别的           |

## 快速开始

```bash
# 1. 建环境（--system-site-packages 复用已装的大包，只补装缺失的）
python -m venv --system-site-packages .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. 运行
set PYTHONPATH=src
.venv/Scripts/python.exe -m voice_input.main
```

启动后托盘出现圆点图标，按 **Ctrl+Alt+空格** 开始录音，再按一次停止并上屏。

**首次运行会自动弹出设置窗口**，在「识别服务」页填密钥即可，不需要改代码或配置文件。
之后随时右键托盘图标 → **设置…** 打开；→ **查看日志…** 看报错。

| 文件 | 用途 |
|---|---|
| `%APPDATA%/voice_input/config.json` | 非敏感配置（热键、设备、服务商、引擎等），可手改 |
| `%APPDATA%/voice_input/app.log` | 全流程日志（INFO 及以上，滚动 1MB × 3） |
| `%APPDATA%/voice_input/error.log` | **只记报错**（WARNING 及以上，带文件名行号），排查问题先看这个 |
| Windows 凭据管理器 | 只存密钥（SecretId / SecretKey / API Key），**不落明文、不进 json、不进日志** |

## 目录结构

```
src/voice_input/
  constants.py    音频参数等单一真相源（R5）
  models.py       值对象 + 状态机 + Session（本体代码化）
  config.py       Config / Credential / 日志脱敏
  recorder.py     常驻流 + 前置缓冲（治丢音头）
  asr.py          AsrClient 抽象 + Mock + OpenAI 兼容实现
  focus.py        焦点抓取 + 剪贴板全格式快照
  injector.py     剪贴板粘贴注入 + 降级策略
  ui.py           Overlay 浮窗 + 托盘（含设置/日志入口）
  settings.py     设置界面 + 日志查看器（密钥只在这里录入）
  hotkey.py       RegisterHotKey + Qt 原生事件
  app.py          Orchestrator：状态机编排 + 配置热重载
  main.py         入口装配
tests/            冒烟 / 集成 / 注入端到端 / 设置与日志测试
```

## 打包成 exe（双击即用）

```bash
# 方式一：双击 packaging/build.bat（最省事）
# 方式二：命令行
.venv/Scripts/python.exe -m pip install pyinstaller
.venv/Scripts/python.exe -m PyInstaller packaging/VoiceInput.spec --noconfirm --distpath dist --workpath build
```

产物：`dist/VoiceInput.exe`（约 74MB，**单文件，可复制到任意位置双击运行**，不需要装 Python）。

```bash
dist/VoiceInput.exe --diag    # 自检：检查凭据库、麦克风、热键、Qt、托盘，结果写进日志
```

打包要注意的三件事（都已在 `packaging/VoiceInput.spec` 里处理）：

1. **keyring 的后端是靠 entry_points 动态发现的**，必须带上它的 dist-info 元数据（`copy_metadata`），
   否则打包后找不到 Windows 凭据库后端 → 密钥存不进去。
2. **spec 里的相对路径不生效**，必须用 `SPECPATH` 拼绝对路径，否则 `voice_input` 包根本不会被收集
   （现象是 exe 能启动但报 `No module named 'voice_input'`）。
3. **`--noconsole` 下 `sys.stderr` 是 None**，`config.setup_logging` 已判断，不再挂控制台日志句柄。

## 测试

```bash
.venv/Scripts/python.exe tests/exe_e2e.py           # 打包产物端到端：启动 exe、模拟按热键、校验上屏
.venv/Scripts/python.exe tests/smoke_test.py        # 纯逻辑，11 项
.venv/Scripts/python.exe tests/integration_test.py  # 编排链路 + 浮窗不抢焦点
.venv/Scripts/python.exe tests/inject_test.py       # 真实粘贴到输入框 + 剪贴板还原
.venv/Scripts/python.exe tests/settings_test.py     # 设置界面 + 双日志 + 脱敏，20 项
```

## 使用 exe 的两个提醒

- **首次启动约 3~8 秒**：单文件 exe 每次启动要把内容解压到临时目录，之后托盘常驻即可，不要反复关。
- **可能被杀毒软件误报**：PyInstaller 打包的程序是误报重灾区。若被拦截，在 Defender 里加白名单
  （「病毒和威胁防护 → 保护历史记录 → 允许」）。

## 接入真实 ASR（腾讯云）

**推荐：全在设置界面里完成**，不用碰命令行：

1. 右键托盘 → 设置… → 服务商选「腾讯云 · 一句话识别」
2. 填 SecretId / SecretKey（输入框默认掩码，可勾"显示"核对），选地域与引擎
3. 点「测试连接」——它会用这两把密钥跑一次识别，通了再保存
4. 点「保存并生效」——密钥进凭据库，热键与识别客户端**立即热重载，无需重启**

命令行方式（等价，可选）：

```bash
.venv/Scripts/python.exe tests/asr_check.py --save <SecretId> <SecretKey>
.venv/Scripts/python.exe tests/asr_check.py        # 不给文件则现场录 3 秒自检
```

OpenAI 兼容端点同理：服务商选「OpenAI 兼容接口」，填接口地址与 API Key。
若凭据库不可用，也可用环境变量 `VOICE_INPUT_ASR_SECRET_ID` /
`VOICE_INPUT_ASR_SECRET_KEY` / `VOICE_INPUT_ASR_API_KEY` 代替。

## 当前状态

已跑通：热键 → 录音 → 识别（Mock）→ 粘贴到光标处 → 剪贴板还原 → 浮窗状态。  
腾讯云客户端已实现（官方 SDK 负责 TC3 签名），**密钥在设置界面里填好即可用**。  
设置界面、密钥凭据库、双文件日志、全局异常捕获均已实机验证。

## 已知限制

- ASR 默认用 `MockAsrClient` 返回固定文本，配置密钥后才有真实识别能力
- 腾讯云"一句话识别"限制音频 **≤60 秒、≤3MB**，因此录音上限设为 55 秒
- 一句话识别是"录完再传"而非流式，长句有 1~2 秒延迟
- 剪贴板中位图类（延迟渲染）格式无法完整备份，只保证文本 / HTML / 文件列表还原
- 管理员权限的窗口之间可能因 UIPI 限制无法注入输入
- 日志脱敏靠模式匹配（`secret=xxx`、`AKIDxxx` 等），异常堆栈里的密钥不会被改写；日志目录仅本机可读
- 密钥依赖 Windows 凭据管理器；若该后端不可用会明确提示，需改用环境变量
