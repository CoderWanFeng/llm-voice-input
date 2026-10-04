"""本体代码化：零件的值对象、状态机与聚合根 Session。

对应 docs/ONTOLOGY.md 第 1 节（零件）与第 4 节（状态机）。
"""

from __future__ import annotations

import io
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import numpy as np

from .constants import AUDIO_CHANNELS, AUDIO_RATE, AUDIO_WIDTH


# --------------------------------------------------------------------------
# 值对象
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AudioClip:
    """一次录音的产物，封口后不可变（规矩 R6）。"""

    pcm: bytes
    rate: int = AUDIO_RATE
    channels: int = AUDIO_CHANNELS
    width: int = AUDIO_WIDTH
    has_preroll: bool = False

    @property
    def duration_ms(self) -> int:
        """按 PCM 字节数推算时长。"""
        bytes_per_ms = self.rate * self.channels * self.width / 1000.0
        if bytes_per_ms <= 0:
            return 0
        return int(len(self.pcm) / bytes_per_ms)

    def to_wav_bytes(self) -> bytes:
        """封装成 wav（16k 单声道，1 分钟约 1.9MB，可直接上传）。"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(self.width)
            w.setframerate(self.rate)
            w.writeframes(self.pcm)
        return buf.getvalue()


@dataclass(frozen=True)
class FocusTarget:
    """按下快捷键那一刻的"上屏目的地"快照（规矩 R2：焦点先抓后用）。"""

    hwnd: int
    thread_id: int = 0
    pid: int = 0
    process_name: str = ""
    integrity_level: Optional[str] = None
    captured_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Transcript:
    """转写结果。"""

    text: str = ""
    confidence: float = 0.0
    duration_ms: int = 0
    is_final: bool = True
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """是否可用（无错误且非空）——规矩 R8 依据此判断要不要上屏。"""
        return self.error is None and bool(self.text.strip())


# --------------------------------------------------------------------------
# 状态机
# --------------------------------------------------------------------------

class AppState(str, Enum):
    """Session 的生命周期状态。"""

    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    INJECTING = "injecting"
    DONE = "done"
    ABORTED = "aborted"
    ERROR = "error"


# 规矩 R3：只能走转移表，禁止跨态跳转。
# 任意状态都允许回到 IDLE（用户主动取消 / 收尾清理）。
TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.IDLE: {AppState.RECORDING},
    AppState.RECORDING: {AppState.TRANSCRIBING, AppState.ABORTED, AppState.IDLE},
    AppState.TRANSCRIBING: {AppState.INJECTING, AppState.ERROR, AppState.IDLE},
    AppState.ERROR: {AppState.TRANSCRIBING, AppState.IDLE},
    AppState.INJECTING: {AppState.DONE, AppState.IDLE},
    AppState.DONE: {AppState.IDLE},
    AppState.ABORTED: {AppState.IDLE},
}


class StateMachine:
    """线程安全的状态机，越权转移直接拒绝并返回 False。"""

    def __init__(self, initial: AppState = AppState.IDLE) -> None:
        self._lock = threading.RLock()
        self._state = initial
        self._listeners: list[Callable[[AppState, AppState], None]] = []

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    def on_change(self, cb: Callable[[AppState, AppState], None]) -> None:
        with self._lock:
            self._listeners.append(cb)

    def can(self, to: AppState) -> bool:
        with self._lock:
            return to in TRANSITIONS.get(self._state, set())

    def transition(self, to: AppState) -> bool:
        """尝试转移，成功返回 True。回调在锁外执行，避免死锁。"""
        with self._lock:
            if to not in TRANSITIONS.get(self._state, set()):
                return False
            old, self._state = self._state, to
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(old, to)
            except Exception:  # 监听者异常不得影响状态机
                pass
        return True

    def force_idle(self) -> bool:
        """收尾用：无论当前处于什么状态都回到 IDLE。"""
        with self._lock:
            if self._state == AppState.IDLE:
                return True
            old, self._state = self._state, AppState.IDLE
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(old, AppState.IDLE)
            except Exception:
                pass
        return True


# --------------------------------------------------------------------------
# 聚合根
# --------------------------------------------------------------------------

@dataclass
class Session:
    """一次 toggle 的完整生命周期，可整体回滚（规矩 R1：同时只允许一个）。"""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: AppState = AppState.IDLE
    focus_target: Optional[FocusTarget] = None
    audio_clip: Optional[AudioClip] = None
    transcript: Optional[Transcript] = None
    started_at: float = field(default_factory=time.time)
    error: Optional[str] = None

    def reset(self) -> None:
        """回到初始态，准备下一次会话。"""
        self.session_id = uuid.uuid4().hex[:12]
        self.state = AppState.IDLE
        self.focus_target = None
        self.audio_clip = None
        self.transcript = None
        self.started_at = time.time()
        self.error = None


def rms_level(pcm: bytes) -> float:
    """计算 PCM 音量（0~1），供浮窗波形使用。"""
    if not pcm:
        return 0.0
    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(arr * arr)))
