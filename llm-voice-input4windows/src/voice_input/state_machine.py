"""录音状态机模块。

对应 macOS 原项目 StateMachine.swift，三态线性流转：
- idle: 待机
- recording: 录音中
- transcribing: 识别中

transcribing 状态忽略热键，避免重复触发；force_idle 用于错误/完成复位。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class StateMachine:
    """三态录音状态机，线程安全。"""

    # 状态常量，便于外部比较
    STATE_IDLE = "idle"
    STATE_RECORDING = "recording"
    STATE_TRANSCRIBING = "transcribing"

    def __init__(self) -> None:
        # 当前状态
        self._state = self.STATE_IDLE
        # 状态变更回调
        self.on_state_change: Optional[Callable[[str], None]] = None
        # 状态锁，防止热键与 ASR 回调并发竞态
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """获取当前状态字符串。"""
        return self._state

    def toggle_recording(self) -> bool:
        """切换录音状态：idle↔recording。返回是否成功切换。"""
        with self._lock:
            # 识别中忽略热键，避免打断
            if self._state == self.STATE_TRANSCRIBING:
                return False
            if self._state == self.STATE_IDLE:
                self._set_state(self.STATE_RECORDING)
                return True
            # recording -> transcribing
            self._set_state(self.STATE_TRANSCRIBING)
            return True

    def force_idle(self) -> None:
        """强制复位为 idle，用于错误恢复或识别完成。"""
        with self._lock:
            self._set_state(self.STATE_IDLE)

    def _set_state(self, new_state: str) -> None:
        """实际状态变更并通知回调（调用方需持有锁）。"""
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        DiagLog_shared_write(f"[State] {old} -> {new_state}")
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception as e:
                DiagLog_shared_write(f"[State] on_state_change 异常: {e}")


# 模块内辅助：避免循环导入顶部 import
def DiagLog_shared_write(msg: str) -> None:
    """延迟导入 DiagLog，避免循环依赖。"""
    from .diag_log import DiagLog
    DiagLog.shared().write(msg)
