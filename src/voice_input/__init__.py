"""VoiceInput Windows 版包入口。

提供版本号与子模块说明：
- asr_base: ASR 服务抽象基类（统一接口）
- diag_log: 诊断日志（最底层依赖，所有模块共用）
- config: 多提供商配置文件与环境变量读写
- state_machine: 录音状态机
- audio_recorder: 麦克风采集
- volc_asr_service: 火山引擎豆包 ASR WebSocket 客户端（已实现）
- xfly_asr_service: 科大讯飞 ASR 客户端（Stub）
- tencent_asr_service: 腾讯云 ASR 客户端（Stub）
- aliyun_asr_service: 阿里云 ASR 客户端（Stub）
- text_cleaner: 识别结果文本清洗
- text_injector: 剪贴板写入与模拟粘贴
- hotkey_manager: 全局快捷键监听
- api_key_dialog: 多提供商凭证配置对话框
- floating_panel_controller: 录音悬浮面板
- status_bar_controller: 系统托盘图标
- app: 主应用编排
"""

__version__ = "2.0.0"
