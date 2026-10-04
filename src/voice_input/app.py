"""Orchestrator：把零件串成一条链路（动作 A2→A13，规矩 R1/R2/R8/R9/R13）。

线程约定（规矩 R16）：ASR 在后台线程跑，结果用信号回主线程，
任何控件操作只发生在主线程。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from .asr import AsrClient
from .config import Config
from .constants import ASR_RETRY
from .focus import capture_focus_target
from .hotkey import HotkeyManager
from .injector import Injector
from .models import (
    AppState,
    AudioClip,
    Session,
    StateMachine,
    Transcript,
)
from .postprocess import polish
from .recorder import Recorder
from .ui import Overlay, TrayIcon

logger = logging.getLogger("voice_input.app")


class _AsrWorker(QObject):
    """后台识别，完成后用信号把 Transcript 送回主线程。"""

    done = Signal(object)

    def __init__(self, client: AsrClient, clip: AudioClip) -> None:
        super().__init__()
        self._client = client
        self._clip = clip

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(self._client.transcribe(self._clip))
        except Exception as exc:  # 兜底：绝不让后台线程炸掉整个应用
            self.done.emit(Transcript(error=f"识别异常：{exc}"))


class Orchestrator(QObject):
    """状态机 + 零件编排。"""

    def __init__(
        self,
        config: Config,
        recorder: Recorder,
        asr: AsrClient,
        injector: Injector,
        overlay: Overlay,
        on_quit: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[], None]] = None,
        on_logs: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.recorder = recorder
        self.asr = asr
        self.injector = injector
        self.overlay = overlay
        self.machine = StateMachine()
        self.session = Session()
        self._retry = 0
        self.hotkey: Optional[HotkeyManager] = None
        self._app = None   # 保存上下文，设置改动后才能就地重注册热键
        self._hwnd: Optional[int] = None

        self.machine.on_change(lambda _o, n: self.session.__setattr__("state", n))

        self.tray = TrayIcon(
            on_toggle=self.toggle,
            on_quit=on_quit or (lambda: None),
            on_settings=on_settings,
            on_logs=on_logs,
        )
        self.tray.set_state(AppState.IDLE)

        self._max_timer = QTimer(self)
        self._max_timer.setSingleShot(True)
        self._max_timer.timeout.connect(self._on_max_duration)

        self._thread: Optional[QThread] = None
        self._worker: Optional[_AsrWorker] = None

    # ---------------- 触发入口（动作 A2） ----------------

    def toggle(self) -> None:
        """按一下开始，再按一下停。非 IDLE/RECORDING 状态的触发一律忽略（R1）。"""
        state = self.machine.state
        if state == AppState.IDLE:
            self._begin()
        elif state == AppState.RECORDING:
            self._finish_recording()
        else:
            logger.debug("忽略重复触发（当前状态 %s）", state.value)

    def abort(self) -> None:
        """动作 A9：中止本次会话，不产生任何副作用。"""
        if self.machine.state != AppState.RECORDING:
            return
        self.recorder.stop()
        self._max_timer.stop()
        self.machine.transition(AppState.ABORTED)
        self._update_ui(AppState.ABORTED, "已取消")
        self._settle()

    # ---------------- 主链路 ----------------

    def _begin(self) -> None:
        if not self.machine.transition(AppState.RECORDING):
            return

        # 动作 A3：抓焦点必须早于任何 UI 变化（规矩 R2）
        self.session.reset()
        self.session.state = AppState.RECORDING
        self.session.focus_target = capture_focus_target()
        if self.session.focus_target is None:
            logger.warning("未抓到焦点目标，注入阶段将降级")

        # 动作 A4：开始录音（流已常驻，不丢音头）
        if not self.recorder.start():
            self._fail("麦克风不可用，无法开始录音")
            return

        self._retry = 0
        self._update_ui(AppState.RECORDING, "")
        self._max_timer.start(self.config.max_record_ms)  # 规矩 R9 上限

    def _finish_recording(self) -> None:
        self._max_timer.stop()
        clip = self.recorder.stop()
        if clip is None:
            self._settle()
            return

        # 规矩 R9：时长不足视为误触
        if clip.duration_ms < self.config.min_record_ms:
            logger.info("录音仅 %d ms，视为误触丢弃", clip.duration_ms)
            self.machine.transition(AppState.ABORTED)
            self._update_ui(AppState.ABORTED, "太短了，已丢弃")
            self._settle()
            return

        self.session.audio_clip = clip
        if not self.machine.transition(AppState.TRANSCRIBING):
            return
        self._update_ui(AppState.TRANSCRIBING, "")
        self._run_asr(clip)

    # ---------------- 识别（动作 A6 / A11） ----------------

    def _run_asr(self, clip: AudioClip) -> None:
        self._cleanup_thread()
        self._thread = QThread(self)
        self._worker = _AsrWorker(self.asr, clip)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_transcribed)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    @Slot(object)
    def _on_transcribed(self, transcript: Transcript) -> None:
        self.session.transcript = transcript

        if not transcript.ok:
            if self._retry < ASR_RETRY:  # 规矩 R13：先重试一次
                self._retry += 1
                self.machine.transition(AppState.ERROR)
                self._update_ui(AppState.ERROR, "识别失败，正在重试")
                QTimer.singleShot(300, lambda: self._run_asr(self.session.audio_clip))
                return
            self._fail(transcript.error or "识别失败")
            return

        text = transcript.text
        if self.config.polish_enabled:  # 动作 A7
            text = polish(text, {})
        self._inject(text)

    # ---------------- 注入（动作 A8 / A10） ----------------

    def _inject(self, text: str) -> None:
        if not text.strip():  # 规矩 R8：空结果不上屏
            logger.info("识别结果为空，跳过注入")
            self._update_ui(AppState.DONE, "没听清")
            self._settle()
            return

        if not self.machine.transition(AppState.INJECTING):
            return
        self._update_ui(AppState.INJECTING, "")

        result = self.injector.inject(text, self.session.focus_target)

        if result.ok:
            self.machine.transition(AppState.DONE)
            self._update_ui(AppState.DONE, text)
            logger.info("注入成功：%s", result.strategy)
        elif result.strategy == "clipboard_only":
            # 无有效目标窗口，文本已留在剪贴板，明确告诉用户
            self.machine.transition(AppState.DONE)
            self._update_ui(AppState.DONE, result.message)
        else:
            self._fail(result.message or "注入失败", keep_text=text)

        self._settle()

    # ---------------- 收尾与异常 ----------------

    def _fail(self, message: str, keep_text: str = "") -> None:
        """动作 A11 / R13：失败必须可见，且绝不静默丢掉用户说过的话。"""
        logger.error("会话失败：%s", message)
        self.session.error = message
        self.machine.transition(AppState.ERROR)
        self._update_ui(AppState.ERROR, message)
        if keep_text:
            logger.info("文本已暂存：%s", keep_text)
        self._settle()

    def _settle(self) -> None:
        """收尾：回到 IDLE，准备下一次会话。"""
        QTimer.singleShot(50, self._to_idle)

    def _to_idle(self) -> None:
        self.machine.force_idle()
        self.session.state = AppState.IDLE
        if self.machine.state == AppState.IDLE and self.overlay.isVisible():
            self.overlay.set_state(AppState.IDLE, "")

    def _on_max_duration(self) -> None:
        logger.warning("达到最长录音时长 %d ms，自动停止", self.config.max_record_ms)
        self._finish_recording()

    def _update_ui(self, state: AppState, text: str) -> None:
        self.overlay.set_state(state, text)
        self.tray.set_state(state)
        self.session.state = state

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            try:
                self._thread.quit()
                self._thread.wait(500)
            except Exception:
                pass
            self._thread.deleteLater()
            self._thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def setup_hotkey(self, app, hwnd: int) -> tuple[bool, str]:
        """动作 A1：注册热键。失败原因必须对用户可见（规矩 R11）。"""
        self._app = app
        self._hwnd = int(hwnd)
        manager = HotkeyManager(self.config.hotkey, self.toggle, self.config.hotkey_backend)
        manager.attach_window(hwnd)
        ok, msg = manager.register()
        if ok:
            manager.install_event_filter(app)
            self.hotkey = manager
        return ok, msg

    # ---------------- 设置保存后的热重载 ----------------

    def apply_config(self, config: Config) -> str:
        """设置界面保存后就地生效，不用重启程序。返回给用户看的结果说明。"""
        old = self.config
        notes: list[str] = []

        hotkey_changed = config.hotkey != old.hotkey or config.hotkey_backend != old.hotkey_backend
        device_changed = config.device != old.device
        self.config = config
        self.injector.fallback_uia = config.fallback_uia

        # 热键：先注销旧的再重新注册，注册失败要如实告诉用户（R11）
        if hotkey_changed and self._app is not None and self._hwnd is not None:
            if self.hotkey is not None:
                self.hotkey.unregister()
                self.hotkey = None
            ok, msg = self.setup_hotkey(self._app, self._hwnd)
            notes.append(msg)
            if not ok:
                logger.error("热键重注册失败：%s", msg)

        # 识别客户端：换服务商或换模型都重建
        from .asr import build_asr_client  # noqa: PLC0415

        self.asr = build_asr_client(config)
        notes.append(f"识别服务：{self.asr.name}")

        # 录音设备：换设备要重开流（常驻流是丢音头方案的前提）
        if device_changed:
            was_open = self.recorder.is_open
            self.recorder.close()
            self.recorder = Recorder(device=config.device)
            self.overlay.set_level_provider(self.recorder.level)
            if was_open:
                notes.append("麦克风已就绪" if self.recorder.open() else "麦克风打开失败")

        if not config.overlay_enabled:
            self.overlay.hide()

        logger.info("配置热重载完成：%s", "；".join(notes))
        return "；".join(notes)

    def shutdown(self) -> None:
        """退出前清理：注销热键、停录音、关流。"""
        self._max_timer.stop()
        self._cleanup_thread()
        if self.hotkey is not None:
            self.hotkey.unregister()
        if self.recorder.is_recording:
            self.recorder.stop()
        self.recorder.close()


def build_orchestrator(
    config: Config,
    overlay: Overlay,
    on_quit=None,
    on_settings=None,
    on_logs=None,
) -> Orchestrator:
    """按配置装配出一个可用的编排器。"""
    from .asr import build_asr_client

    asr = build_asr_client(config)
    recorder = Recorder(device=config.device)
    injector = Injector(fallback_uia=config.fallback_uia)
    # 还原剪贴板必须走 Qt 定时器：主线程不能阻塞，否则 Ctrl+V 消息排队到还原之后才处理
    injector.set_scheduler(lambda ms, fn: QTimer.singleShot(ms, fn))
    return Orchestrator(
        config, recorder, asr, injector, overlay,
        on_quit=on_quit, on_settings=on_settings, on_logs=on_logs,
    )
