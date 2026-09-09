"""主应用编排模块。

对应 macOS 原项目 AppDelegate.swift：
1. 启动时 load config，未配置则弹 APIKeyDialog
2. 创建各组件：StateMachine / AudioRecorder / ASRBase 实现 / HotkeyManager / FloatingPanelController / StatusBarController
3. 注册热键回调、菜单回调、ASR 回调、音量回调
4. 启动托盘（后台线程）、热键（后台线程）、tkinter 主循环（主线程）

ASR 提供商工厂模式：
- 通过 _ASR_PROVIDERS 注册表映射提供商ID到实现类
- 新增提供商只需在注册表中添加一行

热键触发流转（与原项目一致）：
- idle -> recording: 创建 ASR、连接 WebSocket、开始录音、启动 200ms 拉取定时器
- recording -> transcribing: 停录音、拉最后一批、ASR.finish()
- transcribing 忽略热键
- ASR onComplete 在主线程回调：basic_cleanup -> inject -> show_final -> force_idle
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Type

from . import config as config_module
from . import text_cleaner
from . import text_injector
from .api_key_dialog import APIKeyDialog
from .asr_base import ASRBase, ASRResult
from .audio_recorder import AudioRecorder, samples_to_int16_le
from .diag_log import DiagLog
from .floating_panel_controller import FloatingPanelController
from .hotkey_manager import HotkeyManager
from .llm_post_processor import polish_text
from .llm_settings_dialog import LLMSettingsDialog
from .state_machine import StateMachine
from .status_bar_controller import StatusBarController
from .wake_word_detector import WAKE_WORD, WakeWordDetector
from .wake_word_dialog import WakeWordDialog

# 导入各 ASR 提供商实现（延迟在工厂函数中实例化）
from .volc_asr_service import VolcASRService
from .xfly_asr_service import XFlyASRService
from .tencent_asr_service import TencentASRService
from .aliyun_asr_service import AliyunASRService


# 音频拉取间隔（毫秒）
_AUDIO_POLL_MS = 200

# ASR 提供商注册表：提供商ID → 实现类
_ASR_PROVIDERS: Dict[str, Type[ASRBase]] = {
    "volc": VolcASRService,
    "xfly": XFlyASRService,
    "tencent": TencentASRService,
    "aliyun": AliyunASRService,
}


def create_asr(provider: str, credentials: Dict[str, str]) -> ASRBase:
    """工厂函数：根据提供商名称创建对应 ASR 实例。

    Args:
        provider: 提供商ID（volc / xfly / tencent / aliyun）
        credentials: 凭证字典，各提供商字段不同

    Returns:
        对应 ASR 实现类的实例

    Raises:
        ValueError: 当提供商ID不在注册表中
        NotImplementedError: 当提供商实现为 stub（尚未实现）
    """
    cls = _ASR_PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(f"不支持的 ASR 提供商: {provider}")
    return cls(credentials)


class VoiceInputApp:
    """VoiceInput 主应用。"""

    def __init__(self) -> None:
        # 状态机
        self._state = StateMachine()
        # 悬浮面板（主线程）
        self._panel = FloatingPanelController()
        # 录音器
        self._recorder = AudioRecorder()
        # ASR 客户端（每次录音新建，类型为 ASRBase）
        self._asr: Optional[ASRBase] = None
        # 热键管理器
        self._hotkey = HotkeyManager()
        # 托盘控制器
        self._tray = StatusBarController()
        # 语音唤醒检测器（Vosk 离线检测；唤醒词/结束词均可在设置对话框自定义）
        self._wake = WakeWordDetector(
            on_wake=self._on_wake_word,
            wake_word=config_module.load().wake_word,
            stop_word=config_module.load().stop_word,
            on_stop=self._on_stop_word,
        )
        # 当前实时音量（由录音回调更新）
        self._current_amplitude = 0.0
        # 当前 partial 文本（用于面板刷新）
        self._current_partial = ""
        # 拉取定时器 id
        self._poll_id: Optional[str] = None
        # 退出标志
        self._quitting = False
        # 当前提供商名称（用于面板显示）
        self._current_provider_name = "语音识别"

        # 绑定组件回调
        self._state.on_state_change = self._on_state_change
        self._hotkey.on_triggered = self._on_hotkey
        self._tray.set_scheduler(self._panel.schedule_on_main)
        self._tray.on_show_panel = self._on_show_panel
        self._tray.on_setup_credentials = self._on_setup_credentials
        self._tray.on_test_hotkey = self._on_test_hotkey
        self._tray.on_test_5s_recording = self._on_test_5s_recording
        self._tray.on_setup_wake_word = self._open_wake_settings
        self._tray.on_setup_llm = self._open_llm_settings
        self._tray.on_quit = self._on_quit
        self._recorder.on_amplitude = self._on_amplitude
        self._recorder.on_silence_timeout = self._on_silence_timeout
        # 分接录音音频给结束词检测（录音期间复用同一麦克风流）
        self._recorder.on_audio_chunk = self._wake.feed_audio
        # 语音结束词触发标记：True 时在识别结果中剔除结束词文本
        self._strip_stop_word = False
        # 录音开始时记录的目标窗口句柄，注入前据此切回焦点
        # （AI 润色期间用户可能切走焦点，需要切回避免按键丢失）
        self._target_hwnd: int = 0

    def run(self) -> None:
        """应用入口：检查凭证、启动托盘与热键、运行主循环。

        启动时的凭证检查逻辑：
        - 未配置（is_configured=False）→ 弹出凭证对话框
        - 配置来自环境变量（is_from_env=True）→ 也弹出对话框，
          因为环境变量可能是无意中设置的，用户需要知道可以在对话框中配置
        - 配置来自用户保存的配置文件 → 不弹窗，用户已主动配置过
        """
        DiagLog.shared().write("[App] 启动 VoiceInput Windows 版")

        # 凭证检查：未配置或来自环境变量时弹对话框
        cfg = config_module.load()
        if not cfg.is_configured or cfg.is_from_env:
            DiagLog.shared().write(
                f"[App] 弹出凭证对话框（已配置={cfg.is_configured}, "
                f"来自环境变量={cfg.is_from_env}）"
            )
            # 调度到主线程：先显示根窗口再弹对话框
            self._panel.schedule_on_main(lambda: self._prompt_credentials_if_needed())

        # 启动托盘（后台线程）
        self._tray.start()

        # 启动热键监听（后台线程）
        self._hotkey.start()

        # 启动语音唤醒/结束词检测（Vosk 离线；未开启或模型/依赖缺失时静默跳过）
        wake_cfg = config_module.load()
        if wake_cfg.wake_word_enabled or wake_cfg.stop_word_enabled:
            if self._wake.start():
                if not wake_cfg.wake_word_enabled:
                    # 仅结束词模式：唤醒词已禁用，不打开空闲监听流
                    self._wake.pause()
            else:
                DiagLog.shared().write(
                    "[App] 语音唤醒启动失败（缺少 Vosk 模型或依赖），热键仍可用"
                )

        # 默认打开工具面板（就绪状态），无需点击托盘图标即可看到主界面
        self._panel.schedule_on_main(
            lambda: self._panel.show_idle(
                provider_name=config_module.load().get_provider_display_name(),
                wake_word=self._wake_hint(),
            )
        )

        # 主线程跑 tkinter 主循环
        self._panel.run_mainloop()

        # 退出后清理
        self._cleanup()

    def _prompt_credentials_if_needed(self) -> None:
        """主线程调用：弹出凭证配置对话框。

        当配置来自环境变量或未配置时调用此方法。
        对话框会预填现有凭证值，用户可以修改后保存。
        """
        dialog = APIKeyDialog(self._panel.root())
        dialog.show()

    # ===== 热键触发逻辑 =====

    def _on_hotkey(self) -> None:
        """热键触发：调度到主线程处理状态切换。"""
        self._panel.schedule_on_main(self._handle_toggle)

    def _handle_toggle(self) -> None:
        """主线程处理状态切换。"""
        if self._state.state == StateMachine.STATE_TRANSCRIBING:
            # 识别中忽略
            return
        cfg = config_module.load()
        if not cfg.is_configured:
            provider_name = cfg.get_provider_display_name()
            self._panel.show_error(
                f"凭证未配置，请从托盘菜单设置{provider_name}语音识别"
            )
            return

        if self._state.state == StateMachine.STATE_IDLE:
            self._begin_recording()
        else:
            self._end_recording()

    def _begin_recording(self) -> None:
        """开始录音：切换状态、启动 ASR 与录音器、启动拉取定时器。"""
        # 在任何 UI 变化前记录当前前台窗口，注入时据此切回焦点
        # （热键触发时焦点在目标输入框；唤醒词触发时焦点在用户当时使用的窗口）
        self._target_hwnd = text_injector.get_foreground_hwnd()
        # 状态机切换
        if not self._state.toggle_recording():
            return
        self._current_partial = ""
        self._current_amplitude = 0.0

        cfg = config_module.load()
        self._current_provider_name = cfg.get_provider_display_name()

        # 创建 ASR 客户端（通过工厂模式）
        try:
            self._asr = create_asr(cfg.provider, cfg.get_current_credentials())
        except ValueError as e:
            # 凭证校验失败（如缺少必填字段）
            DiagLog.shared().write(f"[App] ASR 凭证错误: {e}")
            self._panel.show_error(f"凭证错误: {e}")
            self._state.force_idle()
            return
        except Exception as e:
            DiagLog.shared().write(f"[App] 创建 ASR 实例失败: {e}")
            self._panel.show_error(f"ASR 服务初始化失败: {e}")
            self._state.force_idle()
            return

        self._asr.on_partial = self._on_asr_partial
        self._asr.on_complete = self._on_asr_complete

        # 启动 ASR（异步连接 WebSocket）
        try:
            self._asr.start()
        except NotImplementedError as e:
            # 提供商尚未实现（Stub），友好提示用户
            DiagLog.shared().write(f"[App] ASR 未实现: {e}")
            self._panel.show_error(
                f"{self._current_provider_name} ASR 服务尚未实现，"
                f"请在托盘菜单中切换到「火山引擎豆包」"
            )
            self._state.force_idle()
            self._asr = None
            return
        except Exception as e:
            DiagLog.shared().write(f"[App] ASR 启动失败: {e}")
            self._panel.show_error(f"ASR 启动失败: {e}")
            self._state.force_idle()
            return

        # 启动录音
        try:
            self._recorder.start()
        except Exception as e:
            DiagLog.shared().write(f"[App] 录音启动失败: {e}")
            self._panel.show_error(f"录音启动失败: {e}")
            # 回滚 ASR
            if self._asr is not None:
                self._asr.cancel()
                self._asr = None
            self._state.force_idle()
            return

        # 面板显示录音中（带提供商名称和明显的视觉标识）
        self._panel.show_recording(provider_name=self._current_provider_name)
        # 启动拉取定时器
        self._schedule_poll()

    def _end_recording(self) -> None:
        """结束录音：切换状态、停录音、拉最后一批、ASR.finish。"""
        if not self._state.toggle_recording():
            return
        # 停止录音
        self._recorder.stop()
        # 拉取最后一批并发送
        last_samples = self._recorder.pull_since_last()
        pcm = samples_to_int16_le(last_samples)
        if self._asr is not None and pcm:
            self._asr.send_audio(pcm)
        # 通知 ASR 音频结束
        if self._asr is not None:
            self._asr.finish()
        # 取消拉取定时器
        self._cancel_poll()
        # 面板显示识别中
        self._panel.show_transcribing(self._current_partial)

    # ===== ASR 回调（在 ASR 线程触发，调度到主线程） =====

    def _on_asr_partial(self, text: str) -> None:
        """ASR partial 文本回调：实时更新 partial 文本和音量条。

        使用新的 _transcribe_partial 方法，只更新文字和音量而不重绘整个面板，
        这样脉冲动画不会被打断。
        """
        self._current_partial = text
        amp = self._current_amplitude
        self._panel.schedule_on_main(
            lambda: self._panel._transcribe_partial(text, amp)
        )

    def _on_asr_complete(self, result: Optional[ASRResult]) -> None:
        """ASR 完成回调：清洗并注入文本。"""
        self._panel.schedule_on_main(lambda: self._handle_asr_complete(result))

    def _handle_asr_complete(self, result: Optional[ASRResult]) -> None:
        """主线程处理 ASR 完成。"""
        if result is None or not result.text:
            # ASR 失败：停掉录音器避免 InputStream 持续占用麦克风
            self._recorder.stop()
            self._cancel_poll()
            self._panel.show_error("识别失败或无结果")
            self._state.force_idle()
            self._asr = None
            return
        # 清洗文本
        cleaned = text_cleaner.basic_cleanup(result.text)
        # 语音结束词触发时，云识别结果会包含结束词本身，注入前剔除
        if self._strip_stop_word:
            self._strip_stop_word = False
            stop = config_module.load().stop_word
            if stop and stop in cleaned:
                cleaned = cleaned.replace(stop, "")
                # 去除剔除后残留的边缘标点与空白
                cleaned = cleaned.strip(" ，。,.！!？?、；;：: \n\t")
                DiagLog.shared().write("[App] 已从识别结果中剔除结束词")
        DiagLog.shared().write(f"[App] 清洗后文本: {cleaned[:80]}")
        # 空文本：直接展示原始结果并复位（无可润色内容）
        if not cleaned:
            self._finish_inject(result.text)
            return
        # AI 润色：启用且凭证完整时，异步调用大模型纠错并按要点换行
        cfg = config_module.load()
        if cfg.llm_enabled and cfg.llm_api_key and cfg.llm_model:
            self._start_llm_polish(cleaned)
            return
        # 未启用润色：直接注入（在主线程同步执行，内部有 sleep，会短暂阻塞 UI）
        self._finish_inject(cleaned)

    def _finish_inject(self, final_text: str) -> None:
        """注入最终文本并复位状态（主线程调用）。

        Args:
            final_text: 待注入的最终文本
        """
        if final_text:
            ok = text_injector.inject(final_text, target_hwnd=self._target_hwnd)
            if not ok:
                # 焦点切换失败：剪贴板已保留文本，提示用户手动粘贴
                DiagLog.shared().write("[App] 注入失败：目标窗口已切换，提示用户手动粘贴")
                self._panel.show_error("目标窗口已切换，请到目标位置按 Ctrl+V 粘贴")
                self._state.force_idle()
                self._asr = None
                self._target_hwnd = 0
                return
        self._panel.show_final(final_text)
        # 复位状态
        self._state.force_idle()
        self._asr = None
        self._target_hwnd = 0

    def _start_llm_polish(self, text: str) -> None:
        """后台线程调用 LLM 润色，完成后回主线程注入。

        润色期间面板显示「AI 润色中」；调用失败或超时自动回退原文，
        保证识别结果不因润色环节而丢失。

        Args:
            text: 清洗后的 ASR 文本
        """
        cfg = config_module.load()
        base_url = cfg.llm_base_url or config_module.LLM_DEFAULT_BASE_URL
        model = cfg.llm_model
        api_key = cfg.llm_api_key
        # 面板切换为润色中状态（旋转动画，提示用户稍候）
        self._panel.show_polishing(text)
        DiagLog.shared().write(f"[App] AI 润色开始: model={model}")

        def worker() -> None:
            """润色工作线程：成功用润色文本，失败回退原文。"""
            try:
                polished = polish_text(
                    text,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                )
            except Exception as e:
                DiagLog.shared().write(f"[App] LLM 润色失败，使用原文: {e}")
                polished = text
            # 回到主线程注入文本
            self._panel.schedule_on_main(lambda: self._finish_inject(polished))

        threading.Thread(target=worker, daemon=True).start()

    # ===== 音频拉取定时器 =====

    def _schedule_poll(self) -> None:
        """安排下一次音频拉取。"""
        self._poll_id = self._panel.root().after(_AUDIO_POLL_MS, self._poll_audio)

    def _cancel_poll(self) -> None:
        """取消挂起的拉取任务。"""
        if self._poll_id is not None:
            try:
                self._panel.root().after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

    def _poll_audio(self) -> None:
        """定时拉取音频并发送 ASR。"""
        if self._state.state != StateMachine.STATE_RECORDING:
            return
        samples = self._recorder.pull_since_last()
        pcm = samples_to_int16_le(samples)
        if pcm and self._asr is not None:
            self._asr.send_audio(pcm)
        # 继续调度下一次
        self._schedule_poll()

    # ===== 录音器音量回调（在录音线程触发） =====

    def _on_amplitude(self, amp: float) -> None:
        """记录当前音量值，由定时器一并刷新面板。"""
        self._current_amplitude = amp

    def _on_silence_timeout(self) -> None:
        """录音线程：静音超时回调，调度到主线程处理。"""
        self._panel.schedule_on_main(self._handle_silence_timeout)

    def _handle_silence_timeout(self) -> None:
        """主线程：静音超时——停止录音并取消 ASR，不再白录等待服务端超时。"""
        if self._state.state != StateMachine.STATE_RECORDING:
            return
        DiagLog.shared().write("[App] 静音超时，自动结束本次录音")
        # 停止录音
        self._recorder.stop()
        self._cancel_poll()
        # 取消 ASR 会话（音频全是静音，无需送识别）
        if self._asr is not None:
            self._asr.cancel()
            self._asr = None
        self._panel.show_error("连续 5 秒未检测到声音，已自动结束。请检查麦克风是否静音或输入设备被切换")
        self._state.force_idle()

    # ===== 状态机变更回调（任意线程触发，需调度到主线程） =====

    def _on_state_change(self, new_state: str) -> None:
        """状态变更通知：控制唤醒监听与结束词检测的模式切换。

        - 空闲：唤醒词启用时恢复常驻监听流；仅结束词模式则保持关闭
        - 录音：关闭唤醒监听流让出麦克风；结束词启用时进入分接检测模式
        - 识别中：录音器已停，退出结束词检测并清空积压
        """
        cfg = config_module.load()
        if new_state == StateMachine.STATE_IDLE:
            if cfg.wake_word_enabled:
                self._wake.resume()
            else:
                self._wake.pause()
        elif new_state == StateMachine.STATE_RECORDING:
            self._wake.pause()
            if cfg.stop_word_enabled:
                self._wake.start_stop_listen()
        else:  # transcribing
            self._wake.pause()
            self._wake.stop_stop_listen()

    # ===== 托盘菜单回调 =====

    def _on_show_panel(self) -> None:
        """菜单：显示工具面板（就绪状态）。"""
        self._panel.schedule_on_main(
            lambda: self._panel.show_idle(
                provider_name=config_module.load().get_provider_display_name(),
                wake_word=self._wake_hint(),
            )
        )

    def _on_setup_credentials(self) -> None:
        """菜单：设置语音识别凭证。"""
        dialog = APIKeyDialog(self._panel.root())
        dialog.show()

    def _open_wake_settings(self) -> None:
        """菜单：打开语音唤醒设置对话框。

        对话框保存后热更新检测器（重建语法识别器）与面板提示，
        无需重启应用；关闭对话框（取消）则不做任何变更。
        """
        dialog = WakeWordDialog(self._panel.root(), self._wake)
        if not dialog.show():
            return
        cfg = config_module.load()
        # 热更新唤醒词与结束词（检测器运行中则重建识别器，未运行则记录待 start 使用）
        self._wake.update_wake_word(cfg.wake_word)
        self._wake.update_stop_word(cfg.stop_word)
        self._apply_wake_state(cfg)
        # 组装面板反馈消息
        parts = []
        if cfg.wake_word_enabled:
            parts.append(f"唤醒词「{cfg.wake_word}」")
        if cfg.stop_word_enabled:
            parts.append(f"结束词「{cfg.stop_word}」")
        if not parts:
            message = "语音唤醒已关闭（热键 Ctrl+Alt+K 仍可用）"
        elif not self._wake.available:
            message = "语音唤醒开启失败：缺少 Vosk 模型或依赖"
        else:
            message = "语音唤醒已更新：" + "、".join(parts)
        self._panel.show_message_test(message)
        # 空闲时刷新就绪面板，同步最新的唤醒词提示
        if self._state.state == StateMachine.STATE_IDLE:
            self._panel.show_idle(
                provider_name=cfg.get_provider_display_name(),
                wake_word=self._wake_hint(),
            )

    def _open_llm_settings(self) -> None:
        """菜单：打开 AI 润色设置对话框。

        保存后下次识别完成即生效，无需重启；
        关闭对话框（取消）则不做任何变更。
        """
        dialog = LLMSettingsDialog(self._panel.root())
        if not dialog.show():
            return
        cfg = config_module.load()
        if cfg.llm_enabled:
            message = f"AI 润色已启用（模型: {cfg.llm_model}）"
        else:
            message = "AI 润色已关闭"
        self._panel.show_message_test(message)

    def _apply_wake_state(self, cfg) -> None:
        """按最新配置同步唤醒检测器的运行状态（对话框保存后调用）。

        - 都未启用：停止检测线程
        - 任一启用但未运行：启动检测线程（仅结束词模式不开空闲监听流）
        - 运行中：空闲监听流仅在唤醒词启用时打开
        """
        need_running = cfg.wake_word_enabled or cfg.stop_word_enabled
        if need_running and not self._wake.is_running:
            if not self._wake.start():
                return
        elif not need_running:
            self._wake.stop()
            return
        # 运行中：按唤醒词开关同步空闲监听流
        if self._state.state == StateMachine.STATE_IDLE:
            if cfg.wake_word_enabled:
                self._wake.resume()
            else:
                self._wake.pause()

    def _wake_hint(self) -> str:
        """返回就绪面板的唤醒词提示（未开启或不可用时返回空串）。"""
        if self._wake.available and config_module.load().wake_word_enabled:
            return config_module.load().wake_word or WAKE_WORD
        return ""

    # ===== 语音唤醒回调 =====

    def _on_wake_word(self) -> None:
        """唤醒词命中（检测线程触发）：调度到主线程处理。"""
        self._panel.schedule_on_main(self._handle_wake_word)

    def _handle_wake_word(self) -> None:
        """主线程处理唤醒命中：仅在空闲状态开始录音。

        若命中时已在录音/识别（如热键抢先触发），直接忽略，
        避免把正在进行的录音误切换为结束。
        """
        if self._state.state != StateMachine.STATE_IDLE:
            DiagLog.shared().write("[Wake] 命中时非空闲状态，忽略本次唤醒")
            return
        DiagLog.shared().write("[App] 语音唤醒触发录音")
        self._handle_toggle()

    # ===== 语音结束词回调 =====

    def _on_stop_word(self) -> None:
        """结束词命中（检测线程触发）：调度到主线程处理。"""
        self._panel.schedule_on_main(self._handle_stop_word)

    def _handle_stop_word(self) -> None:
        """主线程处理结束词命中：结束录音，并在识别结果中剔除结束词。

        结束词语音本身也在录音流里，云识别会把它转写进结果，
        因此设置剔除标记，注入前从文本中删除。
        """
        if self._state.state != StateMachine.STATE_RECORDING:
            return
        DiagLog.shared().write("[App] 语音结束词触发停止录音")
        self._strip_stop_word = True
        self._end_recording()

    def _on_test_hotkey(self) -> None:
        """菜单：测试快捷键。"""
        self._panel.show_message_test("快捷键测试：触发一次")

    def _on_test_5s_recording(self) -> None:
        """菜单：5秒自动录音测试。"""
        if not config_module.load().is_configured:
            cfg = config_module.load()
            provider_name = cfg.get_provider_display_name()
            self._panel.show_error(f"凭证未配置，请先设置{provider_name}语音识别")
            return
        if self._state.state != StateMachine.STATE_IDLE:
            self._panel.show_error("正在工作中，无法开始测试")
            return
        DiagLog.shared().write("[App] 启动 5 秒自动录音测试")
        # 开始录音
        self._begin_recording()
        # 5 秒后自动结束
        self._panel.root().after(5000, self._auto_end_recording)

    def _auto_end_recording(self) -> None:
        """5 秒测试自动结束录音。"""
        if self._state.state == StateMachine.STATE_RECORDING:
            self._end_recording()

    def _on_quit(self) -> None:
        """菜单：退出应用。"""
        DiagLog.shared().write("[App] 收到退出请求")
        self._quitting = True
        # 取消定时器
        self._cancel_poll()
        # 停止录音与 ASR
        if self._recorder.is_running():
            self._recorder.stop()
        if self._asr is not None:
            self._asr.cancel()
            self._asr = None
        # 停止热键、语音唤醒与托盘
        self._hotkey.stop()
        self._wake.stop()
        self._tray.stop()
        # 退出 tkinter 主循环
        self._panel.quit_mainloop()

    def _cleanup(self) -> None:
        """退出后清理。"""
        self._hotkey.stop()
        self._wake.stop()
        self._tray.stop()
        DiagLog.shared().write("[App] 已退出")
