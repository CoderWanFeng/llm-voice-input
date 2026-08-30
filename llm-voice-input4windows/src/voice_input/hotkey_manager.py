"""全局热键监听模块。

对应 macOS 原项目 HotkeyManager.swift：
- 默认热键 Ctrl + Alt + K（Windows 上 Alt 对应 macOS Option；加 Ctrl 避免与系统单 Alt 冲突）
- pynput.keyboard.GlobalHotKeys 注册
- 0.3 秒防抖避免连按

注意：pynput 的 listener 自带后台线程，无需手动管理。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .diag_log import DiagLog

try:
    from pynput import keyboard as _kb
    _PYNPUT_AVAILABLE = True
except Exception as e:  # pynput 未安装或不可用
    _PYNPUT_AVAILABLE = False
    _PYNPUT_IMPORT_ERROR = str(e)


# 默认热键组合字符串（pynput 格式）
DEFAULT_HOTKEY = "<ctrl>+<alt>+k"
# 防抖间隔（秒）
_DEBOUNCE_INTERVAL = 0.3


class HotkeyManager:
    """全局热键管理器，触发回调通知主控。"""

    def __init__(self) -> None:
        # 热键触发回调（无参）
        self.on_triggered: Optional[Callable[[], None]] = None
        # 当前热键字符串
        self._hotkey_str = DEFAULT_HOTKEY
        # pynput listener 实例
        self._listener = None
        # 防抖：上次触发时间
        self._last_trigger_time = 0.0
        # 防抖锁
        self._debounce_lock = threading.Lock()

    def start(self) -> bool:
        """启动全局热键监听，返回是否成功。"""
        if not _PYNPUT_AVAILABLE:
            DiagLog.shared().write(f"[Hotkey] pynput 不可用: {_PYNPUT_IMPORT_ERROR}")
            return False
        try:
            self._listener = _kb.GlobalHotKeys({
                self._hotkey_str: self._handle_trigger,
            })
            self._listener.start()
            DiagLog.shared().write(f"[Hotkey] 监听已启动: {self._hotkey_str}")
            return True
        except Exception as e:
            DiagLog.shared().write(f"[Hotkey] 启动失败: {e}")
            return False

    def stop(self) -> None:
        """停止热键监听。"""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
            DiagLog.shared().write("[Hotkey] 监听已停止")

    def _handle_trigger(self) -> None:
        """热键触发回调（在 pynput 后台线程）：防抖并通知主控。"""
        now = time.time()
        with self._debounce_lock:
            # 0.3 秒内重复触发忽略
            if now - self._last_trigger_time < _DEBOUNCE_INTERVAL:
                return
            self._last_trigger_time = now
        DiagLog.shared().write("[Hotkey] 触发")
        if self.on_triggered:
            try:
                self.on_triggered()
            except Exception as e:
                DiagLog.shared().write(f"[Hotkey] on_triggered 异常: {e}")
